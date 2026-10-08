"""Entry-point do Worker: ``uv run python -m sessionflow_worker``.

Delega para :func:`sessionflow_worker.runner.main`, que monta e roda o daemon
(Discovery + CommandConsumer + dir_scanner + captura de output) com shutdown
gracioso e reconexão por backoff.
"""

from __future__ import annotations

from sessionflow_worker.runner import main

if __name__ == "__main__":
    main()
