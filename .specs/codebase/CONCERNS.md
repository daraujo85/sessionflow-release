# Concerns

*Forward-looking (greenfield) — riscos antecipados do design. Revisar conforme o código nasce.*

## Segurança

### C-SEC-01: API exposta publicamente via túnel sem auth própria
**Risco:** Alto. `api.sessionflow.boletoazap.dev.br` é público; a auth depende 100% da camada de túnel Cloudflare (AD-008). Se o túnel não estiver protegido (ex: Cloudflare Access), qualquer um cria/encerra sessões e injeta comandos no terminal do host.
**Evidência:** AD-008/AD-011; API sem login no MVP.
**Fix:** Confirmar que o container de túnel exige autenticação (Cloudflare Access ou equivalente) ANTES de chegar na API. Validar antes de ir ao ar.

### C-SEC-02: Worker executa comandos arbitrários no host
**Risco:** Alto por natureza. O Worker roda `tmux send-keys` e inicia processos no host — é essencialmente execução remota de comandos. Combinado com C-SEC-01, é o vetor mais sensível.
**Fix:** Restringir criação a raízes/agentes permitidos; sanitizar nomes/dirs; nunca ecoar segredos; logar operações. Auth do túnel é o gate principal.

### C-SEC-03: Mongo management / Rabbit management
**Risco:** Médio. Portas publicadas só em `127.0.0.1` (ok). Não expor `15672`/`27017`/`5672` via túnel.
**Fix:** Manter binds em `127.0.0.1`; não criar subdomínio para management.

## Fragilidade / Acoplamento

### C-FRAG-01: Drift de flags dos CLIs de agente
**Risco:** Médio. `claude/codex/gemini/opencode` mudam flags entre versões; o `agent_launcher` quebra silenciosamente se uma flag sumir/renomear (ex: chave de effort do codex não confirmada; gemini sem effort).
**Fix:** Centralizar a tabela de flags; teste unit por agente; validar contra `--help` no boot e degradar com aviso em vez de crashar.

### C-FRAG-02: Detecção de estado depende de heurística do pane
**Risco:** Médio. `waiting_input`/`completed`/`error` exigem ler/interpretar o pane; `remain-on-exit`/`pane_dead_status` ainda não validados no tmux 3.6b.
**Fix:** Nesta feature, só estados determinísticos; refinar na feature de captura de output com testes de integração reais.

## Operacional

### C-OPS-01: Worker fora do Docker = setup manual
**Risco:** Médio. O Worker no host não sobe com `docker compose`; precisa de processo gerenciado (launchd/pm2/script) e reconexão a Mongo/Rabbit quando a stack reinicia.
**Fix:** Definir supervisão do processo + retry/backoff nos clientes; health do Worker visível no app (mockup já prevê "Worker conectado").

### C-OPS-02: Dependência de rede para tudo externo
**Risco:** Baixo-Médio. Acesso remoto depende do túnel Cloudflare; SSE precisa sobreviver ao timeout de ~100s (heartbeat).
**Fix:** Heartbeat no SSE; UI tolerar reconexão.

## Cobertura de Testes (gaps conhecidos)

### C-TEST-01: Integração não é paralela
**Risco:** Baixo. tmux/Mongo compartilhados forçam testes de integração sequenciais (ver TESTING.md) — suíte mais lenta conforme cresce.
**Fix:** Namespacing de sessões (`sftest-<uuid>`) e DB de teste dedicado; aceitar execução sequencial.

### C-TEST-02: Frontend sem framework de teste definido
**Risco:** Baixo (ainda). Tooling de teste do Angular não foi confirmado.
**Fix:** Decidir Karma/Jasmine vs Jest/Vitest antes de implementar o Dashboard.
