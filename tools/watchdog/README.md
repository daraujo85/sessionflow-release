# SessionFlow API Watchdog (Auto-Healing)

Monitor leve de auto-recuperação para o container `sessionflow-api`.

---

## 1. O que ele faz

* Executa a cada **60 segundos** em background.
* Faz uma requisição HTTP rápida em `http://127.0.0.1:8000/health` (timeout estrito de 5s).
* Se a API responder normalmente (`200 OK`), limpa qualquer falha acumulada e encerra (consumo insignificante de CPU, ~10ms).
* Se a API travar (deadlock de event loop, queda/desconexão do RabbitMQ sem crash do processo) e falhar por **2 checagens consecutivas** (~2 minutos):
  * Executa automaticamente `docker restart sessionflow-api`.
  * Registra o evento no arquivo de log.

---

## 2. Instalação Rápida (1 Comando)

Na raiz do repositório SessionFlow, execute:

```bash
./tools/watchdog/install-watchdog.sh
```

O script detecta o sistema operacional:
* **No macOS**: Instala em `~/.local/bin/sessionflow-watchdog.sh`, cria e carrega o LaunchAgent `com.sessionflow.api-watchdog.plist` no `launchd`.
* **No Linux**: Instala em `~/.local/bin/` e registra um timer no `systemd --user` (ou gera linha de crontab).

---

## 3. Logs e Monitoramento

Para acompanhar os logs de verificação e eventuais reinícios:

```bash
# No macOS:
tail -f ~/Library/Logs/sessionflow-watchdog.log

# No Linux:
tail -f /tmp/sessionflow-watchdog.log
# ou via journalctl se usando systemd:
journalctl --user -u sessionflow-watchdog.service -f
```

---

## 4. Desinstalação

Caso queira remover o monitor:

```bash
# No macOS:
launchctl unload ~/Library/LaunchAgents/com.sessionflow.api-watchdog.plist
rm -f ~/Library/LaunchAgents/com.sessionflow.api-watchdog.plist
rm -f ~/.local/bin/sessionflow-watchdog.sh

# No Linux:
systemctl --user disable --now sessionflow-watchdog.timer
rm -f ~/.config/systemd/user/sessionflow-watchdog.*
```
