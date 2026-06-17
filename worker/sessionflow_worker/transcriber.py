"""Transcrição de áudio via Parakeet (NVIDIA) na porta MLX p/ Apple Silicon.

Usa ``parakeet-mlx`` (modelo ``mlx-community/parakeet-tdt-0.6b-v3``) — rápido e
preciso no Apple Silicon, sem CUDA. O modelo é baixado do Hugging Face no
primeiro uso (cacheado em ``~/.cache/huggingface``) e mantido em cache global
por processo (carregar é caro). A transcrição é CPU/GPU-bound e bloqueante,
então roda em ``loop.run_in_executor`` para não travar o event loop do worker.

Aceita qualquer formato que o ffmpeg decodifique (inclui o ``.webm`` gravado
pelo navegador). Substitui o Whisper, que era mais lento e menos preciso.

Uso típico::

    text = await transcribe("/path/to/audio.webm")

Erros:
- arquivo inexistente → :class:`FileNotFoundError` com mensagem clara.
- falha de carregamento/transcrição propaga a exceção original (o chamador
  decide como tratar; no consumer vira evento de erro).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

# Modelo padrão (Parakeet TDT 0.6b v3, porta MLX). Multilíngue, bom em PT-BR.
DEFAULT_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"

# Cache global de modelos carregados, por nome. Carregar faz download (1ª vez) +
# init; recarregar a cada chamada seria proibitivo. Cache por processo basta
# para o worker long-running.
_MODEL_CACHE: dict[str, Any] = {}


def _load_model(model_name: str) -> Any:
    """Carrega (ou reusa do cache) um modelo Parakeet pelo nome.

    Import do ``parakeet_mlx`` é *lazy* (dentro da função) para que o módulo
    possa ser importado em testes sem pagar o custo do import (mlx etc.) quando
    a transcrição é monkeypatchada.
    """
    cached = _MODEL_CACHE.get(model_name)
    if cached is not None:
        return cached

    from parakeet_mlx import from_pretrained  # import pesado (mlx); adiado

    model = from_pretrained(model_name)
    _MODEL_CACHE[model_name] = model
    return model


def _transcribe_sync(path: str, model_name: str) -> str:
    """Parte bloqueante: carrega modelo e roda a transcrição. Roda no executor."""
    model = _load_model(model_name)
    result = model.transcribe(path)
    # parakeet-mlx retorna um objeto com ``.text``.
    return str(getattr(result, "text", "") or "").strip()


async def transcribe(path: str, model_name: str = DEFAULT_MODEL) -> str:
    """Transcreve o áudio em ``path`` e retorna o texto (strip()).

    Parameters
    ----------
    path:
        Caminho do arquivo de áudio (qualquer formato suportado pelo ffmpeg).
    model_name:
        Repo HF do modelo Parakeet (MLX). Default
        ``mlx-community/parakeet-tdt-0.6b-v3``. Cacheado globalmente por nome.

    Raises
    ------
    FileNotFoundError
        Se o arquivo não existir (validação antecipada, mensagem clara).
    """
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"arquivo de áudio não encontrado: {path!r}")

    loop = asyncio.get_event_loop()
    # Bloqueante: roda no executor p/ não travar o event loop.
    return await loop.run_in_executor(None, _transcribe_sync, path, model_name)
