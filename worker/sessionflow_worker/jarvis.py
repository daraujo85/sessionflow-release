"""JARVIS — resumo falado de sessões, tocado no celular/navegador.

Quando uma sessão **conclui um bloco** ou **aguarda uma decisão**, e o recurso
está habilitado (por sessão via campo ``jarvis`` no doc, ou globalmente via
``app_settings.jarvis_all``), o worker:

1. pega o texto da TELA atual da sessão;
2. gera um **resumo curto** (1-2 frases, pt-BR, para leitura em voz alta);
3. sintetiza a **voz** em ogg/opus e codifica em base64;
4. publica um frame **transiente** ``jarvis_audio`` no RabbitMQ (NÃO persiste no
   Mongo — base64 de áudio não deve inchar a coleção ``events``), que flui pelo
   EventsBroker → SSE → frontend, que toca o áudio no aparelho.

Filosofia (decisão de arquitetura): SessionFlow é **independente do JARVIS**.
Replicamos o caminho LEVE que o próprio JARVIS usa, mas embutido aqui:

- **Voz (default)**: ``say`` nativo do macOS → ``ffmpeg`` → ogg/opus. Zero modelo
  (nada de XTTS/Azure competindo por RAM), zero API externa, zero dependência
  nova — e o worker já roda no Mac. Cada ``say -o`` é um subprocess isolado e
  NÃO usa ``killall say``, então não corta o playback local do JARVIS. O JARVIS
  fala no Mac; o nosso fala no celular — complementares.
- **Resumo (default)**: Ollama local (mesmo modelo do JARVIS), com fallback
  gracioso para o texto do evento quando o Ollama não está no ar.

Configurável por env, então a API hospedada (``audio.boletoazap.dev.br``) segue
disponível como opção premium para uso esporádico:

- ``SESSIONFLOW_JARVIS_TTS``      = ``say`` (default) | ``api`` | ``piper``
- ``SESSIONFLOW_JARVIS_SUMMARY``  = ``ollama`` (default) | ``api`` | ``none``
- ``SESSIONFLOW_JARVIS_VOICE``    = voz do ``say`` (default ``Luciana``) ou voz
  Azure quando ``TTS=api`` (ex. ``pt-BR-AntonioNeural``)
- ``SESSIONFLOW_JARVIS_OLLAMA``   = base do Ollama (default ``http://localhost:11434``)
- ``SESSIONFLOW_JARVIS_OLLAMA_MODEL`` = modelo (default ``llama3.2:3b``)
- ``SESSIONFLOW_JARVIS_RATE``     = rate do ``say`` (default ``190``)
- ``SESSIONFLOW_TTS_BASE``        = base da API hospedada (quando ``=api``)

``piper`` (https://github.com/rhasspy/piper): TTS neural 100% CPU, sem GPU/API
externa — pensado para hosts Linux/Windows sem GPU (ex.: WSL2). Binário
standalone (``SESSIONFLOW_JARVIS_PIPER_BIN``, default
``~/.local/share/piper/piper/piper``) + modelo ``.onnx`` (``SESSIONFLOW_JARVIS_PIPER_MODEL``,
default ``~/.local/share/piper/piper/pt_BR-faber-medium.onnx``). Ambos baixados
manualmente (releases do GitHub + huggingface.co/rhasspy/piper-voices) — sem
``pip``/``apt``/admin, só um binário + arquivo de modelo.

Tudo é **best-effort**: qualquer falha é engolida e jamais derruba o discovery.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import platform
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

import aio_pika
from motor.motor_asyncio import AsyncIOMotorDatabase

from sessionflow_worker import rabbit
from sessionflow_worker.mongo import SESSIONS_COLLECTION

logger = logging.getLogger(__name__)

# --- Config (env, com defaults) ---------------------------------------------

# Voz do SessionFlow: xtts (qualidade boa, reusa o servidor xtts local que o
# usuário já roda) | say (nativo do Mac, básico) | api (audio.boletoazap).
# Default xtts com fallback automático p/ say se o servidor estiver fora.
TTS_MODE = os.environ.get("SESSIONFLOW_JARVIS_TTS", "xtts").lower()
SUMMARY_MODE = os.environ.get("SESSIONFLOW_JARVIS_SUMMARY", "ollama").lower()
SAY_VOICE = os.environ.get("SESSIONFLOW_JARVIS_VOICE", "Luciana")
SAY_RATE = os.environ.get("SESSIONFLOW_JARVIS_RATE", "190")
# Servidor xtts local (mesmo que o JARVIS usa); retorna {path} de um WAV.
XTTS_URL = os.environ.get("SESSIONFLOW_JARVIS_XTTS", "http://127.0.0.1:5111").rstrip("/")
XTTS_LANG = os.environ.get("SESSIONFLOW_JARVIS_LANG", "pt")
OLLAMA_BASE = os.environ.get("SESSIONFLOW_JARVIS_OLLAMA", "http://localhost:11434").rstrip("/")
# Modelo do Ollama p/ o resumo. Default = o que JÁ está instalado nesta máquina
# (llama3.1:8b). Para resumos mais rápidos: `ollama pull llama3.2:3b` e setar
# SESSIONFLOW_JARVIS_OLLAMA_MODEL=llama3.2:3b.
OLLAMA_MODEL = os.environ.get("SESSIONFLOW_JARVIS_OLLAMA_MODEL", "llama3.1:8b")
# Modelo p/ CLASSIFICAR a resposta falada de um picker (modo JARVIS completo)
# contra a lista de opções — tarefa bem mais restrita que o resumo, por isso um
# modelo BEM menor basta (baixa latência) e o parser determinístico (ver
# ``_classify_choice_sync``) já resolve a maioria dos casos sem nem chamar o
# modelo. `ollama pull llama3.2:1b` se ainda não estiver instalado.
CHOICE_MODEL = os.environ.get("SESSIONFLOW_JARVIS_CHOICE_MODEL", "llama3.2:1b")
TTS_BASE_URL = os.environ.get("SESSIONFLOW_TTS_BASE", "https://audio.boletoazap.dev.br").rstrip("/")
# Voz Azure quando TTS=api (a voz `say` não vale lá).
API_VOICE = os.environ.get("SESSIONFLOW_JARVIS_API_VOICE", "pt-BR-AntonioNeural")
# Piper (CPU, sem GPU/API) — binário + modelo baixados manualmente (ver docstring).
PIPER_BIN = os.path.expanduser(
    os.environ.get("SESSIONFLOW_JARVIS_PIPER_BIN", "~/.local/share/piper/piper/piper")
)
PIPER_MODEL = os.path.expanduser(
    os.environ.get(
        "SESSIONFLOW_JARVIS_PIPER_MODEL",
        "~/.local/share/piper/piper/pt_BR-faber-medium.onnx",
    )
)
# Efeito "voz dobrada" (pedido do usuário: soar como o JARVIS do Homem de
# Ferro — a MESMA voz falando duas vezes ao mesmo tempo, levemente defasada).
# Filtro `chorus` do ffmpeg aplicado uniformemente no áudio final (qualquer
# motor de síntese), então funciona igual em say/xtts/piper/api. Desliga com
# SESSIONFLOW_JARVIS_VOICE_EFFECT="" (string vazia).
VOICE_EFFECT = os.environ.get(
    "SESSIONFLOW_JARVIS_VOICE_EFFECT", "chorus=0.5:0.9:55|60:0.5|0.45:0.3|0.25:2|1.4"
).strip()

_SCREEN_TAIL = 2500  # chars da cauda da tela enviados ao resumo.
_HTTP_TIMEOUT = 25
APP_SETTINGS_ID = "app"
APP_SETTINGS_COLLECTION = os.environ.get("SESSIONFLOW_APP_SETTINGS_COLLECTION", "app_settings")

def _summary_sys(owner: str) -> str:
    """Prompt de sistema do resumo falado — papeis (agente vs dono) SEM citar o
    nome do dono (ver histórico abaixo do porquê).

    Motivo original (agora revertido por novo feedback do usuário): definir os
    dois papéis explicitamente e chamar o dono PELO NOME resolvia a ambiguidade
    de quem "voce" era. Só que na pratica ficou estranho ouvir o proprio nome
    toda hora — o usuario pediu pra tirar isso: o AGENTE fala em PRIMEIRA
    PESSOA ("eu terminei X"), e quando sobra algo pro dono da sessao fazer,
    usa um "voce" generico, sem nomear ninguem.
    """
    return (
        "Voce gera um texto curto que sera LIDO EM VOZ ALTA por um sintetizador de "
        "voz (TTS) em portugues do Brasil. Escreva como fala humana natural, do "
        "jeito que uma pessoa contaria rapidinho o que aconteceu. Seja BEM curto: "
        "uma ou no maximo duas frases curtas e diretas. "
        "Voce E o agente (a IA rodando na sessao) contando o que VOCE MESMO fez, "
        "entao fale em PRIMEIRA PESSOA ('eu terminei X', 'eu fiz Y', 'estou "
        "esperando Z'), NUNCA em terceira pessoa ('o agente fez'). Se sobrar "
        "algo pro dono da sessao decidir ou fazer, chame-o de 'voce', SEM DIZER "
        "O NOME dele em nenhuma hipotese (nunca cite nome de pessoa). NAO use "
        "nenhum simbolo nem marcacao: nada de asteriscos, crases, hashtags, "
        "colchetes, parenteses, barras, setas, marcadores, emojis, URLs, "
        "caminhos de arquivo, nomes de variaveis ou trechos de codigo. NAO "
        "leia nem soletre simbolos ou pontuacao (nunca diga a palavra "
        "'ponto'). Use no maximo virgulas e um ponto final por frase, como na "
        "escrita normal. Diga o que voce (o agente) fez e, se estiver "
        "esperando, o que voce precisa que o dono da sessao faca. Responda "
        "APENAS com a frase falada, sem aspas."
    )


_URL_RE = re.compile(r"https?://\S+")
# Símbolos/marcação que um TTS leria em voz alta: markdown, box-drawing (TUI),
# marcadores, setas. Trocados por espaço antes da síntese.
_DROP_RE = re.compile(
    r"[*_#`>\[\](){}<>|~^=+\\/•·●○◦◆■□▪▫▶►◀→⟶←↑↓✓✔✗✘✦✧★☆─-╿]"
)


def _clean_for_speech(text: str) -> str:
    """Tira símbolos/marcação que o TTS leria em voz alta (ex.: ``●``, ``*``).

    Mantém letras, números, vírgulas e pontos internos (pausas naturais), mas
    remove marcação, box-drawing, URLs e pontuação solta nas pontas — assim o
    sintetizador não fala "ponto"/"asterisco" do nada.
    """
    t = _URL_RE.sub("", text or "")
    # Reticências UNICODE (… ‥) viram um ponto ANTES do colapso — o caractere
    # único escapava do tratamento de "..." e o XTTS lia "ponto ponto (ponto)".
    t = t.replace("…", ". ").replace("‥", ". ")
    t = _DROP_RE.sub(" ", t)
    # Ponto ENTRE letras/números (nome de arquivo, versão, decimal) → espaço.
    # Senão o XTTS lê cada ponto como "ponto": ``detalhe.component.ts`` viraria
    # "detalhe ponto component ponto ts"; ``4.8`` → "quatro ponto oito". É a
    # causa do "ponto ponto" no meio de uma fala fluente.
    t = re.sub(r"(?<=\w)\.(?=\w)", " ", t)
    # Colapsa QUALQUER sequência de pontuação (incl. "...", ". .", ".,") num
    # único ponto+espaço — senão o XTTS lê a pontuação solta como "ponto, ponto".
    t = re.sub(r"[.,;:!?](?:\s*[.,;:!?])+", ". ", t)
    t = re.sub(r"\s+([.,!?;:])", r"\1", t)  # espaço antes de pontuação
    # PONTO → VÍRGULA: comprovado por synth+transcrição que o XTTS local fala o
    # "." como a palavra "ponto" (ex.: "Sessão X. resumo" → "...código, PONTO,
    # terminei..."); com vírgula a pausa é a mesma e nada é falado. (Decisão
    # antiga do projeto que havia regredido — não usar "." antes do resumo.)
    t = re.sub(r"\.(\s+|$)", ", ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = t.strip(" .,:;-—–")  # remove pontuação/sobras nas EXTREMIDADES
    return t


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Anti-repetição do JARVIS: último resumo falado por sessão + quando (monotonic).
# Se a detecção de "waiting" re-dispara (pisca após o anti-flap) ou a sessão volta
# a aguardar o MESMO texto, não repetimos a fala dentro dessa janela — evita o
# "fala a mesma coisa de novo tempo depois". Reseta no restart do worker.
_LAST_SPOKEN: dict[str, tuple[str, float]] = {}
_SPEAK_DEDUP_S = 150.0


# --- HTTP helpers (urllib em executor; sem dependência nova) -----------------


def _post_json(url: str, payload: dict[str, Any], timeout: int = _HTTP_TIMEOUT) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _post_form(path: str, fields: dict[str, str], timeout: int = _HTTP_TIMEOUT) -> dict[str, Any]:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        f"{TTS_BASE_URL}{path}",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


# --- Resumo ------------------------------------------------------------------


def _ollama_sync(
    system: str,
    prompt: str,
    num_predict: int = 70,
    temperature: float = 0.3,
    model: str | None = None,
) -> str:
    out = _post_json(
        f"{OLLAMA_BASE}/api/generate",
        {
            "model": model or OLLAMA_MODEL,
            "system": system,
            "prompt": prompt,
            "stream": False,
            # num_predict baixo força brevidade (resumo curto = fala xtts rápida).
            "options": {"temperature": temperature, "num_predict": num_predict},
        },
        timeout=40,
    )
    return (out.get("response") or "").strip()


def _summary_ollama_sync(prompt: str, owner: str) -> str:
    return _ollama_sync(_summary_sys(owner), prompt)


def _summary_api_sync(prompt: str, owner: str) -> str:
    out = _post_form("/ai", {"text": f"{_summary_sys(owner)}\n\n{prompt}", "sanitize": "false"})
    return (out.get("text_output") or "").strip()


# Nome do DONO desta instalação (quem instalou/roda o SessionFlow), usado pra
# desambiguar o resumo falado (ver ``_summary_sys``). Deriva do e-mail de login
# (mesma lógica do Perfil no frontend: pega o local-part, primeiro token antes
# de "."), já que hoje não existe um campo dedicado "nome do dono" na config.
def _owner_display_name() -> str:
    email = os.environ.get("SESSIONFLOW_EMAIL", "").strip()
    local = email.split("@")[0].split(".")[0] if email else ""
    return local.capitalize() if local else ""


async def _summary(screen_text: str, title: str, desc: str) -> str:
    """Resumo curto da tela. Fallback para ``desc``/``title`` em qualquer falha."""
    fallback = desc or title
    tail = (screen_text or "").strip()[-_SCREEN_TAIL:]
    if not tail or SUMMARY_MODE == "none":
        return fallback
    owner = _owner_display_name()
    prompt = f"Contexto: {title}.\n\nConteudo da tela:\n\n{tail}\n\nResumo falado:"
    fn = _summary_api_sync if SUMMARY_MODE == "api" else _summary_ollama_sync
    try:
        loop = asyncio.get_running_loop()
        out = await loop.run_in_executor(None, fn, prompt, owner)
        return out or fallback
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("jarvis: resumo (%s) falhou; usando evento", SUMMARY_MODE, exc_info=True)
        return fallback


# --- Nome falável da sessão -------------------------------------------------

_NAME_SYS = (
    "Voce recebe um identificador tecnico e devolve ELE MESMO, apenas SEPARANDO "
    "as palavras grudadas com espaco para ficar facil de falar. NAO traduza, NAO "
    "adicione nem invente palavras, NAO mude a ordem, NAO mude as letras. So "
    "insira espacos. Exemplos: 'sessionflow' vira 'session flow'; 'prata_digital' "
    "vira 'prata digital'; 'worker-dm-monique' vira 'worker dm monique'; 'portal' "
    "vira 'portal'; 'pvax' vira 'pvax'. Responda APENAS o resultado, sem aspas."
)


def _is_spacing_only(orig: str, out: str) -> bool:
    """True se ``out`` é só o ``orig`` com espaços/separadores (sem traduzir)."""
    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())  # noqa: E731
    return bool(out.strip()) and norm(out) == norm(orig)

# Cache em processo: o nome da sessao nao muda, entao chamamos o modelo no
# maximo uma vez por identificador.
_NAME_CACHE: dict[str, str] = {}


def _name_baseline(name: str) -> str:
    """Versão falável SEM LLM: separadores → espaço, camelCase → palavras."""
    t = re.sub(r"[_\-.]+", " ", name or "")
    t = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t or (name or "")


def _speakable_name_sync(name: str) -> str:
    base = _name_baseline(name)
    if SUMMARY_MODE == "none":
        return base
    try:
        raw = _clean_for_speech(
            _ollama_sync(_NAME_SYS, name, num_predict=24, temperature=0.0)
        )
        # Aceita SÓ se for o mesmo nome com espaços (não traduziu/inventou).
        if _is_spacing_only(name, raw) and len(raw.split()) <= 6:
            return raw
    except Exception:  # noqa: BLE001 - fallback gracioso
        logger.debug("jarvis: nome falável falhou para %r", name, exc_info=True)
    return base


async def _speakable_name(name: str) -> str:
    """Nome falável (cacheado por identificador). Vazio → ''."""
    if not name:
        return ""
    if name in _NAME_CACHE:
        return _NAME_CACHE[name]
    loop = asyncio.get_running_loop()
    label = await loop.run_in_executor(None, _speakable_name_sync, name)
    _NAME_CACHE[name] = label
    return label


async def _display_name(db: AsyncIOMotorDatabase, name: str) -> str:
    """Nome de EXIBIÇÃO/FALADO definido pelo usuário (livre) — '' se não houver."""
    try:
        doc = await db[SESSIONS_COLLECTION].find_one(
            {"tmux_name": name}, projection={"display_name": 1}
        )
        return ((doc or {}).get("display_name") or "").strip()
    except Exception:  # noqa: BLE001 - best-effort
        return ""


async def _tts_label(db: AsyncIOMotorDatabase, name: str) -> str:
    """Nome pro TTS: prefere o display_name do usuário (fala natural); senão gera
    um falável a partir do nome técnico do tmux."""
    return await _display_name(db, name) or await _speakable_name(name)


# --- Síntese de voz ----------------------------------------------------------


def _synth_say_sync(text: str) -> tuple[str, str] | None:
    """macOS ``say`` → AIFF → ffmpeg ogg/opus → (base64, mime). Sem modelo/dep."""
    aiff = tempfile.mktemp(suffix=".aiff")
    ogg = tempfile.mktemp(suffix=".ogg")
    try:
        # NÃO usa `killall say` — não interromper o JARVIS local.
        subprocess.run(
            ["say", "-v", SAY_VOICE, "-r", SAY_RATE, "-o", aiff, text],
            check=True,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", aiff,
             "-c:a", "libopus", "-b:a", "32k", "-ar", "48000", ogg],
            check=True,
        )
        with open(ogg, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii"), "audio/ogg"
    finally:
        for p in (aiff, ogg):
            try:
                os.remove(p)
            except OSError:
                pass


def _synth_xtts_sync(text: str) -> tuple[str, str] | None:
    """Servidor xtts local → WAV → ffmpeg ogg/opus → (base64, mime). Voz boa.

    Reusa o MESMO servidor xtts que o JARVIS usa (já carregado na RAM do Mac),
    então não adiciona modelo/memória ao nosso stack.
    """
    resp = _post_json(f"{XTTS_URL}/synth", {"text": text, "lang": XTTS_LANG}, timeout=120)
    wav = resp.get("path")
    if not wav or not os.path.exists(wav):
        return None
    ogg = tempfile.mktemp(suffix=".ogg")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", wav,
             "-c:a", "libopus", "-b:a", "32k", "-ar", "48000", ogg],
            check=True,
        )
        with open(ogg, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii"), "audio/ogg"
    finally:
        for p in (wav, ogg):
            try:
                os.remove(p)
            except OSError:
                pass


def _synth_piper_sync(text: str) -> tuple[str, str] | None:
    """Binário ``piper`` (CPU) → WAV → ffmpeg ogg/opus → (base64, mime).

    Sem servidor/API: o binário é standalone, síntese local por processo. A
    lib compartilhada (``libonnxruntime``/``libpiper_phonemize``) fica ao lado
    do binário no release baixado, daí o ``LD_LIBRARY_PATH`` apontando pra lá.
    """
    wav = tempfile.mktemp(suffix=".wav")
    ogg = tempfile.mktemp(suffix=".ogg")
    try:
        env = {**os.environ, "LD_LIBRARY_PATH": os.path.dirname(PIPER_BIN)}
        subprocess.run(
            [PIPER_BIN, "--model", PIPER_MODEL, "--output_file", wav],
            input=text.encode("utf-8"),
            check=True,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", wav,
             "-c:a", "libopus", "-b:a", "32k", "-ar", "48000", ogg],
            check=True,
        )
        with open(ogg, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii"), "audio/ogg"
    finally:
        for p in (wav, ogg):
            try:
                os.remove(p)
            except OSError:
                pass


# --- Disponibilidade/instalação de motores (Perfil > Áudio) -----------------
#
# "installed": o motor JÁ FUNCIONA nesse host agora. "installable": não está
# instalado, mas dá pra baixar/instalar sozinho (hoje só o Piper — say é
# nativo do SO, xtts é um servidor à parte que não instalamos por aqui, api
# não precisa instalar nada). O Perfil usa isso pra: (1) nem OFERECER um
# motor impossível nesse SO (ex.: "say" fora do Mac), (2) mostrar um botão
# de instalar (com indicador de progresso) em vez de falhar calado quando o
# usuário escolhe um motor que ainda não está no host.

_PIPER_RELEASE_TAG = "2023.11.14-2"
_PIPER_RELEASE_BASE = f"https://github.com/rhasspy/piper/releases/download/{_PIPER_RELEASE_TAG}"
_PIPER_VOICE_BASE = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium"
)
_PIPER_ASSETS = {
    ("darwin", "arm64"): "piper_macos_aarch64.tar.gz",
    ("darwin", "x86_64"): "piper_macos_x64.tar.gz",
    ("linux", "aarch64"): "piper_linux_aarch64.tar.gz",
    ("linux", "x86_64"): "piper_linux_x86_64.tar.gz",
}


def is_piper_installed() -> bool:
    return os.path.isfile(PIPER_BIN) and os.path.isfile(PIPER_MODEL)


def piper_asset_for_this_host() -> str | None:
    """Nome do asset do release do Piper certo pra ESTE host (SO+arquitetura),
    ou None se a plataforma não tem build oficial (ex.: Windows nativo — o
    worker aqui sempre roda em WSL2/Linux ou macOS)."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x86_64"
    return _PIPER_ASSETS.get((system, arch))


def tts_engine_status() -> dict[str, dict[str, bool]]:
    """Status de cada motor NESTE host: instalado? instalável (auto)?"""
    is_darwin = platform.system().lower() == "darwin"
    return {
        "say": {"installed": is_darwin, "installable": False},
        "xtts": {"installed": is_darwin, "installable": False},
        "piper": {
            "installed": is_piper_installed(),
            "installable": not is_piper_installed() and piper_asset_for_this_host() is not None,
        },
        "api": {"installed": True, "installable": False},
    }


def install_piper_sync() -> bool:
    """Baixa o binário do Piper (release do GitHub) + o modelo de voz pt-BR
    (HuggingFace) pros caminhos padrão (``PIPER_BIN``/``PIPER_MODEL``).

    Sem dependência nova (``urllib``/``tarfile``, stdlib). Idempotente: se já
    estiver instalado, nem baixa de novo. Best-effort: qualquer falha (rede,
    plataforma sem build oficial) devolve ``False`` sem lançar — o caller
    (comando ``jarvis_install_piper``) vira erro de comando normal.
    """
    if is_piper_installed():
        return True
    asset = piper_asset_for_this_host()
    if not asset:
        return False
    import tarfile

    dest_dir = os.path.dirname(os.path.dirname(PIPER_BIN))  # ~/.local/share/piper
    os.makedirs(dest_dir, exist_ok=True)
    tar_path = os.path.join(dest_dir, "piper-download.tar.gz")
    try:
        urllib.request.urlretrieve(f"{_PIPER_RELEASE_BASE}/{asset}", tar_path)
        with tarfile.open(tar_path) as tf:
            tf.extractall(dest_dir)  # noqa: S202 - fonte fixa (nosso release URL), não input do usuário
        if os.path.isfile(PIPER_BIN):
            os.chmod(PIPER_BIN, 0o755)
        urllib.request.urlretrieve(
            f"{_PIPER_VOICE_BASE}/pt_BR-faber-medium.onnx", PIPER_MODEL
        )
        urllib.request.urlretrieve(
            f"{_PIPER_VOICE_BASE}/pt_BR-faber-medium.onnx.json", f"{PIPER_MODEL}.json"
        )
        return is_piper_installed()
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("jarvis: install_piper falhou", exc_info=True)
        return False
    finally:
        try:
            os.remove(tar_path)
        except OSError:
            pass


def _synth_api_sync(text: str) -> tuple[str, str] | None:
    out = _post_form("/tts", {"text": text, "voice": API_VOICE, "convert_to_ogg": "true"})
    b64 = out.get("audio_base64")
    if not b64:
        return None
    return b64, (out.get("audio_mime") or "audio/ogg")


def _apply_voice_effect_sync(audio: tuple[str, str]) -> tuple[str, str]:
    """Duplica a voz sobre ela mesma, levemente defasada (filtro ``chorus`` do
    ffmpeg) — dá aquele ar de "assistente do Homem de Ferro" pedido pelo
    usuário. Aplicado uma única vez aqui (não em cada `_synth_*_sync`), então
    funciona igual não importa o motor (say/xtts/piper/api). Best-effort: se
    o ffmpeg falhar por qualquer razão, devolve o áudio ORIGINAL sem efeito
    (nunca deixa o JARVIS mudo por causa disso).
    """
    b64, mime = audio
    if not VOICE_EFFECT:
        return audio
    src = tempfile.mktemp(suffix=".ogg")
    out = tempfile.mktemp(suffix=".ogg")
    try:
        with open(src, "wb") as f:
            f.write(base64.b64decode(b64))
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", src,
             "-af", VOICE_EFFECT, "-c:a", "libopus", "-b:a", "32k", "-ar", "48000", out],
            check=True,
        )
        with open(out, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii"), "audio/ogg"
    except Exception:  # noqa: BLE001 - best-effort, cai pro áudio sem efeito
        logger.debug("jarvis: efeito de voz falhou; usando áudio original", exc_info=True)
        return b64, mime
    finally:
        for p in (src, out):
            try:
                os.remove(p)
            except OSError:
                pass


_TTS_MODES = frozenset({"say", "xtts", "piper", "api"})
WORKER_STATUS_COLLECTION = "worker_status"


async def _host_audio_settings(
    db: AsyncIOMotorDatabase | None, host_id: str | None
) -> tuple[str, bool]:
    """(modo TTS, efeito ligado) efetivos para este HOST — Perfil > Áudio.

    Lê ``worker_status.<host_id>`` (``tts_mode``/``voice_effect``, editáveis
    via ``PUT /workers/{host_id}/audio-settings``). Ausente/``None`` cai no
    default: modo do env (``SESSIONFLOW_JARVIS_TTS``), efeito LIGADO (mesmo
    comportamento de antes desta config existir). Best-effort: qualquer falha
    de leitura usa o default, nunca derruba a síntese.
    """
    mode = TTS_MODE
    effect_on = True
    if db is not None and host_id:
        try:
            doc = await db[WORKER_STATUS_COLLECTION].find_one(
                {"_id": host_id}, projection={"tts_mode": 1, "voice_effect": 1}
            )
            if doc:
                doc_mode = doc.get("tts_mode")
                if doc_mode in _TTS_MODES:
                    mode = doc_mode
                if doc.get("voice_effect") is not None:
                    effect_on = bool(doc["voice_effect"])
        except Exception:  # noqa: BLE001 - best-effort
            logger.debug("jarvis: leitura de audio-settings falhou p/ %r", host_id, exc_info=True)
    return mode, effect_on


async def _synth(
    text: str,
    db: AsyncIOMotorDatabase | None = None,
    host_id: str | None = None,
) -> tuple[str, str] | None:
    mode, effect_on = await _host_audio_settings(db, host_id)
    loop = asyncio.get_running_loop()
    try:
        result: tuple[str, str] | None = None
        if mode == "api":
            result = await loop.run_in_executor(None, _synth_api_sync, text)
        elif mode == "piper":
            result = await loop.run_in_executor(None, _synth_piper_sync, text)
        elif mode == "xtts":
            # Tenta a voz boa (xtts); cai p/ `say` se o servidor estiver fora.
            try:
                result = await loop.run_in_executor(None, _synth_xtts_sync, text)
            except Exception:  # noqa: BLE001 - fallback gracioso
                logger.debug("jarvis: xtts falhou; caindo p/ say", exc_info=True)
            if not result:
                result = await loop.run_in_executor(None, _synth_say_sync, text)
        else:
            result = await loop.run_in_executor(None, _synth_say_sync, text)
        if result is None:
            return None
        if not effect_on:
            return result
        return await loop.run_in_executor(None, _apply_voice_effect_sync, result)
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("jarvis: sintese (%s) falhou", mode, exc_info=True)
        return None


# --- Habilitação -------------------------------------------------------------


async def is_enabled(db: AsyncIOMotorDatabase, name: str) -> bool:
    """True se o JARVIS está ligado p/ esta sessão (global OU por sessão)."""
    try:
        settings = await db[APP_SETTINGS_COLLECTION].find_one(
            {"_id": APP_SETTINGS_ID}, projection={"jarvis_all": 1}
        )
        if settings and settings.get("jarvis_all"):
            return True
        doc = await db[SESSIONS_COLLECTION].find_one(
            {"tmux_name": name}, projection={"jarvis": 1}
        )
        return bool(doc and doc.get("jarvis"))
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("jarvis: is_enabled falhou para %r", name, exc_info=True)
        return False


async def is_full_mode(db: AsyncIOMotorDatabase, name: str) -> bool:
    """True se esta sessão está no modo "completo" (conversa: picker + voz).

    Checa o atalho global ``jarvis_full_all`` (Perfil) OU o campo por-sessão
    ``jarvis_mode == "full"`` — mesmo padrão do OR em :func:`is_enabled`, só
    que num toggle SEPARADO do ``jarvis_all`` de propósito: ligar áudio em
    toda sessão é inofensivo, ligar ESCUTA DE MICROFONE em toda sessão é uma
    decisão maior, então o usuário escolhe explicitamente esse global à parte
    (não fica embutido/implícito no toggle de áudio comum).
    """
    try:
        settings = await db[APP_SETTINGS_COLLECTION].find_one(
            {"_id": APP_SETTINGS_ID}, projection={"jarvis_full_all": 1}
        )
        if settings and settings.get("jarvis_full_all"):
            return True
        doc = await db[SESSIONS_COLLECTION].find_one(
            {"tmux_name": name}, projection={"jarvis_mode": 1}
        )
        return bool(doc and doc.get("jarvis_mode") == "full")
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("jarvis: is_full_mode falhou para %r", name, exc_info=True)
        return False


# --- Pipeline + publicação ---------------------------------------------------


async def _publish(channel: aio_pika.abc.AbstractChannel, payload: dict[str, Any]) -> None:
    """Publica um frame transiente (não persistido) no exchange de eventos."""
    exchange = await channel.get_exchange(rabbit.EXCHANGE_NAME)
    message = aio_pika.Message(
        body=json.dumps(payload).encode("utf-8"),
        content_type="application/json",
        delivery_mode=aio_pika.DeliveryMode.NOT_PERSISTENT,
    )
    await exchange.publish(message, routing_key=rabbit.EVENTS_QUEUE)


async def maybe_speak(
    db: AsyncIOMotorDatabase,
    channel: aio_pika.abc.AbstractChannel | None,
    name: str,
    title: str,
    desc: str,
    screen_text: str,
    host_id: str | None = None,
) -> None:
    """Se habilitado, gera resumo+voz e publica o frame ``jarvis_audio``.

    Best-effort de ponta a ponta — nunca levanta. Pensado para rodar como uma
    task em background (``asyncio.create_task``) para não bloquear o discovery.
    """
    if channel is None:
        return
    try:
        if not await is_enabled(db, name):
            return
        summary = _clean_for_speech(await _summary(screen_text, title, desc))
        # Dedupe por conteúdo+tempo: se o MESMO resumo foi falado há pouco pra esta
        # sessão, não repete (economiza TTS e evita a fala duplicada "de novo").
        prev = _LAST_SPOKEN.get(name)
        if prev and prev[0] == summary and (time.monotonic() - prev[1]) < _SPEAK_DEDUP_S:
            logger.debug("jarvis: resumo repetido em %r — pulando", name)
            return
        _LAST_SPOKEN[name] = (summary, time.monotonic())
        # Anuncia a sessão no INÍCIO, com um nome FALÁVEL gerado pelo modelo
        # (ex.: "sessionflow" → "session flow"), pausa, e então o resumo — assim,
        # com várias sessões falando, dá pra saber de quem é. O ponto é interno
        # → vira pausa no TTS (não a palavra "ponto").
        label = await _tts_label(db, name)
        spoken = _clean_for_speech(f"Sessão {label}. {summary}" if label else summary)
        audio = await _synth(spoken, db, host_id)
        if audio is None:
            return
        b64, mime = audio
        await _publish(
            channel,
            {
                "type": "jarvis_audio",
                "session_id": name,
                "title": title,
                "text": summary,
                "audio_b64": b64,
                "mime": mime,
                "at": _now_iso(),
            },
        )
        logger.info("jarvis: falou em %r (%d chars)", name, len(summary))
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("jarvis: maybe_speak falhou para %r", name, exc_info=True)


# --- Escolha por voz (modo completo) -----------------------------------------


def _build_choice_question(options: list[dict[str, str]]) -> str:
    parts = [f"{o['key']}, {o['label']}" for o in options]
    return "Preciso que você escolha: " + "; ".join(parts) + "."


async def maybe_ask_choice(
    db: AsyncIOMotorDatabase,
    channel: aio_pika.abc.AbstractChannel | None,
    name: str,
    options: list[dict[str, str]],
    host_id: str | None = None,
) -> None:
    """Se a sessão está em modo completo, fala as opções e publica ``jarvis_choice``.

    Best-effort de ponta a ponta — nunca levanta. O caller (``discovery.py``) já
    filtrou por :func:`is_full_mode`, mas checamos de novo aqui (defesa em
    profundidade, mesmo padrão de :func:`maybe_speak`).
    """
    if channel is None or not options:
        return
    try:
        if not await is_full_mode(db, name):
            return
        question = _clean_for_speech(_build_choice_question(options))
        audio = await _synth(question, db, host_id)
        if audio is None:
            return
        b64, mime = audio
        await _publish(
            channel,
            {
                "type": "jarvis_choice",
                "session_id": name,
                "title": "Preciso que você escolha",
                "options": options,
                "audio_b64": b64,
                "mime": mime,
                "at": _now_iso(),
            },
        )
        logger.info("jarvis: perguntou escolha em %r (%d opções)", name, len(options))
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("jarvis: maybe_ask_choice falhou para %r", name, exc_info=True)


async def maybe_ask_open(
    db: AsyncIOMotorDatabase,
    channel: aio_pika.abc.AbstractChannel | None,
    name: str,
    title: str,
    desc: str,
    screen_text: str,
    host_id: str | None = None,
) -> None:
    """Pergunta ABERTA (prosa, sem picker numerado) em modo completo.

    Fala o resumo da pergunta e publica um ``jarvis_choice`` com ``options``
    VAZIO — o frontend reusa o mesmo pipeline (toca, abre o mic), e como a
    sessão NÃO tem ``pending_choice`` no doc, a transcrição do áudio segue o
    caminho normal do ``_handle_audio``: injeta o texto livre + Enter. Ou
    seja: você fala a resposta em linguagem natural e ela vira o texto
    digitado no terminal — sem classificação de opção, porque não há opções.
    """
    if channel is None:
        return
    try:
        if not await is_full_mode(db, name):
            return
        summary = _clean_for_speech(await _summary(screen_text, title, desc))
        prev = _LAST_SPOKEN.get(name)
        if prev and prev[0] == summary and (time.monotonic() - prev[1]) < _SPEAK_DEDUP_S:
            return
        _LAST_SPOKEN[name] = (summary, time.monotonic())
        label = await _tts_label(db, name)
        spoken = _clean_for_speech(
            f"Sessão {label}. {summary} Pode responder." if label else f"{summary} Pode responder."
        )
        audio = await _synth(spoken, db, host_id)
        if audio is None:
            return
        b64, mime = audio
        await _publish(
            channel,
            {
                "type": "jarvis_choice",
                "session_id": name,
                "title": title or "Preciso da sua resposta",
                "options": [],
                "audio_b64": b64,
                "mime": mime,
                "at": _now_iso(),
            },
        )
        logger.info("jarvis: pergunta aberta em %r", name)
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("jarvis: maybe_ask_open falhou para %r", name, exc_info=True)


async def reask_open(
    db: AsyncIOMotorDatabase,
    channel: aio_pika.abc.AbstractChannel | None,
    name: str,
    host_id: str | None = None,
) -> None:
    """Usuário negou a confirmação da resposta livre → reabre o mic.

    Fala um convite curto e publica outro ``jarvis_choice`` com ``options``
    vazio (mesmo frame da pergunta aberta) — o frontend toca e abre o mic de
    novo pra ele repetir a resposta.
    """
    if channel is None:
        return
    try:
        if not await is_full_mode(db, name):
            return
        audio = await _synth("Ok. Pode falar sua resposta de novo.", db, host_id)
        if audio is None:
            return
        b64, mime = audio
        await _publish(
            channel,
            {
                "type": "jarvis_choice",
                "session_id": name,
                "title": "Pode repetir a resposta",
                "options": [],
                "audio_b64": b64,
                "mime": mime,
                "at": _now_iso(),
            },
        )
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("jarvis: reask_open falhou para %r", name, exc_info=True)


# --- Quick replies (sugestões de resposta) -----------------------------------

_QUICK_REPLIES_SYS = (
    "Voce ve o final da tela de um agente de codigo que fez uma pergunta ou "
    "espera uma decisao do usuario. Gere ate 3 RESPOSTAS CURTAS e uteis que o "
    "usuario poderia dar, em portugues do Brasil, cada uma com no maximo 8 "
    "palavras. Uma resposta por linha, sem numeracao, sem aspas, sem "
    "explicacao. Se a tela mostra opcoes numeradas, as respostas devem ser os "
    "numeros ou frases curtas equivalentes. Varie: inclua uma afirmativa, uma "
    "negativa/alternativa quando fizer sentido."
)


def _quick_replies_sync(screen_tail: str) -> list[str]:
    raw = _ollama_sync(_QUICK_REPLIES_SYS, screen_tail, num_predict=80, temperature=0.4)
    out: list[str] = []
    for ln in raw.splitlines():
        ln = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", ln).strip().strip('"')
        if ln and len(ln) <= 60 and ln not in out:
            out.append(ln)
        if len(out) >= 3:
            break
    return out


async def maybe_quick_replies(
    db: AsyncIOMotorDatabase,
    channel: aio_pika.abc.AbstractChannel | None,
    name: str,
    screen_text: str,
) -> None:
    """Gera sugestões de resposta rápida e publica ``quick_replies`` (SSE).

    Sem TTS — é puramente visual: chips clicáveis acima do input da sessão.
    Roda pra QUALQUER sessão aguardando (não é gateado pelo modo JARVIS —
    não abre mic nem toca áudio, então não incomoda). Best-effort.
    """
    if channel is None or SUMMARY_MODE == "none":
        return
    try:
        tail = (screen_text or "").strip()[-_SCREEN_TAIL:]
        if not tail:
            return
        loop = asyncio.get_running_loop()
        suggestions = await loop.run_in_executor(None, _quick_replies_sync, tail)
        if not suggestions:
            return
        await _publish(
            channel,
            {
                "type": "quick_replies",
                "session_id": name,
                "suggestions": suggestions,
                "at": _now_iso(),
            },
        )
        logger.info("jarvis: quick replies em %r (%d)", name, len(suggestions))
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("jarvis: quick_replies falhou para %r", name, exc_info=True)


# Opções da pergunta de CONFIRMAÇÃO (sim/não) — mesmo formato de `options` do
# picker original, então reaproveita o pipeline inteiro do frontend
# (jarvis_choice: toca áudio, abre mic, botões de fallback) sem mudar nada lá.
CONFIRM_OPTIONS: list[dict[str, str]] = [
    {"key": "sim", "label": "Sim, confirma"},
    {"key": "nao", "label": "Não, deixa eu falar de novo"},
]


async def maybe_confirm_choice(
    db: AsyncIOMotorDatabase,
    channel: aio_pika.abc.AbstractChannel | None,
    name: str,
    label: str,
    host_id: str | None = None,
    open_text: bool = False,
) -> None:
    """Fala a opção RESOLVIDA e pede confirmação (sim/não) antes de injetar.

    Evita que um erro do parser/LLM (ver `classify_choice`) — ou uma
    transcrição errada do Whisper, no caso de resposta LIVRE
    (``open_text=True``) — vire input errado digitado sem o usuário
    perceber. O caller só injeta depois do "sim" (``_handle_audio`` no
    ``command_consumer.py`` guia esse fluxo).
    """
    if channel is None:
        return
    try:
        if not await is_full_mode(db, name):
            return
        phrase = (
            f"Entendi: {label}. Confirma? Diga sim ou não."
            if open_text
            else f"Você escolheu: {label}. Confirma? Diga sim ou não."
        )
        question = _clean_for_speech(phrase)
        audio = await _synth(question, db, host_id)
        if audio is None:
            return
        b64, mime = audio
        await _publish(
            channel,
            {
                "type": "jarvis_choice",
                "session_id": name,
                "title": "Confirma a escolha?",
                "options": CONFIRM_OPTIONS,
                "audio_b64": b64,
                "mime": mime,
                "at": _now_iso(),
            },
        )
        logger.info("jarvis: pediu confirmação em %r (%r)", name, label)
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("jarvis: maybe_confirm_choice falhou para %r", name, exc_info=True)


# Ordinais/números por extenso em PT-BR → índice 0-based na lista de opções.
# Cobre as formas mais comuns de resposta curta; qualquer coisa fora disso cai
# no fallback do LLM (ver `_classify_choice_sync`).
_ORDINAL_WORDS: dict[str, int] = {
    "primeira": 0, "primeiro": 0, "um": 0, "uma": 0,
    "segunda": 1, "segundo": 1, "dois": 1, "duas": 1,
    "terceira": 2, "terceiro": 2, "tres": 2,
    "quarta": 3, "quarto": 3, "quatro": 3,
    "quinta": 4, "quinto": 4, "cinco": 4,
    "sexta": 5, "sexto": 5, "seis": 5,
    "setima": 6, "setimo": 6, "sete": 6,
    "oitava": 7, "oitavo": 7, "oito": 7,
    "nona": 8, "nono": 8, "nove": 8,
    "decima": 9, "decimo": 9, "dez": 9,
}
_LAST_WORDS = frozenset({"ultima", "ultimo"})
_PENULTIMATE_WORDS = frozenset({"penultima", "penultimo"})
_YES_WORDS = frozenset({"sim", "isso", "confirmo", "confirma", "positivo"})
_NO_WORDS = frozenset({"nao", "negativo", "cancela", "cancelar"})


def _strip_accents(text: str) -> str:
    import unicodedata

    return "".join(
        ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn"
    )


def _normalize_spoken(text: str) -> list[str]:
    norm = _strip_accents(text.lower())
    norm = re.sub(r"[^a-z0-9\s]", " ", norm)
    return norm.split()


def _classify_choice_rule_based(
    spoken_text: str, options: list[dict[str, str]]
) -> str | None:
    """Parser determinístico (sem LLM): ordinal, dígito, "opção N", sim/não.

    Zero latência e zero risco de alucinação — cobre a grande maioria das
    respostas curtas esperadas aqui. Retorna a KEY da opção escolhida, ou
    ``None`` se a frase não casar com nenhum padrão conhecido (aí quem chamou
    cai pro fallback do LLM).
    """
    words = _normalize_spoken(spoken_text)
    if not words:
        return None
    n = len(options)
    valid_keys = {o["key"] for o in options}

    # "número 2" / "opção 2" / dígito solto que bate com uma key OU posição.
    digits = [w for w in words if w.isdigit()]
    if digits:
        d = digits[0]
        if d in valid_keys:
            return d
        idx = int(d) - 1
        if 0 <= idx < n:
            return options[idx]["key"]

    # Ordinal por extenso.
    for w in words:
        if w in _LAST_WORDS:
            return options[-1]["key"]
        if w in _PENULTIMATE_WORDS and n >= 2:
            return options[-2]["key"]
        if w in _ORDINAL_WORDS:
            idx = _ORDINAL_WORDS[w]
            if 0 <= idx < n:
                return options[idx]["key"]

    # Sim/não — só quando alguma opção claramente rotula isso (evita casar
    # "sim" contra um picker sem opção de confirmação óbvia).
    has_yes_no = any(w in _YES_WORDS for w in words) or any(w in _NO_WORDS for w in words)
    if has_yes_no:
        for o in options:
            label = _strip_accents(o["label"].lower())
            if any(w in _YES_WORDS for w in words) and label.startswith(("sim", "yes")):
                return o["key"]
            if any(w in _NO_WORDS for w in words) and label.startswith(("nao", "no")):
                return o["key"]

    return None


_CHOICE_CLASSIFY_SYS = (
    "Voce recebe uma pergunta com opcoes numeradas e uma frase falada por uma "
    "pessoa respondendo. Responda APENAS o numero da opcao escolhida, sem mais "
    "nada (sem pontuacao, sem palavras). Se a frase nao deixar claro qual "
    "opcao, responda exatamente 'nenhuma'."
)


def _classify_choice_llm_sync(spoken_text: str, options: list[dict[str, str]]) -> str | None:
    options_txt = "\n".join(f"{o['key']}. {o['label']}" for o in options)
    prompt = f"Opções:\n{options_txt}\n\nResposta falada: \"{spoken_text}\"\n\nNúmero:"
    valid_keys = {o["key"] for o in options}
    try:
        raw = _ollama_sync(
            _CHOICE_CLASSIFY_SYS, prompt, num_predict=6, temperature=0.0, model=CHOICE_MODEL
        )
    except Exception:  # noqa: BLE001 - fallback gracioso
        logger.debug("jarvis: classify_choice (llm) falhou", exc_info=True)
        return None
    # Validação estrita: só aceita se a saída for EXATAMENTE uma key válida
    # (às vezes o modelo devolve "2." ou "Opção 2" mesmo pedindo só o número —
    # extrai o primeiro dígito da saída e valida contra as keys).
    m = re.search(r"\d+", raw)
    if not m:
        return None
    key = m.group(0)
    return key if key in valid_keys else None


async def classify_choice(spoken_text: str, options: list[dict[str, str]]) -> str | None:
    """Traduz uma resposta falada livre pra KEY da opção certa (ou ``None``).

    1ª tentativa: parser determinístico (rápido, zero alucinação). Só cai pro
    LLM pequeno (:data:`CHOICE_MODEL`) se a frase não casar com nenhum padrão
    conhecido — e mesmo assim valida a saída contra as keys válidas antes de
    aceitar (nunca "confia cegamente" na saída do modelo).
    """
    if not spoken_text or not options:
        return None
    key = _classify_choice_rule_based(spoken_text, options)
    if key is not None:
        return key
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, _classify_choice_llm_sync, spoken_text, options
        )
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("jarvis: classify_choice falhou para %r", spoken_text, exc_info=True)
        return None
