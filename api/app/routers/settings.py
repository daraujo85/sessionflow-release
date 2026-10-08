"""Configurações gerais do app (single-user) — coleção ``app_settings``.

Hoje guarda ``milestones_auto`` (instruir as sessões a trabalhar em
tarefas/marcos automaticamente ao abrir/criar) e ``voice_dedup_enabled``
(condensar transcrição de áudio via SLM local antes de injetar no terminal).
Doc único ``_id="app"``.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/settings", tags=["settings"])

SETTINGS_ID = "app"


def milestones_instruction(session: str) -> str:
    """Instrução (uma linha) injetada na sessão p/ manter os marcos.

    O nome do arquivo é NAMESPACED pela sessão (``milestones.<session>.json``)
    para não colidir quando várias sessões compartilham o mesmo diretório.
    """
    return (
        "[SessionFlow] A partir de agora, trabalhe em tarefas/marcos: mantenha o "
        f"arquivo .sessionflow/milestones.{session}.json na raiz do projeto no "
        'formato {"description":"<1 frase>","milestones":[{"id":"<kebab>",'
        '"title":"<curto>","status":"todo|doing|blocked|done"}]}, criando e '
        "atualizando o status conforme avança. O SessionFlow lê esse arquivo para "
        "mostrar suas tarefas. O campo \"description\" é uma descrição BREVE e "
        "VIVA (1 frase, sua observação) do que se trata esta sessão — o app "
        "mostra junto do nome; atualize-a sempre que o foco/assunto da sessão "
        "mudar de verdade. "
        f"Use EXATAMENTE esse nome de arquivo (milestones.{session}.json). "
        "IMPORTANTE: assim que TERMINAR uma tarefa, marque-a como \"done\" na hora "
        "(não deixe em \"doing\"); tenha no máximo UMA tarefa em \"doing\" por vez. "
        "Marcar \"done\" é o que sinaliza a conclusão pro usuário (som + destaque). "
        "NÃO use sua lista de tarefas interna (ex.: TodoWrite) pra isso — ela fica "
        "renderizada no terminal (e some empilhando os itens já concluídos), "
        "atrapalhando a leitura dos logs na área do terminal do app; o arquivo "
        ".sessionflow/milestones já É o painel de tarefas (visível no app), "
        "então essa lista redundante no terminal é só ruído. Se sua ferramenta "
        "interna de tarefas abrir sozinha mesmo assim, ao terminar cada item "
        "REMOVA-o da lista em vez de deixá-lo marcado/acumulado ali. "
        "MANTENHA O PAINEL ENXUTO E CONFIÁVEL (faça faxina sempre): a lista deve "
        "refletir o estado REAL do trabalho — no máximo ~8 itens focados no que "
        "está vivo/recente. NÃO acumule histórico: quando um marco ficar obsoleto "
        "ou for superado, REMOVA-o; não mantenha várias linhas 'done' sobre o mesmo "
        "ticket/feature (colapse em UMA). Antes de mudar um status, confira o "
        "estado real (o que de fato foi concluído). Poucos itens verdadeiros valem "
        "mais que uma lista longa e desatualizada. "
        "FORMATO DE ENTREGA (checkpoint): ao CONCLUIR algo que o usuário pediu, "
        "feche com um checkpoint estruturado e enxuto — '✅ Checkpoint — <título "
        "curto>' seguido de: (1) Arquivos alterados: path — o quê/por quê, 1 linha "
        "cada; (2) Comportamento: como funciona agora (flags/casos especiais); "
        "(3) Como foi testado: cenários REAIS → resultado (tabela se ajudar) — só "
        "liste o que você de fato executou; (4) Fora de escopo respeitado: o que "
        "NÃO foi mexido de propósito; (5) Próximos candidatos (opcional, curto). "
        "Seja factual e verificável; se algo falhou ou ficou pendente, diga "
        "claramente em vez de omitir. "
        "ARQUIVOS GERADOS (imagem/PDF/relatório etc.): se o usuário pode querer "
        "ver esse arquivo pelo celular/fora do computador, rode "
        "'./tools/sf share <caminho-do-arquivo>' (ou "
        "'~/.claude/skills/sf-delegate/sf share <caminho>' se 'tools/sf' não "
        "existir nesse repo) — sobe o arquivo pro app, com botão de "
        "download/preview na tela desta sessão. Não precisa passar a sessão "
        "de destino: o comando detecta sozinho."
    )


def milestones_refresh_instruction(session: str) -> str:
    """Instrução periódica (revisão) — diferente da instrução inicial acima.

    Enviada automaticamente pelo scheduler de milestones (não pelo botão
    manual, removido; ver ``app/scheduler.py:_milestones_tick``), a cada
    ``milestones_refresh_interval_seconds``. Pede uma FAXINA no arquivo já
    existente em vez de instruir a criá-lo do zero.
    """
    return (
        f"[SessionFlow] Revise AGORA o arquivo .sessionflow/milestones.{session}.json: "
        "confira o estado REAL do trabalho, atualize status desatualizados, remova "
        'itens obsoletos/duplicados e garanta no máximo uma tarefa "doing". '
        'Revise também o campo "description" (1 frase, o que se trata esta sessão '
        "na SUA observação) — atualize se o foco mudou; crie se não existir."
    )


class SettingsOut(BaseModel):
    """Configurações expostas ao app."""

    milestones_auto: bool = True
    # JARVIS (voz) ligado para TODAS as sessões (atalho global; o liga/desliga
    # por-sessão fica no doc da sessão). Default off — fala só onde pedido.
    jarvis_all: bool = False
    # JARVIS COMPLETO (picker + resposta por voz) para TODAS as sessões —
    # atalho global separado do `jarvis_all` de propósito: ligar áudio em toda
    # sessão é inofensivo, ligar ESCUTA DE MICROFONE em toda sessão é uma
    # decisão maior, então precisa de um toggle explícito próprio. Default off.
    jarvis_full_all: bool = False
    # Compressão pós-transcrição: remove redundâncias do áudio transcrito via
    # SLM local (Ollama) antes de injetar no terminal. Default off — requer
    # Ollama rodando no host com o modelo configurado.
    voice_dedup_enabled: bool = False
    # Modelo SLM para dedup. qwen3:0.6b roda em CPU (~400MB); qwen3:1.7b é
    # melhor se houver GPU (M4/CUDA). Qualquer modelo Ollama serve.
    voice_dedup_model: str = "qwen3:0.6b"
    # Dedup automático do TEXTO DIGITADO/ENVIADO: ao apertar Enviar, passa o
    # draft pelo mesmo Ollama/SLM antes de submeter (preserva comandos, URLs,
    # emojis, line breaks). Default off — feature separada de voz porque tem
    # gate e custo próprios (uma chamada por envio).
    text_dedup_enabled: bool = False
    # Keep-alive do modelo Ollama: ping a cada 120s pra evitar que o Ollama
    # descarregue o modelo após idle (~5min). Sem isso, o 1º dedup depois de
    # alguns minutos parado paga ~2s de load — perceptível. Default off.
    keep_model_warm: bool = False
    # Glossário de termos técnicos (ex. "QI Tech, BMS, BMP"), separados por
    # vírgula e/ou quebra de linha — vira initial_prompt do Whisper na
    # transcrição de áudio, reduzindo erro em siglas/nomes próprios
    # recorrentes. Default vazio (sem hint).
    transcription_glossary: str = ""


class SettingsIn(BaseModel):
    """Atualização das configurações."""

    milestones_auto: bool
    jarvis_all: bool = False
    jarvis_full_all: bool = False
    voice_dedup_enabled: bool = False
    voice_dedup_model: str = "qwen3:0.6b"
    text_dedup_enabled: bool = False
    keep_model_warm: bool = False
    transcription_glossary: str = ""


async def read_settings(request: Request) -> SettingsOut:
    """Lê o doc de settings (default: tudo ligado)."""
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    doc = await db[settings.app_settings_collection].find_one({"_id": SETTINGS_ID})
    if not doc:
        return SettingsOut()
    return SettingsOut(
        milestones_auto=bool(doc.get("milestones_auto", True)),
        jarvis_all=bool(doc.get("jarvis_all", False)),
        jarvis_full_all=bool(doc.get("jarvis_full_all", False)),
        voice_dedup_enabled=bool(doc.get("voice_dedup_enabled", False)),
        voice_dedup_model=str(doc.get("voice_dedup_model", "qwen3:0.6b")),
        text_dedup_enabled=bool(doc.get("text_dedup_enabled", False)),
        keep_model_warm=bool(doc.get("keep_model_warm", False)),
        transcription_glossary=str(doc.get("transcription_glossary", "")),
    )


@router.get("", response_model=SettingsOut)
async def get_settings(request: Request) -> SettingsOut:
    return await read_settings(request)


@router.put("", response_model=SettingsOut)
async def put_settings(request: Request, body: SettingsIn) -> SettingsOut:
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    await db[settings.app_settings_collection].update_one(
        {"_id": SETTINGS_ID},
        {"$set": {
            "milestones_auto": body.milestones_auto,
            "jarvis_all": body.jarvis_all,
            "jarvis_full_all": body.jarvis_full_all,
            "voice_dedup_enabled": body.voice_dedup_enabled,
            "voice_dedup_model": body.voice_dedup_model,
            "text_dedup_enabled": body.text_dedup_enabled,
            "keep_model_warm": body.keep_model_warm,
            "transcription_glossary": body.transcription_glossary,
        }},
        upsert=True,
    )
    # Desligar o global = silenciar TUDO: zera o toggle por-sessão também (senão
    # sessões com jarvis=true continuam falando, já que is_enabled é um OU).
    # Mesma semântica do `/jarvis all off`.
    if not body.jarvis_all:
        await db[settings.sessions_collection].update_many(
            {"jarvis": True}, {"$set": {"jarvis": False}}
        )
    if not body.jarvis_full_all:
        await db[settings.sessions_collection].update_many(
            {"jarvis_mode": "full"}, {"$set": {"jarvis_mode": "speaker"}}
        )
    return SettingsOut(
        milestones_auto=body.milestones_auto,
        jarvis_all=body.jarvis_all,
        jarvis_full_all=body.jarvis_full_all,
        voice_dedup_enabled=body.voice_dedup_enabled,
        voice_dedup_model=body.voice_dedup_model,
        text_dedup_enabled=body.text_dedup_enabled,
        keep_model_warm=body.keep_model_warm,
        transcription_glossary=body.transcription_glossary,
    )
