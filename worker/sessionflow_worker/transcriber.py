"""Transcrição de áudio via Whisper na porta MLX (Apple Silicon).

Usa ``mlx-whisper`` (modelo ``mlx-community/whisper-large-v3-turbo``) — rápido no
Apple Silicon, sem CUDA. O modelo é baixado do Hugging Face no primeiro uso
(cacheado em ``~/.cache/huggingface``) e mantido em cache global por processo
(carregar é caro). A transcrição é CPU/GPU-bound e bloqueante, então roda em
``loop.run_in_executor`` para não travar o event loop do worker.

Por que Whisper de novo (e não Parakeet): o ``parakeet-mlx`` não expõe NENHUMA
forma de fixar o idioma — o modelo multilíngue auto-detecta por trecho e às
vezes "trava" em inglês, transcrevendo fala em português como inglês. O Whisper
aceita ``language="pt"``, garantindo PT-BR sempre. O ``turbo`` mantém a latência
parecida (~2-4s no M-series) com acurácia alta em PT-BR.

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

# Modelo padrão (Whisper large-v3 turbo, porta MLX). Multilíngue, ótimo em PT-BR
# e rápido no Apple Silicon.
DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"

# Idioma forçado por padrão. Whisper usa códigos ISO-639-1 ("pt", "es", "en"…).
# Sem isso, o auto-detect pode escorregar para inglês — origem do bug relatado.
DEFAULT_LANGUAGE = "pt"


def _transcribe_sync(path: str, model_name: str, language: str | None) -> str:
    """Parte bloqueante: roda a transcrição. Roda no executor.

    Import do ``mlx_whisper`` é *lazy* (dentro da função) para que o módulo
    possa ser importado em testes sem pagar o custo do import (mlx etc.) quando
    a transcrição é monkeypatchada. O ``mlx_whisper`` já mantém cache interno
    do modelo carregado por ``path_or_hf_repo``.
    """
    import mlx_whisper  # import pesado (mlx); adiado

    result = mlx_whisper.transcribe(
        path,
        path_or_hf_repo=model_name,
        language=language,  # None ⇒ auto-detect; default força PT.
    )
    # mlx_whisper retorna um dict com "text".
    return str(result.get("text", "") or "").strip()


async def transcribe(
    path: str,
    model_name: str = DEFAULT_MODEL,
    language: str | None = DEFAULT_LANGUAGE,
) -> str:
    """Transcreve o áudio em ``path`` e retorna o texto (strip()).

    Parameters
    ----------
    path:
        Caminho do arquivo de áudio (qualquer formato suportado pelo ffmpeg).
    model_name:
        Repo HF do modelo Whisper (MLX). Default
        ``mlx-community/whisper-large-v3-turbo``.
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

    loop = asyncio.get_event_loop()
    # Bloqueante: roda no executor p/ não travar o event loop.
    return await loop.run_in_executor(
        None, _transcribe_sync, path, model_name, language
    )
