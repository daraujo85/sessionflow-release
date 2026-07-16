# Ambiente SessionFlow separado para o Lucas (2 hosts Windows)

Levantamento de tudo que o Lucas precisa pra ter uma instância **própria e
independente** do SessionFlow — não compartilha nada com a stack do Diego
(Mongo/RabbitMQ/API/frontend/túnel Cloudflare são todos NOVOS, dele). Os dois
hosts dele (laptop + desktop) entram como dois "hosts" no mesmo painel, igual
ao par Mac+Windows do Diego hoje.

Baseado 100% no que foi feito e validado nesta sessão (não é teórico — cada
item abaixo já foi executado e testado de verdade no ambiente do Diego).

## Checklist de pré-requisitos (o Lucas precisa ter ANTES de começar)

### Nas duas máquinas (laptop + desktop)
- [ ] Windows 10/11 de 64 bits, com conta de **Administrador** (ou senha de
      alguém que seja) — quase todo passo abaixo exige privilégio de admin.
- [ ] Virtualização habilitada na BIOS/UEFI (exigida pelo WSL2 — geralmente
      já vem ligada, mas em desktop montado/OEM antigo às vezes não).
- [ ] Espaço em disco livre: **~15GB** por máquina sem GPU (WSL2 + Docker se
      for a máquina da stack + CLIs); **~25-30GB** na(s) máquina(s) que for
      rodar TTS/STT local (modelos Whisper/XTTS somados passam de 5GB).
- [ ] Conexão de internet estável (as instalações baixam bastante coisa:
      Docker, modelos de IA, dependências CUDA).
- [ ] **Conta paga do Claude** (Pro/Max/Team) — o login do Claude Code é
      OAuth e só ele pode fazer, em cada máquina, na hora.
- [ ] **Conta ChatGPT Plus/Pro/Team** (se for usar Codex também) — mesma
      lógica, login OAuth manual por máquina.
- [ ] Node.js instalado (dentro do WSL2 — usado pra Codex CLI e agentmemory).

### Só na máquina que vai rodar a stack (Docker) — provável o desktop
- [ ] **Docker Desktop** instalado, com o WSL2 backend habilitado (é o
      padrão hoje em dia).
- [ ] Espaço extra pro Docker (imagens/volumes do Mongo+RabbitMQ+API+
      frontend): reserva mais **~10GB**.

### Só na(s) máquina(s) com GPU (TTS/STT local)
- [ ] GPU NVIDIA com **8GB+ de VRAM** (a RTX 3060/12GB do Diego é a régua
      usada aqui).
- [ ] Driver NVIDIA atualizado (Game Ready ou Studio, tanto faz) — é ele
      que expõe a GPU pro WSL2, sem precisar instalar CUDA Toolkit à parte.

### Se quiser acesso de fora da LAN (opcional)
- [ ] Domínio próprio + conta Cloudflare (não dá pra usar a do Diego).

### Pro Diego configurar remotamente (o que este documento cobre na seção 8)
- [ ] Rodar, como Administrador, o script `sessionflow-remote-access.ps1`
      em cada máquina (habilita SSH + firewall).
- [ ] Criar uma conta grátis no ngrok (ou Cloudflare, se preferir) e rodar 2
      comandos — o script já deixa isso pronto, só falta esse passo manual.
- [ ] Mandar de volta: endereço do túnel + usuário Windows + senha (só
      enquanto durar a configuração — dá pra revogar/trocar depois).

## 0. Decisão prévia: onde roda a stack (Mongo/RabbitMQ/API/frontend)

O `docker-compose.yml` (mongo, rabbitmq, api, frontend) precisa rodar em **UMA
máquina só** — o candidato natural é o **desktop** (mais provável de ficar
sempre ligado). Os workers (laptop e desktop) se conectam nela.

- Precisa de Docker Desktop instalado no desktop.
- Requer WSL2 no Windows (o Docker Desktop já pede isso).

## 1. Clonar o repo e gerar segredos PRÓPRIOS

**Nunca reaproveitar os segredos do Diego.** Gerar do zero:

- `MONGO_ROOT_PASSWORD` / `MONGO_APP_PASSWORD` — senhas novas (aleatórias).
- `RABBITMQ_DEFAULT_PASS` — senha nova.
- `SESSIONFLOW_JWT_SECRET` — novo (ex.: `openssl rand -hex 32`).
- `SESSIONFLOW_PASSWORD` / `SESSIONFLOW_EMAIL` — login do Lucas no app.
- `SESSIONFLOW_VAPID_PUBLIC`/`PRIVATE`/`SUBJECT` — gerar par novo (web-push
  exige chaves únicas por app; há gerador online ou `npx web-push
  generate-vapid-keys`).
- Copiar `.env.example`/`.env` do repo do Diego só como MODELO de quais
  variáveis existem — sem copiar nenhum valor real.

## 2. Subir a stack Docker (no desktop)

```bash
docker compose up -d                       # mongo + rabbitmq
docker compose --profile app up -d --build # api + frontend
```

`docker-compose.yml` já publica Mongo (27017) e RabbitMQ (5672) em `0.0.0.0`
(não só `127.0.0.1`) — é isso que permite o worker do LAPTOP (outra máquina)
alcançar essa stack pela rede local. `api`/`frontend`/mgmt UI do RabbitMQ
continuam só em `127.0.0.1` (não precisam ser expostos na LAN).

## 3. Rede — acesso de fora da LAN (opcional, só se precisar)

Se o Lucas quiser usar o app fora de casa/escritório (celular no 4G, notebook
em outro lugar), precisa de:

- Conta Cloudflare própria + um domínio (não dá pra usar
  `boletoazap.dev.br`, é do Diego).
- `cloudflared` rodando como container (`docker run cloudflare/cloudflared ...`
  conectado à mesma rede docker) com ingress pro frontend (`:4200`) e API
  (`:8000`) — replica o padrão do túnel `macbook` já usado aqui.
- Se o LAPTOP não estiver na mesma LAN do desktop em algum momento (ex.: fora
  de casa), o worker do laptop também precisa de um túnel TCP cru
  (Mongo 27017 + RabbitMQ 5672) — replica o padrão do túnel `duck-server`
  (`cloudflared access tcp` do lado do worker fazendo de proxy local).
- Se os dois hosts do Lucas estiverem **sempre na mesma LAN**, pode pular
  esta seção inteira e usar só os IPs locais — bem mais simples.

## 4. Por host Windows (repetir para LAPTOP e DESKTOP)

Cada um vira um "host" no SessionFlow (`host_id` próprio, gerado sozinho no
1º boot do worker).

### 4.1. WSL2

- Instalar WSL2 + distro Ubuntu (`wsl --install -d Ubuntu`, ou já vem com o
  Docker Desktop se for o desktop).
- Habilitar systemd: `/etc/wsl.conf` →
  ```ini
  [boot]
  systemd=true
  ```
- Se o worker precisar sobreviver ao logon (não é criado por padrão): tarefa
  agendada do Windows (`schtasks`) rodando `wsl.exe -d Ubuntu` no logon do
  usuário, já que o WSL2 não sobe sozinho com o Windows.

### 4.2. Usuário Linux — **NÃO usar root**

Achado importante desta sessão: o Claude Code **recusa** rodar em
`--permission-mode bypassPermissions` como root (trava de segurança do
próprio CLI). Criar um usuário comum de propósito:

```bash
useradd -m -s /bin/bash lucas
usermod -aG sudo lucas
```

Todo o resto (worker, tmux, CLIs) roda como esse usuário, não root.

### 4.3. Dependências de sistema (`apt`)

```bash
apt-get update
apt-get install -y tmux ffmpeg curl git
```

### 4.4. `uv` (gerenciador Python do worker) — como o usuário `lucas`

```bash
su - lucas -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
```

### 4.5. Código do worker

Copiar (ou clonar, se o Lucas tiver acesso ao repo) só a pasta `worker/` pra
`/home/lucas/sessionflow/worker`, e um `.env` na raiz (`/home/lucas/sessionflow/.env`)
com:

```bash
MONGO_URI_HOST=mongodb://<user>:<senha>@<ip-do-desktop>:27017/sessionflow?authSource=sessionflow
RABBITMQ_URI_HOST=amqp://<user>:<senha>@<ip-do-desktop>:5672/
```
(trocar `<ip-do-desktop>` pelo IP da LAN do desktop, ou `127.0.0.1` se for o
próprio desktop que está rodando o worker também.)

Autocomplete de diretório (se os repos de projeto não ficarem em
`~/Documents/projects` como no Mac):
```bash
SESSIONFLOW_SCAN_ROOTS=/mnt/c/repo
```

### 4.6. Serviço systemd do worker

```ini
[Unit]
Description=SessionFlow Worker (multi-host)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/lucas/sessionflow/worker
ExecStart=/home/lucas/.local/bin/uv run python -m sessionflow_worker
Restart=always
RestartSec=5
User=lucas
Environment=HOME=/home/lucas
# CRÍTICO: sem isso, "systemctl restart" mata o cgroup inteiro — incluindo
# o servidor tmux e TODAS as sessões reais rodando nele (achado desta sessão).
KillMode=process

[Install]
WantedBy=multi-user.target
```

```bash
cp sessionflow-worker.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now sessionflow-worker
```

### 4.7. Agentes de CLI (por host, como o usuário `lucas`)

- **Claude Code** (nativo, não npm):
  ```bash
  su - lucas -c 'curl -fsSL https://claude.ai/install.sh | bash'
  ```
  Login: **interativo, só o Lucas pode fazer** (abre uma sessão de terminal
  nessa máquina — ou usa a feature "abrir no Mac"/terminal remoto do próprio
  SessionFlow uma vez que o front estiver de pé — e loga com a conta Claude
  dele). Primeira execução também pede: tema, aceitar bypass-permissions,
  confiar na pasta — tudo isso é 1x por host.

- **Codex CLI** (via npm, instala globalmente pro sistema):
  ```bash
  npm install -g @openai/codex
  ```
  Login: também interativo (OAuth ChatGPT) na primeira execução.

- (Opcional) **Gemini CLI** / **OpenCode**, se o Lucas usar — mesma lógica,
  instalar + logar 1x por host.

### 4.8. Code-intel stack (rtk + Graphify + agentmemory) — opcional mas recomendado

Só faz sentido se o Lucas quiser os mesmos ganhos de token que o Diego usa.
Pra cada host:

- **rtk**: baixar o `.deb` da release mais recente
  (`https://github.com/rtk-ai/rtk/releases`) e `dpkg -i` (fica em
  `/usr/bin/rtk`, disponível pra qualquer usuário). Hook do Claude Code
  (`~/.claude/settings.json` → `PreToolUse`/`Bash` chamando um
  `rtk-rewrite.sh`) + instrução em `~/.codex/AGENTS.md`→`RTK.md` pro Codex
  (que não suporta hooks, só instrução).
- **Graphify**: `uv tool install graphifyy` (fica em `~/.local/bin/graphify`
  do usuário `lucas`) + hook `SessionStart` do Claude Code que roda
  `graphify extract . --code-only` automaticamente em projeto novo.
- **agentmemory**: `npm install -g @agentmemory/agentmemory` (global,
  `/usr/bin/agentmemory`) rodando como serviço systemd próprio (`ExecStart=
  /usr/bin/agentmemory`, porta 3111) + registrado como MCP tanto em
  `~/.claude.json` quanto em `~/.codex/config.toml` (seção
  `[mcp_servers.agentmemory]`), apontando pra `http://localhost:3111`.
- `~/.claude/CLAUDE.md` com as instruções de uso (quando usar grep vs
  Graphify vs agentmemory) — arquivo de texto, só copiar/adaptar.

## 5. GPU (TTS/STT locais) — se as GPUs derem 8GB+ de VRAM

Como as duas máquinas do Lucas têm GPU "na linha" da do Diego (RTX 3060,
12GB), dá pra ligar voz (JARVIS) e transcrição de áudio **sem depender de
API externa**, em qualquer um dos dois hosts (ou nos dois).

### 5.1. STT (transcrição) — `faster-whisper`, já é dependência do worker

Só precisa instalar o runtime CUDA que falta (o worker já traz
`faster-whisper` no `pyproject.toml`):
```bash
cd /home/lucas/sessionflow/worker
uv pip install --python .venv/bin/python nvidia-cublas-cu12 nvidia-cudnn-cu12
```

### 5.2. TTS (voz) — servidor XTTS-v2 próprio (CUDA)

```bash
su - lucas -c 'uv venv --python 3.12 ~/.claude/hooks/tts-venv'
su - lucas -c 'uv pip install --python ~/.claude/hooks/tts-venv/bin/python "coqui-tts==0.25.3" torch click'
```
Copiar `xtts_server.py` (script simples, ~160 linhas, já existe no ambiente
do Diego em `~/.claude/hooks/xtts_server.py` — pedir pra ele passar o
arquivo) pra `/home/lucas/.claude/hooks/xtts_server.py`. Serviço systemd:
```ini
[Service]
Environment=CLAUDE_TTS_DEVICE=cuda
Environment=COQUI_TOS_AGREED=1
ExecStart=/home/lucas/.claude/hooks/tts-venv/bin/python /home/lucas/.claude/hooks/xtts_server.py
User=lucas
Restart=always
```
(porta padrão 5111 — primeira subida baixa o modelo, ~1.9GB, do Hugging
Face.)

### 5.3. Ligar as capabilities

No `.env` do worker desse host:
```bash
SESSIONFLOW_HOST_TTS=1
SESSIONFLOW_HOST_TRANSCRIPTION=1
SESSIONFLOW_FASTER_WHISPER_DEVICE=cuda
```

## 6. "Abrir no Mac"-equivalente pro Lucas

A feature de abrir sessão remota num terminal local (hoje só funciona pro
Mac — usa `osascript`/Terminal.app) **não existe pra Windows** ainda no
código — o Lucas não vai ter esse botão em nenhuma das duas máquinas dele a
não ser que a gente generalize essa parte (hoje é código Mac-only). Não é
bloqueante — sessões continuam funcionando normal via app, só falta esse
atalho específico.

## 7. Checklist de validação (por host)

- [ ] `systemctl status sessionflow-worker` → active/running
- [ ] `GET /workers` mostra o host novo, `online: true`
- [ ] Criar sessão Claude nesse host pelo app → status vira `running` de
      verdade (não `detached`)
- [ ] Criar sessão Codex nesse host → idem
- [ ] (se GPU) `curl localhost:5111/health` → `ok`; transcrever um áudio de
      teste funciona
- [ ] `agentmemory`: `curl localhost:3111/agentmemory/health` → `healthy`

## 8. Acesso remoto do Diego pra configurar tudo isso pelo Lucas

Em vez do Lucas seguir esse guia sozinho, o mais prático é o Diego configurar
remotamente (via SSH) — igual foi feito com o host Windows de teste nesta
sessão. Script: `sessionflow-remote-access.ps1` (enviado ao Lucas por
WhatsApp), rodado como Administrador em CADA máquina:

1. Habilita o OpenSSH Server do Windows (feature nativa, vem desligada).
2. Deixa o serviço `sshd` ligando sozinho + inicia agora.
3. Libera a porta 22 no firewall do Windows.
4. Baixa o `ngrok` — cria um túnel temporário de fora pra dentro, sem
   precisar mexer no roteador/modem (a porta 22 continua só acessível na
   LAN dele até o túnel subir).

Depois de rodar o script, faltam 2 passos manuais do Lucas (1x só, ~1 min):
criar conta grátis no ngrok, rodar `ngrok.exe config add-authtoken ...` e
`ngrok.exe tcp 22`, e mandar pro Diego o endereço que aparece
(`tcp://X.tcp.ngrok.io:PORTA`) + usuário Windows + senha.

Com isso o Diego conecta via `ssh usuario@X.tcp.ngrok.io -p PORTA` e segue o
resto deste guia (seções 4-6) remotamente, como fez com o host de teste.
**Ao terminar, o Lucas fecha a janela do ngrok** — o túnel cai na hora, nada
fica exposto de fora depois disso (o SSH em si só continua acessível dentro
da rede dele, como qualquer outro serviço da LAN).

## Notas / pegadinhas já vividas nesta sessão (evita retrabalho)

- **Nunca rodar o worker como root** se quiser usar Claude Code de verdade.
- **`KillMode=process`** no systemd do worker é obrigatório — o default
  (`control-group`) mata o tmux (e sessões reais) a cada deploy/restart.
- CLIs instalados só do lado Windows "puro" (fora do WSL2) **não contam** —
  precisa instalar de novo de dentro do WSL2 (ambientes são isolados).
- Comandos SSH/scripts remotos: o shell padrão do OpenSSH no Windows é
  `cmd.exe`, não PowerShell nem bash — cuidado com aspas/escaping ao
  automatizar (prefira sempre um arquivo de script copiado, não `-c` inline).
