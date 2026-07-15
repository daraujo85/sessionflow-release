"""Transcrição de áudio via Whisper — backend MLX (Mac) ou CUDA (outros hosts).

Dois backends, mesma interface pública (``transcribe()``):

- ``mlx`` (default no macOS): ``mlx-whisper`` (modelo
  ``mlx-community/whisper-large-v3-turbo``) — rápido no Apple Silicon, sem CUDA.
- ``faster-whisper`` (default fora do macOS, ex.: Windows/WSL2 com GPU NVIDIA):
  biblioteca CTranslate2, roda em CUDA quando disponível (``SESSIONFLOW_FASTER_WHISPER_DEVICE``,
  default ``cuda``). Modelo default ``large-v3`` — cabe folgado em GPUs com
  8GB+ de VRAM.

Cada backend baixa o modelo na 1ª vez (HF Hub) e mantém cache em memória por
processo (carregar é caro). A transcrição é CPU/GPU-bound e bloqueante, então
roda em ``loop.run_in_executor`` para não travar o event loop do worker.

Por que Whisper (e não Parakeet): o ``parakeet-mlx`` não expõe NENHUMA forma
de fixar o idioma — o modelo multilíngue auto-detecta por trecho e às vezes
"trava" em inglês, transcrevendo fala em português como inglês. O Whisper
aceita ``language="pt"``, garantindo PT-BR sempre.

Aceita qualquer formato que o ffmpeg decodifique (inclui o ``.webm`` gravado
pelo navegador).

Uso típico::

    text = await transcribe("/path/to/audio.webm")            # PT-BR (default)
    text = await transcribe("/path/to/audio.webm", language="es")

Erros:
- arquivo inexistente → :class:`FileNotFoundError` com mensagem clara.
- falha de carregamento/transcrição propaga a exceção original (o chamador
  decide como tratar; no consumer vira evento de erro).
"""

from __future__ import annotations

import asyncio
import os
import platform

# Modelo padrão por backend. MLX usa um repo HF; faster-whisper usa o nome
# "curto" do modelo Whisper (baixa do HF internamente via CTranslate2).
DEFAULT_MODEL_MLX = "mlx-community/whisper-large-v3-turbo"
DEFAULT_MODEL_FASTER_WHISPER = os.environ.get(
    "SESSIONFLOW_FASTER_WHISPER_MODEL", "large-v3"
)
FASTER_WHISPER_DEVICE = os.environ.get("SESSIONFLOW_FASTER_WHISPER_DEVICE", "cuda")
# float16 é o compute_type recomendado p/ GPU NVIDIA (metade da VRAM do
# float32, sem perda de qualidade perceptível). CPU cairia p/ int8 — não é o
# caso hoje (backend CTranslate2 só é escolhido quando há CUDA disponível).
FASTER_WHISPER_COMPUTE = os.environ.get("SESSIONFLOW_FASTER_WHISPER_COMPUTE", "float16")

# Idioma forçado por padrão. Whisper usa códigos ISO-639-1 ("pt", "es", "en"…).
# Sem isso, o auto-detect pode escorregar para inglês — origem do bug relatado.
DEFAULT_LANGUAGE = "pt"

# Override explícito (testes/operação); vazio ⇒ auto-detecta pela plataforma.
_BACKEND_OVERRIDE = os.environ.get("SESSIONFLOW_TRANSCRIBE_BACKEND", "").strip().lower()

# Cache do modelo faster-whisper carregado (chave: nome+device+compute) — é
# caro carregar, mesmo espírito do cache interno do mlx_whisper por HF repo.
_faster_whisper_models: dict[tuple[str, str, str], object] = {}


def _resolve_backend() -> str:
    """``"mlx"`` no macOS, ``"faster-whisper"`` em qualquer outro host.

    Override via ``SESSIONFLOW_TRANSCRIBE_BACKEND=mlx|faster-whisper`` (ex.:
    forçar CPU/faster-whisper num Mac pra debugar, ou o inverso).
    """
    if _BACKEND_OVERRIDE in ("mlx", "faster-whisper"):
        return _BACKEND_OVERRIDE
    return "mlx" if platform.system().lower() == "darwin" else "faster-whisper"


def _default_model_for(backend: str) -> str:
    return DEFAULT_MODEL_MLX if backend == "mlx" else DEFAULT_MODEL_FASTER_WHISPER


def _transcribe_sync_mlx(path: str, model_name: str, language: str | None) -> str:
    """Parte bloqueante do backend MLX (Apple Silicon). Roda no executor.

    Import do ``mlx_whisper`` é *lazy* (dentro da função) para que o módulo
    possa ser importado em testes/hosts sem MLX sem pagar o custo do import.
    O ``mlx_whisper`` já mantém cache interno do modelo carregado por
    ``path_or_hf_repo``.
    """
    import mlx_whisper  # import pesado (mlx); adiado

    result = mlx_whisper.transcribe(
        path,
        path_or_hf_repo=model_name,
        language=language,  # None ⇒ auto-detect; default força PT.
    )
    # mlx_whisper retorna um dict com "text".
    return str(result.get("text", "") or "").strip()


def _get_faster_whisper_model(model_name: str):
    """Carrega (ou reusa do cache) o ``WhisperModel`` CTranslate2 pedido."""
    key = (model_name, FASTER_WHISPER_DEVICE, FASTER_WHISPER_COMPUTE)
    model = _faster_whisper_models.get(key)
    if model is None:
        from faster_whisper import WhisperModel  # import pesado (torch/ctranslate2); adiado

        model = WhisperModel(
            model_name, device=FASTER_WHISPER_DEVICE, compute_type=FASTER_WHISPER_COMPUTE
        )
        _faster_whisper_models[key] = model
    return model


def _transcribe_sync_faster_whisper(
    path: str, model_name: str, language: str | None
) -> str:
    """Parte bloqueante do backend CUDA (faster-whisper/CTranslate2)."""
    model = _get_faster_whisper_model(model_name)
    segments, _info = model.transcribe(path, language=language)
    return " ".join(seg.text.strip() for seg in segments).strip()


async def transcribe(
    path: str,
    model_name: str | None = None,
    language: str | None = DEFAULT_LANGUAGE,
) -> str:
    """Transcreve o áudio em ``path`` e retorna o texto (strip()).

    Parameters
    ----------
    path:
        Caminho do arquivo de áudio (qualquer formato suportado pelo ffmpeg).
    model_name:
        Nome/repo do modelo Whisper. ``None`` (default) usa o padrão do
        backend resolvido pra esta plataforma (ver ``_resolve_backend``):
        ``mlx-community/whisper-large-v3-turbo`` no Mac, ``large-v3`` (CUDA)
        nos demais hosts.
    language:
        Código ISO-639-1 do idioma falado (``"pt"``, ``"es"``, ``"en"``…).
        Default ``"pt"`` (força PT-BR). Passe ``None`` para auto-detect.

    Raises
    ------
    FileNotFoundError
        Se o arquivo não existir (validação antecipada, mensagem clara).
    """
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"arquivo de áudio não encontrado: {path!r}")

    backend = _resolve_backend()
    resolved_model = model_name or _default_model_for(backend)
    fn = _transcribe_sync_mlx if backend == "mlx" else _transcribe_sync_faster_whisper

    loop = asyncio.get_event_loop()
    # Bloqueante: roda no executor p/ não travar o event loop.
    return await loop.run_in_executor(None, fn, path, resolved_model, language)
