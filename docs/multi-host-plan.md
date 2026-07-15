# Plano: múltiplos hosts (Mac + Windows/WSL2)

> Status (2026-07-15): **TODAS as 4 fases implementadas e validadas**, mais
> alguns retoques pedidos pelo Diego depois de usar na prática: nome de host
> editável, worker do Windows como serviço persistente (sempre on), badge de
> host nas Tarefas (não só nos cards de sessão), e — achado importante —
> **o autocomplete de diretório também precisava ser escopado por host**
> (mesmo bug de índice único que afetou `sessions`, agora corrigido em
> `host_directories`). Ver também [`PORTABILITY.md`](../PORTABILITY.md) —
> aquele doc cobre "rodar o worker fora do Mac" (1 host por vez); este cobre
> "vários hosts ativos ao mesmo tempo, cada um com suas sessões".

## Objetivo

Ter sessões do Mac e sessões de outra máquina (Windows via WSL2, ou outro
Mac/Linux) coexistindo no mesmo SessionFlow — cada worker só enxergando/
controlando as sessões do seu próprio host, sem features incompatíveis
(TTS, transcrição MLX, "abrir no Mac") aparecendo pra quem não tem suporte.

## Por que não era trivial (achados) — todos com solução implementada

1. ✅ **RESOLVIDO** — Fila de comandos era única e global
   (`worker/sessionflow_worker/rabbit.py`). Todo comando (`key`, `input`,
   `resize`, `scroll`, etc.) caía na mesma fila `COMMANDS_QUEUE`; dois workers
   consumindo dela fariam o RabbitMQ round-robin entre eles. **Solução:**
   `rabbit.commands_queue_name(host_id)` — cada worker declara/consome só
   `sessionflow.commands.<host_id>`; a API (`command_publisher.py`) resolve
   o `host_id` da sessão alvo (ou do worker mais recente, pra `create`) e
   publica na routing key certa. Testado (`test_rabbit.py`,
   `test_sessions_*.py`) e validado em produção (fila do Mac com 1
   consumidor, 0 mensagens paradas).
2. ✅ **RESOLVIDO** — Status de worker era singleton (`_id="worker"` fixo em
   `worker_status`). **Solução:** `heartbeat_loop` agora usa `_id=host_id`;
   `GET /worker` (retrocompat, pega o mais recente) e `GET /workers` (lista
   todos) na API. Validado: `GET /workers` já mostra o Mac (online) + o doc
   antigo `_id="worker"` (agora offline, órfão — inofensivo).
3. ✅ **RESOLVIDO** — Stack só ouvia em `127.0.0.1`. **Solução:** duas
   opções, ambas testadas de ponta a ponta (ver seção "Rede" abaixo) — LAN
   direta (`docker-compose.yml` com Mongo/Rabbit em `0.0.0.0`) e túnel
   Cloudflare TCP (`mongo-sessionflow`/`rabbitmq-sessionflow.boletoazap.dev.br`),
   pra quando o host remoto não estiver na mesma rede.
4. ✅ **RESOLVIDO** — Sessão não tinha noção de host; `tmux_name` sem
   garantia de unicidade entre hosts. **Solução:** campo `host_id` no doc de
   sessão (estampado em `_handle_create`/`discovery._upsert_session`), índice
   único trocado de `tmux_name` sozinho pra `(host_id, tmux_name)` composto
   (`mongo.py::ensure_indexes`), e backfill automático no boot do worker
   (`_backfill_legacy_host_id`) — rodou em produção: **3743 sessões
   migradas, 0 sem host_id**. Teste novo (`test_unique_index_allows_same_tmux_name_different_hosts`)
   prova que dois hosts podem ter sessão ativa com mesmo nome sem colidir.
5. **Constatação, não requer código aqui** — tmux não roda nativo no
   Windows; "sessão Windows" = worker dentro do WSL2. Já documentado no
   `PORTABILITY.md`; confirmado na prática nos testes de conectividade
   (a máquina de teste já tinha WSL2 + Ubuntu prontos).
6. 🔶 **PARCIAL** — Features mac-only (JARVIS/TTS, transcrição MLX, "abrir
   no Mac"). O worker já calcula e publica `capabilities` por host
   (`host_identity.py::capabilities_for`, incluído no heartbeat) — falta só
   o **gate no frontend** (Fase 3, ainda não feita) pra esconder os botões
   correspondentes quando o host da sessão não suporta.

### Achados NOVOS, descobertos durante a implementação (não estavam no
### mapeamento original — todos já corrigidos)

7. ✅ **RESOLVIDO (crítico)** — `Discovery._mark_missing_stopped`
   (`discovery.py`) consultava Mongo por status ativo SEM filtrar por host e
   marcava como `stopped` tudo que não estivesse no tmux LOCAL — um segundo
   worker (Windows) teria marcado como parada TODA sessão ativa do Mac (elas
   nunca aparecem no tmux dele). **Solução:** filtro `host_id` adicionado à
   query. Esse era o bug mais perigoso encontrado — silencioso e destrutivo.
8. ✅ **RESOLVIDO** — `runner._capturable_sessions` também listava sessões
   ativas de QUALQUER host (mitigado por `runtime.has_session`, mas
   vulnerável a colisão de nome entre hosts). Filtro `host_id` adicionado.
9. ✅ **RESOLVIDO** — `runner.milestones_loop` sincronizava marcos de
   sessões de qualquer host (inofensivo — falha silenciosa ao ler `work_dir`
   de outro host — mas gastava ciclo à toa). Filtro `host_id` adicionado.
10. **Nota, não é bug** — `host_models`/`host_usage` (cache de modelos
    disponíveis / limites de uso do Claude) continuam globais, sem
    `host_id`. Avaliado: são dados da CONTA Claude, não da máquina — dois
    hosts com a mesma conta devem ver os mesmos modelos/limites mesmo.
    Nome sugere "por host" mas é intencionalmente compartilhado.
11. ✅ **RESOLVIDO (achado 2026-07-15, mesma família do achado #4)** —
    `host_directories` (cache do autocomplete de "Diretório de trabalho" na
    tela de criar sessão) tinha o MESMO problema de índice único só em
    `path`: dois hosts com o mesmo caminho relativo (ex.:
    `~/Documents/projects/foo`) colidiriam no upsert, e a busca não tinha
    como escopar por host — o autocomplete misturaria diretórios de
    máquinas diferentes (ex.: sugerir um caminho do Mac pra uma sessão que
    vai rodar no Windows). **Solução:** `dir_scanner.py` estampa `host_id`
    em cada sugestão; índice único virou `(host_id, path)` composto;
    `GET /directories` aceita `host_id` opcional (fallback = busca em
    todos, comportamento antigo); frontend passa o host escolhido na tela
    de criar. 476 docs legados (sem `host_id`, virariam duplicata
    permanente) foram limpos em produção — o scan periódico já os
    substituiu por versões com `host_id`.
12. ✅ **RESOLVIDO (achado 2026-07-15)** — mesmo com o `host_id` certo, o
    autocomplete do Windows voltou **vazio**: `dir_scanner.DEFAULT_ROOTS`
    (`~/Documents/projects`, `~/dev`, `~/work`) é convenção Mac — nessa
    máquina os projetos reais moram em `C:\repo` (`/mnt/c/repo` de dentro
    do WSL2), que não bate com nenhuma raiz padrão. **Solução:** raízes de
    scan viraram configuráveis por host via env `SESSIONFLOW_SCAN_ROOTS`
    (lista separada por vírgula, no `.env` DAQUELE host) —
    `runner._resolve_scan_roots()` lê a env e cai no `DEFAULT_ROOTS` de
    sempre se ela não existir (Mac não precisa mudar nada). No Windows,
    setei `SESSIONFLOW_SCAN_ROOTS=/mnt/c/repo` — 879 diretórios escaneados
    depois do restart, autocomplete testado de ponta a ponta via API.

## Design proposto (rascunho — sujeito a mudar com os testes)

### 1. Identidade do host
- Cada worker gera/lê um `host_id` estável (ex.: hash do hostname + um UUID
  persistido em `~/.claude/.sessionflow-host-id` na 1ª subida — não usar só
  hostname puro, pra não colidir se o usuário reinstalar o SO com o mesmo nome).
- Worker publica `capabilities` junto do heartbeat: `{tts: bool, transcription: bool,
  open_terminal: bool, platform: "darwin"|"linux"|"wsl2"}` — o frontend usa
  isso pra esconder botões/handlers incompatíveis por sessão (baseado no
  `host_id` da sessão), sem precisar de lista hardcoded de features por SO.

### 2. Roteamento de comandos por host
Duas opções, a decidir:
- **(A) Uma fila por host** (`sessionflow.commands.<host_id>`), publisher
  decide a fila pelo `host_id` da sessão alvo. Mais simples de raciocinar,
  mas precisa declarar/bind fila dinamicamente quando um host novo aparece.
- **(B) Uma fila só, com `host_id` no payload + consumer filtra e re-enfileira
  (ou nack) o que não é seu.** Mais simples de não mexer na topologia, mas
  desperdiça round-trips e complica o ack/retry.
- **Recomendação inicial: (A)** — fila por host é mais previsível e mais fácil
  de debugar (dá pra inspecionar a fila de UM host isoladamente no
  management UI do RabbitMQ).

### 3. Worker status multi
- Troca `_id="worker"` fixo por `_id=host_id`. Perfil na UI passa a listar N
  workers (hostname, plataforma, capabilities, online/offline por
  `updated_at` recente).

### 4. Schema da sessão
- Novo campo `host_id` no doc de sessão (index composto com `tmux_name` pra
  evitar colisão entre hosts). Sessões existentes (sem `host_id`) tratadas
  como pertencentes ao host "legado" (o Mac atual) — migração leve, sem
  downtime.

### 5. Frontend
- Badge de host no card da sessão (Home/Sessões) quando há mais de 1 host
  ativo — não mostrar nada se só existe 1 (não polui o caso comum de hoje).
- Filtro por host na tela de Sessões.
- Botões/áreas de feature incompatível (TTS, "abrir no Mac", upload de
  áudio pra transcrição) escondidos quando `capabilities` do host da sessão
  não suporta.

### 6. Rede

**Atualização (14/07) — achado grande: JÁ existe infra de túnel pronta,
não é preciso montar nada do zero.** Correção do que eu tinha escrito antes
(dizia "o projeto já usa túnel" — impreciso; o `docker-compose.yml` do
SessionFlow em si não declara `cloudflared`, mas há um container `cloudflare`
rodando **fora** do compose, já **conectado à rede `sessionflow_sessionflow_net`**):

- Container `cloudflare` (`cloudflared:latest`, rodando há dias, restart
  `always`) usa a conta boletoazap.dev.br (mesma da skill `cloudflare`).
  Túnel **`macbook`** (`5abf5f01-...`) já tem ingress pra:
  - `sessionflow.boletoazap.dev.br` → `http://host.docker.internal:4200`
    (frontend)
  - `api-sessionflow.boletoazap.dev.br` → `http://host.docker.internal:8000`
    (API)
  - Ou seja: **o app já é acessível de qualquer lugar via HTTPS hoje**,
    sem precisar abrir porta nenhuma na LAN. Isso resolve acesso remoto ao
    APP (frontend+API), mas **não** resolve o que o WORKER remoto precisa
    (Mongo 27017 + RabbitMQ 5672 são protocolos binários, não HTTP — não
    estão nessa lista de ingress).
  - `host.docker.internal` é a razão de já funcionar mesmo com
    `docker-compose.yml` publicando só em `127.0.0.1`: de DENTRO de um
    container no Docker Desktop (Mac), esse hostname especial alcança o
    host sem passar pela restrição de loopback que bloqueia acesso externo
    via LAN.
- **Existe um SEGUNDO túnel, `duck-server`**, que já aponta pro MESMO IP
  dessa máquina Windows de teste (`192.168.31.231`): `ollama`, `chat`,
  `note`, e principalmente `rdp.boletoazap.dev.br → tcp://192.168.31.231:3389`.
  **Isso prova que TCP cru via túnel Cloudflare (não só HTTP) já é um
  padrão usado e funcionando nessa conta** — não seria a primeira vez.
- **RabbitMQ já tem usuário/senha própria** (`RABBITMQ_DEFAULT_USER=sessionflow`,
  não é o `guest/guest` default) — reduz (mas não zera) o risco de abrir a
  porta pra LAN. Mongo também já usa usuário de app dedicado (ver `.env`,
  `MONGO_APP_USERNAME`), não é sem auth.

**Reavaliação da recomendação:** como a máquina de teste (Windows) está na
MESMA LAN do Mac (confirmado: ping, SSH, RDP via IP direto todos
funcionam), a rota mais simples PRA ESSE CASO é (a) abrir as portas do
`docker-compose.yml` pra LAN (trocar `127.0.0.1:PORT:PORT` por
`0.0.0.0:PORT:PORT` ou o IP da interface), já que auth em Mongo/Rabbit já
existe. **Guardar túnel TCP (Cloudflare) como o caminho pra quando o host
remoto NÃO estiver na mesma LAN** (ex.: outra casa/rede) — nesse caso o
precedente do `duck-server`/RDP mostra que dá pra criar `tcp://` ingress
pro Mongo/Rabbit também, exigindo `cloudflared access tcp` rodando do lado
do worker remoto como proxy local.
- **Confirmado na prática (14/07):** de dentro do WSL2 da máquina Windows,
  as 3 portas (Mongo 27017, Rabbit 5672, API 8000) respondem `Connection
  refused` no IP LAN do Mac (`192.168.31.147`) — bloqueio real, precisa de
  uma das duas soluções acima antes de rodar o worker de verdade lá.

## Fases (status)

1. ✅ **Feito** — `host_id` + capabilities no worker (`host_identity.py`,
   `heartbeat_loop`). Já aparece em produção via `GET /worker`/`GET /workers`.
2. ✅ **Feito** — Fila por host (`rabbit.commands_queue_name`,
   `command_publisher.py` roteando por `host_id`, `_require_route` na API).
   Worker do Mac já reiniciado com essa topologia, rodando em produção.
   Junto: índice composto `(host_id, tmux_name)` + backfill automático
   (3743 sessões migradas) + correção do bug crítico em
   `_mark_missing_stopped`.
3. ✅ **Feito** — Frontend: badge de host (Home/Sessões, só quando >1 host
   ativo), filtro por host em Sessões, gate de capabilities (esconde
   JARVIS/TTS, "abrir no Mac" e o gravador de áudio/transcrição quando o
   host da sessão não suporta) em `detalhe.component.ts`, e Perfil listando
   todos os hosts conhecidos. Novo `WorkersStore` compartilhado
   (`core/workers-store.ts`), fail-open por padrão. `tsc`/`ng build` OK.
4. ✅ **Feito (validado, depois parado)** — Worker real rodado na máquina
   Windows/WSL2 de teste (2026-07-14), via LAN direta:
   - Código do worker copiado por SSH (tar via stdin, sem git/credenciais no
     host de teste); `.env` MÍNIMO criado só com `MONGO_URI_HOST`/
     `RABBITMQ_URI_HOST` (IP da LAN do Mac) — não copiei o `.env` completo
     do projeto (tem segredos que o worker não usa).
   - `uv sync` instalou as deps; `uv run python -m sessionflow_worker` subiu
     de verdade: **`host_id` novo, `platform=wsl2`, capabilities corretas
     (tts/transcription/open_terminal todos `false`)** — exatamente como
     projetado, sem precisar de nenhum código específico pra essa máquina.
   - **Validado com os DOIS workers rodando ao mesmo tempo**: `GET /workers`
     mostrou os 2 hosts online; RabbitMQ com 2 filas
     (`sessionflow.commands.<host_id>` do Mac e do Windows), **1 consumidor
     cada, 0 mensagens** — zero competição. As 6 sessões reais do Mac
     continuaram intactas (contadas antes/depois) — o bug do
     `_mark_missing_stopped` (achado #7) realmente não se repetiu.
   - Único efeito colateral observado: o worker novo rodou sua rotina
     interna de scraping de uso (`sfusage-*`, ephemeral, auto-marca
     `stopped`) — comportamento esperado, não aparece no app
     (`_INTERNAL_PREFIXES`).
   - **Formalizado como serviço persistente** (2026-07-14, a pedido do
     Diego — "quero ele sempre on que nem no Mac"): unit systemd
     `/etc/systemd/system/sessionflow-worker.service` dentro do WSL2
     (`Restart=always`, `WorkingDirectory=/root/sessionflow/worker`),
     habilitada (`systemctl enable`) — sobrevive a queda do processo. Como o
     WSL2 não sobe sozinho com o Windows, criei também uma tarefa agendada
     do Windows (`schtasks /create ... /sc onlogon`) que dispara
     `wsl.exe -d Ubuntu -u root -- true` no login do usuário, o que inicia a
     distro (e, com ela, o systemd + o serviço). Equivalente ao `launchd` do
     Mac. Validado: serviço reiniciado via `systemctl start`, gerou o MESMO
     `host_id` de antes (persistido em arquivo), voltou a aparecer em
     `GET /workers` como online.
   - **Nome de exibição editável por host** (2026-07-14, a pedido do Diego —
     hostnames técnicos tipo `DESKTOP-ASCBQRT` são feios): novo campo
     `display_name` em `worker_status` (separado do `hostname` técnico, que
     o heartbeat do worker nunca sobrescreve por acidente), endpoint
     `PUT /workers/{host_id}/display-name`, e edição inline no Perfil (ícone
     ✎ ao lado do nome, em qualquer host, principal ou não).
     `WorkersStore.hostname()` (usado nos badges de Home/Sessões) já prefere
     o `display_name` quando existir. Testado de ponta a ponta (renomear →
     confirmar via `GET /workers` → limpar).
5. ✅ Ajustes de uso real (2026-07-15): nome de host editável, worker do
   Windows como serviço persistente, badge de host nas Tarefas, seletor de
   host na tela de criar sessão, e correção do autocomplete de diretório
   (achado #11 acima) — todos a pedido do Diego depois de usar o Perfil e a
   tela de criar sessão na prática.

## Testes / dúvidas a validar (Diego vai pedir aos poucos)

> Seção viva — cada teste pedido vira uma entrada aqui com o resultado.

- [x] **Conectividade LAN até a máquina Windows** (2026-07-14) — host
  `192.168.31.231` (`DESKTOP-ASCBQRT`, Windows 10.0.22621.4317), mesma rede
  do Mac. Ping OK (~7ms). Portas nativas: SSH (22) fechada, RDP (3389) e
  SMB (445/139) abertas — confirma que é Windows puro, sem WSL2/sshd ainda.
- [x] **Habilitar OpenSSH Server remotamente** (2026-07-14) — copiado script
  `habilitar-ssh.ps1` via SMB (montagem `//usuario:***@192.168.31.231/C`,
  autenticação só funcionou com o usuário local exato `usuario`, sem domínio)
  pra `Downloads` do usuário; executado manualmente no PowerShell Admin.
  `Add-WindowsCapability` demorou ~2-3min (baixa o payload via Windows
  Update) — não é travamento, é normal; confirmado acompanhando
  `C:\Windows\Logs\CBS\CBS.log` via SMB (`Processing complete...
  Package: OpenSSH-Server-Package..., state: Installed`). SSH (porta 22)
  respondeu logo depois que o `Start-Service sshd` + regra de firewall do
  script rodaram.
- [x] **SSH de ponta a ponta funcionando** (2026-07-14) — `ssh
  usuario@192.168.31.231` autentica e executa comandos. **Atenção:** o shell
  remoto default do OpenSSH nesse Windows é **`cmd.exe`**, não PowerShell —
  `;` como separador de comando (sintaxe bash/PowerShell) não funciona, usar
  `&&` ou `&`. Isso importa pro worker: os comandos que o SessionFlow hoje
  manda pro shell (via tmux) assumem sintaxe Unix — rodando de fato dentro
  do WSL2 (não do cmd.exe/PowerShell nativo) evita ter que adaptar sintaxe.
- [x] **WSL2 já está instalado nessa máquina** (2026-07-14) — surpresa boa,
  não precisou instalar nada. `wsl --list --verbose` mostra 2 distros
  rodando: `Ubuntu` (a que interessa) e `docker-desktop` (auxiliar do Docker
  Desktop). Ambas versão WSL **2** (não a 1, que não teria kernel Linux real
  nem bom suporte a rede/tmux).
- [x] **Ambiente dentro do Ubuntu já tem quase tudo que o worker precisa**
  (2026-07-14) — levantado via `ssh usuario@... 'wsl -d Ubuntu -- ...'`:
  - Kernel `5.15.167.4-microsoft-standard-WSL2` (x86_64).
  - **tmux 3.4** ✅ (o worker depende disso pra tudo).
  - **Python 3.12.3** ✅, **git** ✅, **node** ✅ já instalados.
  - **`uv` NÃO está instalado** (o launchd do Mac roda o worker via
    `uv run python -m sessionflow_worker`) — precisa instalar.
  - Usuário default do WSL é **`root`** (não existe usuário uid 1000
    configurado) — funciona, mas vale decidir se cria um usuário normal
    (mais alinhado com o Mac, que roda como usuário comum) ou aceita rodar
    como root nesse host.
  - `/etc/wsl.conf` já tem `systemd=true` — units systemd (auto-start do
    worker, alternativa ao `launchd` do Mac) funcionam nativamente, sem
    gambiarra.
  - Disco: 1TB total, 952GB livres — sem preocupação de espaço.
  - **Docker já funciona de dentro do Ubuntu** (`docker info` conecta via
    integração do Docker Desktop, `docker-cli.sock`) — não que o worker
    precise de Docker, mas facilita testar/rodar coisas.
  - Rede do WSL2: interface própria NAT (`172.23.71.10/20`), diferente da
    LAN (`192.168.31.x`) — MAS isso não atrapalha o worker, porque ele só
    faz conexões de SAÍDA (consome Mongo/Rabbit da API) — NAT de saída
    funciona transparente, sem precisar configurar port-forward.
  - Já tem uma pasta `~/dev/rogue-dialog-engine` (projeto não-relacionado,
    só mostra que a máquina já é usada pra dev de verdade).
- [x] **Bloqueio de rede confirmado NA PRÁTICA, não só na teoria**
  (2026-07-14) — de dentro do WSL2 (`172.23.71.10`), tentei alcançar o Mac
  pelo IP da LAN (`192.168.31.147`) nas portas do Mongo (27017), Rabbit
  (5672) e API (8000): as 3 deram **"Connection refused"** — confirma que
  o `docker-compose.yml` (bind só em `127.0.0.1`) realmente impede acesso
  remoto hoje. Precisa da decisão do item "Rede" acima antes de dar
  qualquer passo de conectar um worker remoto de verdade.
- [x] **Já existe infra de túnel Cloudflare rodando neste Mac, fora do
  compose do SessionFlow** (2026-07-14) — container `cloudflare`
  (`cloudflared:latest`, `restart: always`, rodando há dias) já está
  conectado à rede `sessionflow_sessionflow_net`. Túnel **`macbook`** já
  expõe `sessionflow.boletoazap.dev.br` (frontend) e
  `api-sessionflow.boletoazap.dev.br` (API) via `host.docker.internal` —
  ou seja, **o app já é acessível remotamente hoje**, mas isso é HTTP; não
  cobre o que o worker precisa (Mongo/Rabbit, protocolos binários).
- [x] **Existe precedente de TCP cru via túnel Cloudflare, já funcionando**
  (2026-07-14) — segundo túnel, **`duck-server`**, aponta pro MESMO IP da
  máquina Windows de teste (`192.168.31.231`): tem `rdp.boletoazap.dev.br
  → tcp://192.168.31.231:3389` já ativo. Prova que dá pra rotear TCP
  arbitrário (não só HTTP) nessa conta, caso precise expor Mongo/Rabbit
  via túnel pra um host fora da LAN.
- [x] **RabbitMQ/Mongo já têm auth própria** (não são `guest/guest` nem
  sem senha) — reduz o risco de simplesmente abrir a porta pra LAN nesse
  teste específico (mesma rede confirmada Mac↔Windows).
- [x] **Decisão: manter acesso direto do worker a Mongo/RabbitMQ** (2026-07-14)
  — Diego cogitou o worker falar só com a API (HTTP), reduzindo a
  superfície exposta a 1 protocolo em vez de 2. Avaliado: seria uma
  reescrita grande (o worker faz ~20 coleções de Mongo com upsert/
  aggregation direto, e CONSOME fila — viraria polling/SSE do zero), não
  um ajuste de config. **Decisão: manter como está hoje** (acesso direto),
  reavaliar só se o cenário de confiança da rede mudar no futuro.
- [x] **Portas abertas pra LAN e testadas** (2026-07-14) — `docker-compose.yml`:
  Mongo (27017) e RabbitMQ AMQP (5672) trocados de `127.0.0.1` pra `0.0.0.0`
  (API/frontend/mgmt UI do Rabbit continuam só locais). Testado de novo do
  WSL2: as 2 portas agora conectam (antes davam "Connection refused").
- [ ] ⚠️ **Risco identificado antes de rodar o worker de verdade no Windows:**
  rodar o worker completo lá AGORA significaria um SEGUNDO worker
  consumindo a MESMA fila global (`COMMANDS_QUEUE`) que o worker do Mac já
  usa em produção pras sessões reais do Diego — exatamente o problema de
  roteamento mapeado no achado #1 do topo deste doc. Comando de uma sessão
  do Mac poderia ser entregue por engano ao worker do Windows (que não tem
  aquele tmux) e vice-versa. **Recomendação: validar conectividade crua
  (Mongo/Rabbit, sem rodar o worker completo) antes de subir um 2º worker
  de fato — fila-por-host (fase 2 do plano) é pré-requisito pra rodar os
  dois workers ao mesmo tempo com segurança.**
- [x] **Conectividade de ponta a ponta confirmada (sem risco de 2 workers)**
  (2026-07-14) — `uv` instalado no Ubuntu/WSL2
  (`curl -LsSf https://astral.sh/uv/install.sh | sh`, versão 0.11.28).
  Script isolado (`uv run --with motor --with aio-pika script.py`, copiado
  via SMB pra Downloads → acessível em `/mnt/c/Users/usuario/Downloads/`
  de dentro do WSL2) testou, pelo IP da LAN do Mac
  (`192.168.31.147`): **Mongo autenticou** (ping + count em `sessions`,
  só leitura) e **RabbitMQ autenticou** (conectou e fechou, sem declarar
  fila). Script apagado logo depois — nada ficou residual na máquina
  remota nem em produção.
- [ ] _Próximo: implementar fase 1 (host_id + capabilities no worker) e
  fase 2 (fila por host) — só DEPOIS disso é seguro rodar o worker
  completo no Windows/WSL2 sem risco de competir pela fila com o worker
  do Mac. Alternativa mais rápida (não recomendada pra uso real, só se
  quiser ver o worker "funcionando" visualmente): apontar esse 2º worker
  pra um Mongo/Rabbit de teste separado, não o de produção._
- [x] **Túnel TCP preparado e testado de ponta a ponta — Mac pode estar em
  QUALQUER rede (5G incluso)** (2026-07-14) — Diego perguntou se o Mac
  precisava estar na mesma LAN; resposta: SIM pro esquema atual (IP
  privado só é roteável na LAN — 5G nem permite conexão de entrada,
  CGNAT). Preparado o caminho que resolve isso, replicando o padrão já
  provado do `duck-server`/RDP:
  - **Ingress novo no túnel `macbook`** (via `cf.py tunnel-route-add` —
    comando idempotente, reescreve a lista inteira preservando as rotas
    já existentes de prata/assina/ssh/etc.):
    - `mongo-sessionflow.boletoazap.dev.br` → `tcp://host.docker.internal:27017`
    - `rabbitmq-sessionflow.boletoazap.dev.br` → `tcp://host.docker.internal:5672`
  - **DNS CNAME criado pros 2 hostnames** → `5abf5f01-...cfargotunnel.com`,
    `proxied=true` (mesmo padrão dos outros registros sessionflow/rdp).
  - **Validado de ponta a ponta**: instalado `cloudflared` no WSL2
    (`curl -Lso cloudflared .../cloudflared-linux-amd64`), subido
    `cloudflared access tcp --hostname mongo-sessionflow.boletoazap.dev.br
    --url localhost:27018` (e o mesmo pro rabbitmq numa porta local
    diferente) — script de teste conectou em `127.0.0.1:27018`/`:56720`
    (NÃO mais o IP da LAN) e autenticou nos dois serviços. Processos de
    teste e script apagados depois — só ficaram a rota do túnel + o DNS,
    que são permanentes/reutilizáveis.
  - **Como o worker remoto vai usar isso na prática** (quando a fase 2 do
    plano — fila por host — estiver pronta): rodar
    `cloudflared access tcp --hostname mongo-sessionflow.boletoazap.dev.br
    --url localhost:27017 &` e o mesmo pro rabbitmq, e configurar
    `MONGO_URI_HOST`/`RABBITMQ_URI_HOST` do worker remoto apontando pra
    `127.0.0.1:27017`/`:5672` (o proxy local do `cloudflared access` finge
    ser o serviço de verdade). Funciona de qualquer rede porque quem inicia
    a conexão é sempre o lado que faz `cloudflared access` (saída), nunca
    precisa de porta aberta de entrada em lugar nenhum.
  - **Duas opções de rede agora disponíveis, documentadas e testadas**: LAN
    direta (mais simples, só quando os hosts estão na mesma rede) OU túnel
    (funciona de qualquer lugar, exige `cloudflared access tcp` rodando do
    lado do worker). A porta LAN (0.0.0.0) pode continuar aberta sem
    conflito — as duas formas coexistem.
- [x] **Fases 1+2 implementadas, testadas e em produção** (2026-07-14) —
  código: `worker/sessionflow_worker/host_identity.py` (novo),
  `rabbit.py`/`runner.py`/`command_consumer.py`/`discovery.py`/`mongo.py`
  (worker), `command_publisher.py`/`routers/sessions.py`/`routers/worker.py`
  (API). Resumo da validação:
  - **Testes automatizados**: suíte do worker (167 passaram, 11 falhas —
    todas confirmadas PRÉ-EXISTENTES via `git stash`, nenhuma relacionada);
    suíte da API (57 passaram, 2 falhas pré-existentes confirmadas do mesmo
    jeito). Teste novo cobrindo o achado #4 (`(host_id, tmux_name)` composto).
  - **Worker de produção reiniciado** com o código novo: gerou `host_id`
    (`f5efcc44-...`), **backfill migrou 3743 sessões** (0 ficaram sem
    `host_id`), fila `sessionflow.commands.f5efcc44-...` declarada com
    **1 consumidor ativo, 0 mensagens** (confirmado via
    `rabbitmqctl list_queues`).
  - **API reiniciada** com o código novo: `GET /worker` e `GET /workers`
    respondendo certo (host, platform, capabilities); tráfego REAL do
    frontend (screen/tasks/sessions) continuou funcionando sem interrupção
    durante e depois do restart.
  - **Achado lateral durante os testes**: a suíte da API tem testes que
    publicam num `sessionflow.commands` "cru" — como o worker de produção
    (ainda na topologia antiga) estava competindo pela mesma fila, várias
    mensagens de teste eram roubadas por ele antes do teste conseguir
    drená-las. Resolvido natural e definitivamente ao reiniciar o worker
    (ele saiu da fila legada); 3 testes de `create` foram ajustados pra
    passar um `host_id` de teste explícito (novo campo opcional
    `SessionCreate.host_id`, que também serve de base pro picker de host da
    Fase 3).
  - Filas de teste (`sessionflow.commands.test-host*`) limpas do broker
    depois da validação.

## Referências
- [`PORTABILITY.md`](../PORTABILITY.md) — features mac-only e como portar
  cada uma (TTS, transcrição MLX, auto-start, "abrir no Mac").
- `worker/sessionflow_worker/rabbit.py` — topologia atual da fila (exchange
  `sessionflow`, fila única `COMMANDS_QUEUE`).
- `worker/sessionflow_worker/runner.py:405-434` — `heartbeat_loop` (worker
  status singleton hoje).
