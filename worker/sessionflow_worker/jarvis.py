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

- ``SESSIONFLOW_JARVIS_TTS``      = ``say`` (default) | ``api``
- ``SESSIONFLOW_JARVIS_SUMMARY``  = ``ollama`` (default) | ``api`` | ``none``
- ``SESSIONFLOW_JARVIS_VOICE``    = voz do ``say`` (default ``Luciana``) ou voz
  Azure quando ``TTS=api`` (ex. ``pt-BR-AntonioNeural``)
- ``SESSIONFLOW_JARVIS_OLLAMA``   = base do Ollama (default ``http://localhost:11434``)
- ``SESSIONFLOW_JARVIS_OLLAMA_MODEL`` = modelo (default ``llama3.2:3b``)
- ``SESSIONFLOW_JARVIS_RATE``     = rate do ``say`` (default ``190``)
- ``SESSIONFLOW_TTS_BASE``        = base da API hospedada (quando ``=api``)

Tudo é **best-effort**: qualquer falha é engolida e jamais derruba o discovery.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import subprocess
import tempfile
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
TTS_BASE_URL = os.environ.get("SESSIONFLOW_TTS_BASE", "https://audio.boletoazap.dev.br").rstrip("/")
# Voz Azure quando TTS=api (a voz `say` não vale lá).
API_VOICE = os.environ.get("SESSIONFLOW_JARVIS_API_VOICE", "pt-BR-AntonioNeural")

_SCREEN_TAIL = 2500  # chars da cauda da tela enviados ao resumo.
_HTTP_TIMEOUT = 25
APP_SETTINGS_ID = "app"
APP_SETTINGS_COLLECTION = os.environ.get("SESSIONFLOW_APP_SETTINGS_COLLECTION", "app_settings")

_SUMMARY_SYS = (
    "Voce gera um texto curto que sera LIDO EM VOZ ALTA por um sintetizador de "
    "voz (TTS) em portugues do Brasil. Escreva como fala humana natural, do "
    "jeito que uma pessoa contaria rapidinho o que aconteceu. Seja BEM curto: "
    "uma ou no maximo duas frases curtas e diretas. NAO use nenhum simbolo nem "
    "marcacao: nada de asteriscos, crases, "
    "hashtags, colchetes, parenteses, barras, setas, marcadores, emojis, URLs, "
    "caminhos de arquivo, nomes de variaveis ou trechos de codigo. NAO leia nem "
    "soletre simbolos ou pontuacao (nunca diga a palavra 'ponto'). Use no maximo "
    "virgulas e um ponto final por frase, como na escrita normal. Diga o que o "
    "agente fez e, se estiver esperando, qual decisao a pessoa precisa tomar. "
    "Responda APENAS com a frase falada, sem aspas."
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
    t = _DROP_RE.sub(" ", t)
    # Colapsa QUALQUER sequência de pontuação (incl. "...", ". .", ".,") num
    # único ponto+espaço — senão o XTTS lê a pontuação solta como "ponto, ponto".
    t = re.sub(r"[.,;:!?](?:\s*[.,;:!?])+", ". ", t)
    t = re.sub(r"\s+([.,!?;:])", r"\1", t)  # espaço antes de pontuação
    t = re.sub(r"\s+", " ", t).strip()
    t = t.strip(" .,:;-—–")  # remove pontuação/sobras nas EXTREMIDADES
    return t


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    system: str, prompt: str, num_predict: int = 70, temperature: float = 0.3
) -> str:
    out = _post_json(
        f"{OLLAMA_BASE}/api/generate",
        {
            "model": OLLAMA_MODEL,
            "system": system,
            "prompt": prompt,
            "stream": False,
            # num_predict baixo força brevidade (resumo curto = fala xtts rápida).
            "options": {"temperature": temperature, "num_predict": num_predict},
        },
        timeout=40,
    )
    return (out.get("response") or "").strip()


def _summary_ollama_sync(prompt: str) -> str:
    return _ollama_sync(_SUMMARY_SYS, prompt)


def _summary_api_sync(prompt: str) -> str:
    out = _post_form("/ai", {"text": f"{_SUMMARY_SYS}\n\n{prompt}", "sanitize": "false"})
    return (out.get("text_output") or "").strip()


async def _summary(screen_text: str, title: str, desc: str) -> str:
    """Resumo curto da tela. Fallback para ``desc``/``title`` em qualquer falha."""
    fallback = desc or title
    tail = (screen_text or "").strip()[-_SCREEN_TAIL:]
    if not tail or SUMMARY_MODE == "none":
        return fallback
    prompt = f"Contexto: {title}.\n\nConteudo da tela:\n\n{tail}\n\nResumo falado:"
    fn = _summary_api_sync if SUMMARY_MODE == "api" else _summary_ollama_sync
    try:
        loop = asyncio.get_running_loop()
        out = await loop.run_in_executor(None, fn, prompt)
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


def _synth_api_sync(text: str) -> tuple[str, str] | None:
    out = _post_form("/tts", {"text": text, "voice": API_VOICE, "convert_to_ogg": "true"})
    b64 = out.get("audio_base64")
    if not b64:
        return None
    return b64, (out.get("audio_mime") or "audio/ogg")


async def _synth(text: str) -> tuple[str, str] | None:
    loop = asyncio.get_running_loop()
    try:
        if TTS_MODE == "api":
            return await loop.run_in_executor(None, _synth_api_sync, text)
        if TTS_MODE == "xtts":
            # Tenta a voz boa (xtts); cai p/ `say` se o servidor estiver fora.
            try:
                r = await loop.run_in_executor(None, _synth_xtts_sync, text)
                if r:
                    return r
            except Exception:  # noqa: BLE001 - fallback gracioso
                logger.debug("jarvis: xtts falhou; caindo p/ say", exc_info=True)
            return await loop.run_in_executor(None, _synth_say_sync, text)
        return await loop.run_in_executor(None, _synth_say_sync, text)
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("jarvis: sintese (%s) falhou", TTS_MODE, exc_info=True)
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
        # Anuncia a sessão no INÍCIO, com um nome FALÁVEL gerado pelo modelo
        # (ex.: "sessionflow" → "session flow"), pausa, e então o resumo — assim,
        # com várias sessões falando, dá pra saber de quem é. O ponto é interno
        # → vira pausa no TTS (não a palavra "ponto").
        label = await _speakable_name(name)
        spoken = _clean_for_speech(f"Sessão {label}. {summary}" if label else summary)
        audio = await _synth(spoken)
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
