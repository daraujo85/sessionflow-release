"""SessionFlow Worker package."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

__version__ = "0.1.0"

# Carrega o `.env` da raiz do repo ANTES de qualquer submódulo (jarvis,
# transcriber, mongo, ...) ser importado — vários deles leem env var em
# constante de MÓDULO (ex.: `TTS_MODE = os.environ.get(...)` em jarvis.py),
# avaliada uma única vez, no import. Sem carregar aqui (no __init__ do
# pacote, que roda primeiro que tudo), essas constantes ficavam presas no
# valor default do processo, e o `.env` só chegava a valer bem mais tarde
# (dentro de runner.load_env()/mongo._load_env(), chamadas em runtime) —
# tarde demais pra afetar quem já leu a env no import.
_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
if _ROOT_ENV.exists():
    load_dotenv(_ROOT_ENV, override=False)
