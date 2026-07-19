"""Configurações gerais do app (single-user) — coleção ``app_settings``.

Hoje guarda só ``milestones_auto`` (instruir as sessões a trabalhar em
tarefas/marcos automaticamente ao abrir/criar). Doc único ``_id="app"``.
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
        'formato {"milestones":[{"id":"<kebab>","title":"<curto>",'
        '"status":"todo|doing|blocked|done"}]}, criando e atualizando o status '
        "conforme avança. O SessionFlow lê esse arquivo para mostrar suas tarefas. "
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


class SettingsOut(BaseModel):
    """Configurações expostas ao app."""

    milestones_auto: bool = True
    # JARVIS (voz) ligado para TODAS as sessões (atalho global; o liga/desliga
    # por-sessão fica no doc da sessão). Default off — fala só onde pedido.
    jarvis_all: bool = False


class SettingsIn(BaseModel):
    """Atualização das configurações."""

    milestones_auto: bool
    jarvis_all: bool = False


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
    return SettingsOut(
        milestones_auto=body.milestones_auto, jarvis_all=body.jarvis_all
    )
