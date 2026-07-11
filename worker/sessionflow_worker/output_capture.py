"""Captura de output das sessões (DASH-02).

Captura o stdout/stderr de uma sessão tmux via ``pipe-pane``, classifica cada
linha por heurística, persiste no MongoDB (coleção ``session_output``) com um
``seq`` incremental por sessão, aplica um *ring buffer* (limite de N linhas por
sessão) e publica cada linha nova como evento ``output`` no exchange RabbitMQ.

Decisões de design
------------------
- **pipe-pane**: ``tmux pipe-pane -o -t <name> 'cat >> <tmpfile>'`` redireciona
  uma cópia (``-o`` = output-only, sem entrada) do pane para um arquivo
  temporário. Lemos o arquivo por *offset* de bytes (``poll_new_lines`` guarda
  quanto já leu), o que dá um diff incremental simples e robusto sem depender de
  ``capture-pane`` (que reflete só a tela visível e não preserva histórico já
  rolado). Usamos ``subprocess`` direto para o ``pipe-pane`` porque o libtmux
  não expõe esse comando de forma estável.
- **Classificação por heurística** (``classify_line``): regras textuais simples,
  PROVISÓRIAS, que dependem do formato de saída de cada CLI de agente e precisam
  de **validação empírica por CLI** (claude/codex/gemini/opencode divergem nos
  prefixos/ornamentos). Documentadas em ``classify_line``/``detect_waiting``.
- **Ring buffer**: após persistir as novas linhas, removemos do Mongo as linhas
  mais antigas além de ``max_lines`` por sessão (cap), mantendo a coleção
  limitada por sessão sem perder o ``seq`` monotônico.
- **Coleção injetável**: ``collection`` é parametrizável p/ testes isolados no
  mesmo DB ``sessionflow``.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

from sessionflow_worker.agent_launcher import AgentType
from sessionflow_worker.rabbit import publish
from sessionflow_worker.tmux_runtime import TmuxRuntime

DEFAULT_COLLECTION = "session_output"
DEFAULT_SCREEN_COLLECTION = "session_screen"
DEFAULT_MAX_LINES = 2000
# Mesma routing key dos eventos para chegar ao consumer SSE da API
# (fila `sessionflow.sse` bindada a `sessionflow.events`). O frontend distingue
# linha de output (tem `seq`/`line_type`) de evento de ciclo de vida.
OUTPUT_ROUTING_KEY = "sessionflow.events"

# Tipos de linha possíveis (classificação heurística).
LINE_CMD = "cmd"
LINE_SYS = "sys"
LINE_AGENT = "agent"
LINE_TOOL = "tool"
LINE_OUT = "out"
LINE_ASK = "ask"

# Marcadores que sugerem que o agente está pedindo uma decisão ao humano.
# PROVISÓRIO: precisa de validação empírica por CLI.
_ASK_MARKERS = (
    "(s/n)",
    "(y/n)",
    "(sim/não)",
    "[y/n]",
    "[s/n]",
    "deseja",
    "aplico",
    "confirma",
    "posso prosseguir",
    "do you want",
    "should i",
    "proceed?",
)

# Prefixos que indicam um comando digitado / prompt de shell.
_CMD_PREFIXES = ("$", "›", "❯", ">", "#")

# Prefixos/ornamentos que indicam mensagem do sistema / agente.
_SYS_PREFIXES = ("[", "==", "--", "warning", "error", "info")

# --- limpeza de escapes ANSI/terminal -------------------------------------
# O output capturado (tanto pelo snapshot via ``capture-pane`` quanto pelo
# ``pipe-pane``) contém sequências de escape: códigos CSI (cores, movimento de
# cursor), OSC (títulos), e bracketed-paste (``\x1b[?2004h``/``\x1b[?2004l``).
# Esses bytes não devem ser persistidos como texto. As regras abaixo cobrem:
#   - CSI:  ESC [ ... <byte final em @-~>  (inclui o ``?2004h/l``)
#   - OSC:  ESC ] ... (terminado por BEL ou ST ``ESC \``)
#   - escapes de 1/2 chars (ESC seguido de um byte simples).
_ANSI_CSI = r"\x1b\[[0-?]*[ -/]*[@-~]"
_ANSI_OSC = r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
_ANSI_SIMPLE = r"\x1b[@-Z\\-_]"
_ANSI_RE = re.compile(f"{_ANSI_CSI}|{_ANSI_OSC}|{_ANSI_SIMPLE}")

# Caracteres de controle C0 que sobram DEPOIS do strip ANSI e poluem o texto
# persistido (cursor/largura-zero). Removemos todos os C0 (0x00–0x1f) e o DEL
# (0x7f) EXCETO ``\t`` (0x09) e ``\n`` (0x0a), que carregam layout legível.
# ``\r`` (0x0d) e ``\b`` (0x08) são tratados antes (ver ``strip_ansi``).
_C0_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _resolve_backspaces(text: str) -> str:
    """Resolve ``\\b`` (0x08): cada backspace apaga o caractere anterior.

    Ex.: ``'e\\becho'`` -> ``'echo'``. Backspaces no início (sem char anterior)
    são simplesmente descartados.
    """
    if "\b" not in text:
        return text
    out: list[str] = []
    for ch in text:
        if ch == "\b":
            if out:
                out.pop()
        else:
            out.append(ch)
    return "".join(out)


def strip_ansi(text: str) -> str:
    """Remove escapes ANSI/terminal e caracteres de controle de ``text``.

    Cobre, em ordem:
      1. Códigos CSI (cores, cursor, bracketed-paste ``\\x1b[?2004h/l``),
         OSC (títulos de janela) e escapes simples de 1–2 bytes.
      2. ``\\r`` (carriage return): removido — as linhas já vêm separadas.
      3. ``\\b`` (backspace, 0x08): resolvido apagando o char anterior, de modo
         que ``'e\\becho'`` vira ``'echo'`` (corrige o "eecho" no output).
      4. Demais caracteres de controle C0/DEL não imprimíveis (exceto ``\\t``
         e ``\\n``): removidos.

    Função pura; mantém apenas texto legível.
    """
    text = _ANSI_RE.sub("", text)
    text = text.replace("\r", "")
    text = _resolve_backspaces(text)
    return _C0_CONTROL_RE.sub("", text)


# Sequências SGR (cor/atributo): ``ESC [ <params> m``. Preservadas no espelho
# da tela (para o frontend colorir) — todo o resto de escape/controle é removido.
_SGR_RE = re.compile(r"\x1b\[[0-9;]*m")

# Hyperlinks OSC 8: ``ESC ] 8 ; params ; URI ST`` (abre) e ``ESC ] 8 ; ; ST``
# (fecha), com ST = BEL (\x07) ou ``ESC \``. Também PRESERVADOS para o frontend
# transformar em ``<a>`` clicável (ex.: o "ressalvas-preview" do Claude Code, que
# de outra forma perderia a URL). ``[^\x1b\x07]*`` cobre ``params;URI``.
_OSC8_RE = re.compile(r"\x1b\]8;[^\x1b\x07]*(?:\x07|\x1b\\)")

# Combinado: o que queremos MANTER no espelho (cor + hyperlink).
_KEEP_RE = re.compile(f"{_SGR_RE.pattern}|{_OSC8_RE.pattern}")

# URL de artifact do claude.ai vista na tela (persistida por sessão p/ o botão
# "abrir artifact" do app — o rodapé "⧉ <nome>" do Claude Code não expõe a URL).
_ARTIFACT_URL_RE = re.compile(r"https://claude\.ai/code/artifact/[0-9a-f-]+", re.IGNORECASE)
# Coleção dos docs de sessão (onde o last_artifact_url é gravado).
SESSIONS_COLLECTION_NAME = "sessions"


def clean_screen_keep_color(text: str) -> str:
    """Como ``strip_ansi``, mas mantém cor/atributo (SGR) e hyperlinks (OSC 8).

    Usado no espelho da tela (``capture-pane -e``) para reproduzir as cores do
    terminal real e manter os links clicáveis. Protege SGR/OSC8 com sentinelas,
    aplica a limpeza normal (cursor, OSC de título, bracketed-paste, C0) e os
    restaura ao final.
    """
    protected: list[str] = []
    sent_open = chr(0xE000)
    sent_close = chr(0xE001)

    def _protect(m: "re.Match[str]") -> str:
        protected.append(m.group(0))
        # Sentinela em área de uso privado do Unicode — sobrevive ao strip_ansi.
        return f"{len(protected) - 1}"

    text = _KEEP_RE.sub(_protect, text)
    text = strip_ansi(text)
    return re.sub(
        r"(\d+)", lambda m: protected[int(m.group(1))], text
    )


@dataclass(frozen=True, slots=True)
class OutputLine:
    """Uma linha de output capturada e classificada de uma sessão."""

    tmux_name: str
    seq: int
    text: str
    line_type: str
    at: datetime


def classify_line(text: str) -> str:
    """Classifica uma linha de output por heurística textual (função pura).

    Retorna um dentre ``cmd|sys|agent|tool|out|ask``. As regras são
    PROVISÓRIAS e dependem do formato de cada CLI — precisam de validação
    empírica por agente.

    Regras (avaliadas nesta ordem de precedência):
        1. ``ask``  — a linha sugere pedido de decisão (ver ``_ASK_MARKERS``)
           ou termina com ``?`` (ver ``detect_waiting``-like). Tem prioridade
           para não ser ofuscada por outro prefixo.
        2. ``tool`` — começa com o ornamento de chamada de ferramenta ``⎿``
           (usado por CLIs estilo Claude Code para output de tool).
        3. ``cmd``  — começa com um prompt de shell / comando digitado
           (``$``, ``›``, ``❯``, ``>``, ``#``).
        4. ``sys``  — começa com marcadores de sistema/log (``[`` , ``==``,
           ``--``) ou palavras-chave ``warning``/``error``/``info``.
        5. ``agent``— linha começando com ``●`` / ``•`` / ``*`` (bullet de fala
           do agente) — saída textual atribuída ao agente.
        6. ``out``  — default: qualquer outra saída de programa.

    Linha vazia/whitespace é classificada como ``out``.
    """
    stripped = text.strip()
    if not stripped:
        return LINE_OUT

    lowered = stripped.lower()

    # 1. ask — pergunta / pedido de decisão.
    if stripped.endswith("?") or any(m in lowered for m in _ASK_MARKERS):
        return LINE_ASK

    # 2. tool — ornamento de output de ferramenta.
    if stripped.startswith("⎿"):
        return LINE_TOOL

    # 3. cmd — prompt de shell / comando.
    if stripped[0] in _CMD_PREFIXES:
        return LINE_CMD

    # 4. sys — log / sistema.
    if any(lowered.startswith(p) for p in _SYS_PREFIXES):
        return LINE_SYS

    # 5. agent — bullet de fala do agente.
    if stripped[0] in ("●", "•", "*"):
        return LINE_AGENT

    # 6. default.
    return LINE_OUT


# Pistas FORTES (na TELA) de que o agente espera uma DECISÃO específica do
# usuário — não o mero prompt pronto. Evitamos sinais sempre presentes (ex.: o
# cursor "❯" do Claude ocioso) p/ não gerar falso-positivo a cada sessão.
# Cobrem footer de picker e confirmações y/n. PROVISÓRIO: validar por CLI.
_ATTENTION_SCREEN_MARKERS = (
    "to select",       # picker: "Enter to select"
    "to navigate",     # picker: "↑/↓ to navigate"
    "esc to cancel",   # footer de diálogo/picker
    "(y/n)",
    "(s/n)",
    "[y/n]",
    "[s/n]",
    "(yes/no)",
    "(sim/não)",
)


# Frases COMPLETAS (PT + EN) que indicam que o agente está AGUARDANDO UMA
# DECISÃO do usuário. Diferente do "❯" ocioso ou de um "?" solto na prosa,
# essas frases são longas o bastante para não casarem com texto qualquer, e
# costumam aparecer ALGUMAS linhas acima do prompt atual (não só nas 3
# últimas) — por isso são checadas numa janela maior (últimas ~10 linhas).
# Match case-insensitive sobre o texto já sem ANSI. Conservador por design.
_AWAITING_DECISION_MARKERS = frozenset(
    {
        # PT
        "aguardando sua decisão",
        "aguardando decisão",
        "aguardo sua decisão",
        "aguardo sua resposta",
        "posso prosseguir",
        "deseja que eu",
        "quer que eu",
        "qual você prefere",
        # EN
        "awaiting your decision",
        "awaiting your input",
        "awaiting your",
        "i'll hold",
        "i will hold here",
        "waiting for your",
        "let me know how",
        "should i proceed",
        "do you want me to",
        "how would you like",
    }
)


def screen_wants_attention(text: str, agent_type: AgentType) -> bool:
    """A TELA VISÍVEL sugere que o agente espera uma DECISÃO do usuário?

    Sinais fortes: footer de picker / confirmação y-n em qualquer lugar da
    cauda, OU uma pergunta (``detect_waiting``) nas ÚLTIMAS 3 linhas (a área do
    prompt atual — restrito p/ não casar "?" no meio da prosa do agente). O
    texto pode trazer cor (capture ``-e``), então removemos o ANSI antes.
    Heurística PROVISÓRIA por CLI.
    """
    if not text:
        return False
    non_empty = [strip_ansi(ln) for ln in text.splitlines() if strip_ansi(ln).strip()]
    if not non_empty:
        return False
    # Footer/confirmação: procura nas últimas ~8 linhas.
    blob = "\n".join(non_empty[-8:]).lower()
    if any(m in blob for m in _ATTENTION_SCREEN_MARKERS):
        return True
    # "Aguardando decisão": frases completas (PT/EN) podem aparecer algumas
    # linhas acima do prompt — checa numa janela maior (últimas ~10 linhas).
    blob10 = "\n".join(non_empty[-10:]).lower()
    if any(m in blob10 for m in _AWAITING_DECISION_MARKERS):
        return True
    # Pergunta direta: só nas 3 últimas linhas (prompt atual).
    return any(detect_waiting(ln, agent_type) for ln in non_empty[-3:])


# Prefixos de spinner / "pensando" (primeiro char não-vazio de uma linha de
# trabalho do agente, ex.: "✻ Mustering… (48s · ↓ 1.5k tokens)").
_THINKING_PREFIXES = ("✻", "✶", "✽", "·", "◯", "◆", "*")

# Pista FRACA de atividade de "pensando" embutida na linha (timer/tokens), ex.:
# "… (12s" ou "tokens)". Regex p/ o padrão de timer "(<n>s".
_TIMER_RE = re.compile(r"\(\s*\d+\s*s\b")


def derive_activity(
    text: str, agent_type: AgentType, attention: str | None
) -> str:
    """Rótulo amigável (PT-BR) do que o agente está fazendo (função pura).

    Heurística *best-effort* sobre a TELA já capturada. Trabalha sobre as
    linhas não-vazias (sem ANSI) da CAUDA (últimas ~12 linhas, a área viva) e
    mapeia o sinal mais recente para um rótulo curto. ``attention`` tem
    precedência (vem do upstream ``screen_wants_attention``/idle).

    Ordem:
      1. ``attention == "waiting"`` -> "Aguardando você"
      2. ``attention == "idle"``    -> "Concluído"
      3. Varre a cauda de BAIXO p/ CIMA e usa o 1º sinal que casar:
         - shell/comando  -> "Rodando comando"
         - edição         -> "Codificando"
         - leitura/análise-> "Analisando"
         - pensando       -> "Pensando"
      4. default -> "Executando"

    ``agent_type`` é reservado p/ refinamento futuro por CLI (hoje não diferencia).
    """
    _ = agent_type
    if attention == "waiting":
        return "Aguardando sua decisão"
    if attention == "idle":
        return "Concluído"

    non_empty = [
        s for s in (strip_ansi(ln) for ln in text.splitlines()) if s.strip()
    ]
    if not non_empty:
        return "Executando"

    for line in reversed(non_empty[-12:]):
        stripped = line.strip()
        lowered = stripped.lower()

        # shell/comando.
        if (
            "bash(" in lowered
            or "shell command" in lowered
            or "running…" in lowered
            or stripped.startswith("$ ")
        ):
            return "Rodando comando"

        # edição/escrita.
        if any(
            m in lowered
            for m in (
                "edit(",
                "write(",
                "update(",
                "edited",
                "updated",
                "create(",
            )
        ):
            return "Codificando"

        # leitura/análise.
        if any(
            m in lowered
            for m in (
                "read(",
                "grep(",
                "glob(",
                "search",
                "explore",
                "reading",
                "web",
            )
        ):
            return "Analisando"

        # pensando / spinner de trabalho.
        first = stripped[0]
        rest = stripped[1:].lstrip()
        if (first in _THINKING_PREFIXES and rest[:1].isalpha()) or (
            "tokens)" in lowered or _TIMER_RE.search(stripped)
        ):
            return "Pensando"

    return "Executando"


def detect_waiting(text: str, agent_type: AgentType) -> bool:
    """True se a linha sugere que o agente pede uma decisão (função pura).

    Heurística PROVISÓRIA: ``True`` quando a linha termina com ``?`` ou contém
    um dos marcadores de decisão (``(s/n)``/``(y/n)``/"Deseja"/"Aplico"/...).

    ⚠️ Esta heurística é um *placeholder* e PRECISA de validação empírica por
    CLI: cada agente (claude/codex/gemini/opencode) formata seus prompts de
    confirmação de modo diferente, então o ``agent_type`` é recebido para
    permitir, no futuro, regras específicas por CLI (hoje não diferencia).
    """
    # ``agent_type`` é reservado para refinamento futuro por CLI.
    _ = agent_type
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.endswith("?"):
        return True
    lowered = stripped.lower()
    return any(marker in lowered for marker in _ASK_MARKERS)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OutputCapture:
    """Captura, classifica, persiste e publica o output de sessões tmux.

    Parameters
    ----------
    runtime:
        Runtime tmux (usado p/ o ``server`` libtmux e validação de sessão).
    db:
        Database ``motor`` onde persistir o output.
    channel:
        Canal aio-pika já com a topologia declarada, p/ publicar eventos
        ``output``. Opcional: se ``None``, não publica (apenas persiste).
    collection:
        Nome da coleção de output (injetável p/ testes isolados).
    max_lines:
        Tamanho do ring buffer por sessão (linhas mais antigas são removidas).
    """

    def __init__(
        self,
        runtime: TmuxRuntime,
        db: AsyncIOMotorDatabase,
        channel=None,
        collection: str = DEFAULT_COLLECTION,
        max_lines: int = DEFAULT_MAX_LINES,
    ) -> None:
        self._runtime = runtime
        self._db = db
        self._channel = channel
        self._collection = collection
        self._max_lines = max_lines
        # Estado por sessão: arquivo do pipe-pane e offset de bytes já lido.
        self._pipe_files: dict[str, str] = {}
        self._offsets: dict[str, int] = {}
        # Próximo seq por sessão (carregado do Mongo no primeiro poll se preciso).
        self._next_seq: dict[str, int] = {}
        # Snapshot inicial pendente (linhas já capturadas do pane, ainda não
        # persistidas) — drenado no primeiro ``poll_new_lines`` da sessão.
        self._pending_snapshot: dict[str, list[str]] = {}
        # Hash do último espelho publicado por sessão (dedupe do push SSE — só
        # empurra quando a tela MUDA, em vez de a cada ciclo de captura).
        self._screen_hash: dict[str, int] = {}
        # Último artifact URL persistido por sessão (dedupe do update no Mongo).
        self._last_artifact: dict[str, str] = {}

    @property
    def collection(self) -> str:
        return self._collection

    def _coll(self):
        return self._db[self._collection]

    # -- captura ----------------------------------------------------------

    def _tmux_base_cmd(self) -> list[str]:
        """Comando base ``tmux [-L socket]`` do mesmo server do libtmux."""
        socket_name = getattr(self._runtime.server, "socket_name", None)
        cmd = ["tmux"]
        if socket_name:
            cmd += ["-L", socket_name]
        return cmd

    def _capture_pane_snapshot(self, tmux_name: str) -> list[str]:
        """Captura o conteúdo atual do pane (``capture-pane -p``) como linhas.

        Aplica ``strip_ansi`` em cada linha e remove as linhas em branco do
        fim (que o ``capture-pane`` produz para preencher a altura do pane).
        Retorna ``[]`` se o comando falhar (sessão inexistente, etc.).
        """
        cmd = self._tmux_base_cmd() + ["capture-pane", "-p", "-t", tmux_name]
        try:
            proc = subprocess.run(cmd, check=True, capture_output=True)
        except (OSError, subprocess.SubprocessError):
            return []
        text = proc.stdout.decode("utf-8", errors="replace")
        lines = [strip_ansi(raw.rstrip("\r")) for raw in text.split("\n")]
        # Remove o "" final (após o \n) e as linhas em branco do rodapé.
        while lines and not lines[-1].strip():
            lines.pop()
        return lines

    def capture_screen(self, tmux_name: str) -> str:
        """Captura a TELA VISÍVEL atual do pane como texto (espelho ao vivo).

        Roda ``tmux [-L socket] capture-pane -p -t <name>`` (apenas a tela
        visível atual, NÃO o histórico rolado), aplica ``strip_ansi`` por
        linha e rejunta com ``\\n``. Diferente do ``pipe-pane``/output, isto é
        idempotente: reflete o que o agente TUI mostra AGORA e SUBSTITUI o
        anterior (não acumula banner/redesenho).

        Retorna ``""`` se a sessão não existir ou o comando falhar.
        """
        # ``-e`` preserva os escapes SGR (cor/atributo) para o espelho colorir
        # a tela como no terminal real; mantemos só os SGR (clean_screen_keep_color).
        cmd = self._tmux_base_cmd() + ["capture-pane", "-e", "-p", "-t", tmux_name]
        try:
            proc = subprocess.run(cmd, check=True, capture_output=True)
        except (OSError, subprocess.SubprocessError):
            return ""
        text = proc.stdout.decode("utf-8", errors="replace")
        lines = [clean_screen_keep_color(raw.rstrip("\r")) for raw in text.split("\n")]
        # Remove o "" final (após o \n) e as linhas em branco do rodapé que o
        # capture-pane produz p/ preencher a altura do pane. ``strip_ansi`` no
        # teste de vazio para ignorar linhas que só têm códigos de cor.
        while lines and not strip_ansi(lines[-1]).strip():
            lines.pop()
        return "\n".join(lines)

    def capture_scrollback(self, tmux_name: str, lines: int = 2000) -> str:
        """Captura o HISTÓRICO rolado do pane (scrollback) como texto colorido.

        Diferente de :meth:`capture_screen` (só a tela visível atual), roda
        ``tmux [-L socket] capture-pane -e -p -S -<lines> -t <name>``, que
        inclui as últimas ``lines`` linhas do scrollback ALÉM da tela visível.
        Limpo do MESMO jeito que o espelho (``clean_screen_keep_color``, mantém
        SGR de cor) para o frontend renderizar igual. É mais caro que a tela
        visível, mas é lido SOB DEMANDA via HTTP (não empurrado por SSE) — só
        vive no doc do Mongo para o modo "histórico".

        Retorna ``""`` se a sessão não existir ou o comando falhar.
        """
        cmd = self._tmux_base_cmd() + [
            "capture-pane",
            "-e",
            "-p",
            "-S",
            f"-{lines}",
            "-t",
            tmux_name,
        ]
        try:
            proc = subprocess.run(cmd, check=True, capture_output=True)
        except (OSError, subprocess.SubprocessError):
            return ""
        text = proc.stdout.decode("utf-8", errors="replace")
        out = [clean_screen_keep_color(raw.rstrip("\r")) for raw in text.split("\n")]
        # Remove o "" final (após o \n) e as linhas em branco do rodapé.
        while out and not strip_ansi(out[-1]).strip():
            out.pop()
        return "\n".join(out)

    async def snapshot_screen(
        self, tmux_name: str, collection: str = DEFAULT_SCREEN_COLLECTION
    ) -> str:
        """Captura a tela visível e faz **upsert** de 1 doc por sessão.

        Persiste ``{tmux_name, text, scrollback, at}`` na coleção ``collection``
        (default ``session_screen``), substituindo o ``text`` a cada chamada
        (espelho ao vivo). O ``scrollback`` (histórico mais profundo) é
        capturado no MESMO ciclo (captura extra barata) e guardado no doc para
        leitura SOB DEMANDA via HTTP — NÃO é empurrado por SSE/RabbitMQ (só o
        ``text`` da tela visível continua sendo empurrado). Retorna o texto
        capturado (tela visível).
        """
        text = self.capture_screen(tmux_name)
        now = _now()
        # NÃO capturamos o scrollback (2000 linhas coloridas) a CADA ciclo: era um
        # capture-pane pesado, síncrono, por sessão — atrasava o ciclo ao vivo (e o
        # push SSE junto), deixando o espelho lento. O scrollback é útil só sob
        # demanda (modo Histórico) e é inútil em TUIs alt-screen (o tmux não guarda
        # histórico deles). Guardamos ``scrollback = text`` (barato); o Histórico
        # cai no texto visível, que é o que já valia p/ esses agentes.
        await self._db[collection].update_one(
            {"tmux_name": tmux_name},
            {
                "$set": {
                    "tmux_name": tmux_name,
                    "text": text,
                    "scrollback": text,
                    "at": now,
                }
            },
            upsert=True,
        )
        # Push SSE do espelho: empurra a tela assim que captura (em vez do front
        # pollar a cada ~1,2s), só quando MUDOU — feedback quase imediato.
        await self._publish_screen(tmux_name, text, now)
        # Último ARTIFACT visto: o rodapé "⧉ <nome>" do Claude Code não expõe a
        # URL (o clique é tratado pelo TUI); persistimos a última URL de artifact
        # que passou pela tela p/ o app oferecer o botão "abrir artifact" mesmo
        # depois que a linha rolou pra fora. Best-effort, só quando muda.
        try:
            m = _ARTIFACT_URL_RE.findall(text)
            if m and self._last_artifact.get(tmux_name) != m[-1]:
                self._last_artifact[tmux_name] = m[-1]
                await self._db[SESSIONS_COLLECTION_NAME].update_one(
                    {"tmux_name": tmux_name},
                    {"$set": {"last_artifact_url": m[-1]}},
                )
        except Exception:  # noqa: BLE001 - nunca derruba o snapshot
            pass
        return text

    async def _publish_screen(self, tmux_name: str, text: str, at: datetime) -> None:
        """Publica o espelho via SSE quando mudou (dedupe por hash)."""
        if self._channel is None:
            return
        h = hash(text)
        if self._screen_hash.get(tmux_name) == h:
            return
        self._screen_hash[tmux_name] = h
        payload = {
            "event": OUTPUT_ROUTING_KEY,
            "kind": "screen",
            "tmux_name": tmux_name,
            "session_id": tmux_name,
            "text": text,
            "at": at.isoformat(),
        }
        await publish(self._channel, OUTPUT_ROUTING_KEY, payload)

    def start_capture(self, tmux_name: str) -> str:
        """Captura o snapshot atual do pane e inicia o pipe-pane da sessão.

        Antes de iniciar o ``pipe-pane`` (que só captura output NOVO), tira um
        snapshot do conteúdo atual do pane via ``capture-pane -p`` e o guarda
        como output inicial pendente — assim sessões já rodando e ociosas não
        aparecem vazias até emitirem algo. O snapshot é persistido/publicado
        no primeiro ``poll_new_lines``.

        Cria um arquivo temporário e registra ``tmux pipe-pane -o``
        redirecionando o output do pane para esse arquivo. Retorna o caminho.

        Idempotente: re-chamar para uma sessão já iniciada é no-op (não
        re-tira snapshot nem reinicia o pipe).
        """
        if tmux_name in self._pipe_files:
            return self._pipe_files[tmux_name]

        # Snapshot ANTES do pipe (capture-pane reflete a tela atual).
        snapshot = self._capture_pane_snapshot(tmux_name)
        if snapshot:
            self._pending_snapshot[tmux_name] = snapshot

        fd, path = tempfile.mkstemp(prefix=f"sfcapture-{tmux_name}-", suffix=".log")
        os.close(fd)
        self._pipe_files[tmux_name] = path
        self._offsets.setdefault(tmux_name, 0)

        # ``-o`` = só output (não captura input); o comando do shell faz o cat
        # append para o arquivo. Disparamos via tmux CLI do mesmo socket do
        # server libtmux para garantir consistência.
        cmd = self._tmux_base_cmd() + [
            "pipe-pane",
            "-o",
            "-t",
            tmux_name,
            f"cat >> {path!r}",
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return path

    async def poll_new_lines(self, tmux_name: str) -> list[OutputLine]:
        """Lê linhas novas do arquivo de captura, persiste e publica.

        Faz o diff por offset de bytes em relação à última leitura, classifica
        cada linha nova, persiste em lote no Mongo (com ``seq`` incremental),
        aplica o ring buffer e publica cada linha como evento ``output``.
        Retorna a lista de :class:`OutputLine` novas (ordenadas por ``seq``).
        """
        path = self._pipe_files.get(tmux_name)
        if path is None or not os.path.exists(path):
            return []

        # Drena o snapshot inicial (capturado em ``start_capture``) ANTES das
        # linhas novas do pipe — garante que o estado atual da sessão venha
        # primeiro, com seq crescente.
        snapshot = self._pending_snapshot.pop(tmux_name, [])

        offset = self._offsets.get(tmux_name, 0)
        with open(path, "rb") as fh:
            fh.seek(offset)
            chunk = fh.read()

        raw_lines: list[str] = []
        if chunk:
            # Só consumimos até o último '\n' completo; bytes parciais ficam
            # p/ o próximo poll (avançamos o offset só pelo processado).
            last_nl = chunk.rfind(b"\n")
            if last_nl != -1:
                consumed = chunk[: last_nl + 1]
                self._offsets[tmux_name] = offset + len(consumed)
                text = consumed.decode("utf-8", errors="replace")
                # último elemento é "" após o \n final
                raw_lines = text.split("\n")[:-1]

        # ANSI strip em snapshot e pipe; descarta vazios resultantes do strip.
        cleaned = [strip_ansi(raw.rstrip("\r")) for raw in (*snapshot, *raw_lines)]
        cleaned = [line for line in cleaned if line.strip()]
        if not cleaned:
            return []

        seq = await self._current_seq(tmux_name)
        now = _now()
        out_lines: list[OutputLine] = []
        for line in cleaned:
            out_lines.append(
                OutputLine(
                    tmux_name=tmux_name,
                    seq=seq,
                    text=line,
                    line_type=classify_line(line),
                    at=now,
                )
            )
            seq += 1
        self._next_seq[tmux_name] = seq

        await self._persist(tmux_name, out_lines)
        await self._enforce_ring_buffer(tmux_name)
        await self._publish(out_lines)
        return out_lines

    # -- persistência -----------------------------------------------------

    async def _current_seq(self, tmux_name: str) -> int:
        """Próximo ``seq`` para a sessão (cache + lookup do max no Mongo)."""
        cached = self._next_seq.get(tmux_name)
        if cached is not None:
            return cached
        doc = await self._coll().find_one(
            {"tmux_name": tmux_name}, sort=[("seq", -1)], projection={"seq": 1}
        )
        seq = (doc["seq"] + 1) if doc else 0
        self._next_seq[tmux_name] = seq
        return seq

    async def _persist(self, tmux_name: str, lines: list[OutputLine]) -> None:
        if not lines:
            return
        docs = []
        for line in lines:
            doc = asdict(line)
            # ``session_id``/``tmux_name``: alinhamos session_id ao tmux_name
            # (chave estável da sessão neste estágio).
            doc["session_id"] = tmux_name
            docs.append(doc)
        await self._coll().insert_many(docs)

    async def _enforce_ring_buffer(self, tmux_name: str) -> None:
        """Mantém no máximo ``max_lines`` linhas por sessão (cap das mais antigas)."""
        coll = self._coll()
        count = await coll.count_documents({"tmux_name": tmux_name})
        excess = count - self._max_lines
        if excess <= 0:
            return
        # Pega os ``excess`` menores seq e remove.
        cursor = coll.find(
            {"tmux_name": tmux_name}, projection={"seq": 1}
        ).sort("seq", 1).limit(excess)
        old_seqs = [d["seq"] async for d in cursor]
        if old_seqs:
            await coll.delete_many(
                {"tmux_name": tmux_name, "seq": {"$in": old_seqs}}
            )

    async def _publish(self, lines: list[OutputLine]) -> None:
        if self._channel is None:
            return
        for line in lines:
            payload = {
                "event": OUTPUT_ROUTING_KEY,
                "tmux_name": line.tmux_name,
                "session_id": line.tmux_name,
                "seq": line.seq,
                "text": line.text,
                "line_type": line.line_type,
                "at": line.at.isoformat(),
            }
            await publish(self._channel, OUTPUT_ROUTING_KEY, payload)
