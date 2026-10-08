# Portabilidade

> Estado atual: o SessionFlow é desenvolvido e roda em **macOS + Apple Silicon**.
> O núcleo é portável, mas algumas features (voz) e conveniências (abrir no Mac,
> auto-start) são amarradas ao macOS. Este doc mapeia o que quebra fora do Mac e
> o que seria preciso pra rodar em Linux/Windows(WSL2) — caso um dia valha a pena.

## TL;DR

| Camada | macOS | Linux | Windows + WSL2 |
|---|---|---|---|
| Docker (api / frontend / mongo / rabbit / cloudflared) | ✅ | ✅ | ✅ |
| Mirror + controle de sessão (tmux + worker + agente) | ✅ | ✅ | ✅ *se tudo rodar DENTRO do WSL* |
| Transcrição de áudio (`mlx-whisper`) | ✅ | ❌ | ❌ |
| Voz / JARVIS (`say` / XTTS) | ✅ | ⚠️ | ⚠️ |
| "Abrir no Mac" (`osascript` + Terminal.app) | ✅ | ❌ | ❌ |
| Auto-start do worker (`launchd`) | ✅ | ❌ (usar systemd) | ❌ (usar systemd no WSL) |

**Resumo:** o loop principal (espelhar terminal, mandar texto/anexo, tarefas,
notificações) é portável. Os bloqueadores reais fora do Mac são **transcrição
de áudio (MLX)** e, em menor grau, **a voz**.

## O que é amarrado ao macOS / Apple Silicon

### 1. Transcrição de áudio — `mlx-whisper` (bloqueador duro)
- `worker/sessionflow_worker/transcriber.py` usa `mlx-whisper`, que depende do
  **MLX (framework da Apple, só Apple Silicon)**. Não existe em Linux/Windows.
- **Pra portar:** trocar o backend por algo cross-platform — `faster-whisper`
  (CTranslate2, roda em CPU/CUDA) ou `whisper.cpp`. A API pública `transcribe(path,
  language=...)` já isola isso; bastaria reimplementar `_transcribe_sync`.

### 2. Voz / JARVIS — `say` (macOS) + XTTS local
- O fallback de TTS é o comando `say` do macOS (não existe em Linux).
- O servidor XTTS (`~/.claude/hooks/xtts_server.py`, fora do repo) está montado
  pro Mac e roda na CPU local.
- **Pra portar:** usar Piper/Coqui em Linux (XTTS roda, mas é pesado), ou apontar
  `SESSIONFLOW_JARVIS_TTS=api` pra usar a API hospedada (`audio.boletoazap…`).
  Sem isso, a leitura por voz fica indisponível.

### 3. "Abrir no Mac" — AppleScript (não aplicável fora do Mac)
- `worker/sessionflow_worker/command_consumer.py::_handle_open_terminal` usa
  `osascript` pra abrir o Terminal.app e dar `tmux attach`. É 100% macOS.
- **Pra portar:** sem equivalente direto; no Linux abriria um terminal via
  `x-terminal-emulator`/`gnome-terminal`, no Windows via `wt.exe`. Baixo valor —
  provavelmente vira no-op fora do Mac.

### 4. Auto-start do worker — `launchd`
- O worker roda no host via `~/Library/LaunchAgents/dev.sessionflow.worker.plist`
  (RunAtLoad + KeepAlive). `launchd` é só macOS.
- **Pra portar:** um unit do **systemd** (Linux / WSL2 com systemd habilitado) com
  `Restart=always`. O worker em si é só `python -m sessionflow_worker`.
- Mesmo vale pro XTTS (`dev.jarvis.xtts.plist`) e pro Ollama.

### 5. tmux precisa estar no mesmo SO do worker e do agente
- O worker usa `libtmux` + `tmux` CLI e o agente (Claude Code) roda numa sessão
  tmux. No **Windows isso só existe dentro do WSL** — então worker + tmux +
  agente teriam que rodar todos dentro do WSL (Linux), não no Windows nativo.
- O scroll do histórico (botões ▲▼) manda evento de roda de mouse SGR via
  `tmux send-keys -H` — isso é portável (depende do agente aceitar mouse, não do SO).

### 6. Suposições de caminho
- `HOST_UPLOADS_DIR` e os `work_dir` assumem layout Unix (`~/Documents/projects/…`).
  No WSL viram `/home/<user>/…` ou `/mnt/c/…`. Ajuste de config, não de código.

## Se um dia for portar (ordem sugerida)
1. Trocar `mlx-whisper` por `faster-whisper` (destrava áudio fora do Mac).
2. Voz: `SESSIONFLOW_JARVIS_TTS=api` ou Piper local.
3. Auto-start: unit systemd em vez do plist launchd.
4. "Abrir no Mac": esconder/no-op fora do macOS (ou integrar `wt.exe`/`gnome-terminal`).
5. Documentar o setup WSL2 (tudo dentro do WSL: tmux, worker, agente).

## Notas
- Os serviços em Docker (`docker-compose.yml`) já são cross-platform.
- O Cloudflare tunnel roda em container — também portável.
- O frontend (Angular PWA) é cross-platform por definição.
