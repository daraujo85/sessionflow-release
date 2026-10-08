# tmux Runtime & Discovery — Specification

## Problem Statement

O SessionFlow precisa de uma camada que trate o **tmux como fonte de verdade**: descobrir todas as sessões tmux do host (inclusive as criadas fora do app), criar novas sessões já iniciando o agente certo, e gerenciar o ciclo de vida (encerrar, renomear, retomar). Sem essa fundação, nenhuma outra capacidade (output, input, histórico, notificações) tem o que observar ou controlar.

## Goals

- [ ] Worker (host, Python) descobre 100% das sessões tmux vivas e reconcilia estado em ciclos de ≤ 5s
- [ ] Criar sessão (nome, tipo de agente, diretório, modelo, esforço) resulta numa sessão tmux com o agente rodando, em ≤ 3s após o comando
- [ ] Encerrar / renomear / retomar refletem no tmux e no estado exposto pela API
- [ ] Cada sessão tem um estado dentre os 7 definidos, derivado de forma determinística

## Out of Scope

| Feature | Reason |
| --- | --- |
| Captura/streaming de output do pane | Feature separada ("Captura de Output & Input Remoto") |
| Injeção de input / `send-keys` | Feature separada ("Captura de Output & Input Remoto") |
| Persistência detalhada de eventos/histórico | Feature "Persistência & Histórico" (MongoDB) |
| UI do dashboard (telas Angular) | Feature "Dashboard Mobile-First + SSE" |
| Distinção semântica fina `waiting_input` vs `running` por parsing de pane | Depende de captura de output — tratada de forma básica aqui, refinada na feature de output |
| Métricas de token/contexto e limites por provider | Capturadas em features posteriores (dependem de leitura de output) |

---

## User Stories

### P1: Descoberta e reconciliação de sessões ⭐ MVP

**User Story**: Como operador, quero que o SessionFlow descubra automaticamente todas as sessões tmux do host — inclusive as que eu criei direto no terminal — para ter visão unificada sem cadastrar nada manualmente.

**Why P1**: tmux é a fonte de verdade (AD-001). Sem discovery, o app não sabe o que existe.

**Acceptance Criteria**:

1. WHEN o Worker inicia THEN SHALL listar todas as sessões tmux existentes (`tmux list-sessions`) e registrar cada uma com seus metadados (nome, criada-em, anexada ou não).
2. WHEN existe uma sessão tmux criada fora do SessionFlow THEN o Worker SHALL incluí-la na lista e marcá-la como `origem: externa`.
3. WHEN o Worker executa um ciclo de discovery (intervalo ≤ 5s) THEN SHALL reconciliar: sessões novas viram conhecidas, sessões que sumiram do tmux são marcadas `stopped`.
4. WHEN não há servidor tmux rodando (`tmux` sem sessões) THEN o Worker SHALL retornar lista vazia sem erro.
5. WHEN o Worker tenta identificar o tipo de agente de uma sessão THEN SHALL inferir a partir do comando/processo do pane (`claude`/`codex`/`gemini`/`opencode`) e marcar `desconhecido` quando não casar.

**Independent Test**: Criar 2 sessões tmux manualmente no terminal, subir o Worker e verificar que ambas aparecem na saída de discovery com tipo inferido e `origem: externa`.

---

### P2: Criar sessão com agente, modelo e esforço ⭐ MVP

**User Story**: Como operador, quero criar uma sessão informando nome, tipo de agente, diretório, modelo e esforço de raciocínio, para que o SessionFlow crie a sessão tmux e já inicie o agente configurado.

**Why P1 (MVP)**: É o UC01 e o fluxo central do mockup (overlay "Nova sessão"). _Marcada P2 apenas por ordem de implementação — depende da discovery existir._

**Acceptance Criteria**:

1. WHEN o operador envia criar-sessão com {nome, tipo, diretório, modelo, esforço} THEN o Worker SHALL executar `tmux new-session -d -s <nome> -c <diretório>` e então iniciar o agente naquele pane.
2. WHEN o agente é iniciado THEN o Worker SHALL montar o comando do CLI com as flags de modelo e esforço correspondentes ao provider _(mapeamento exato de flags por CLI a definir no Design — ver nota de incerteza)_.
3. WHEN o nome informado já existe como sessão tmux THEN o sistema SHALL rejeitar com erro claro (`nome duplicado`) sem criar nada.
4. WHEN o diretório não existe no host THEN o sistema SHALL rejeitar com erro (`diretório inexistente`) — não cria diretório silenciosamente.
5. WHEN a criação tem sucesso THEN a nova sessão SHALL aparecer na próxima discovery com `origem: sessionflow`, estado inicial `running`, e os metadados {modelo, esforço, tipo} associados.

**Independent Test**: Chamar criar-sessão para um agente em um diretório existente e verificar via `tmux list-sessions` + inspeção do pane que a sessão existe e o processo do agente está rodando com as flags esperadas.

---

### P3: Autocomplete de diretório do host ⭐ MVP

**User Story**: Como operador no celular, quero sugestões de diretórios do host ao digitar, para escolher a pasta de trabalho sem digitar o caminho inteiro.

**Why P1 (MVP)**: Confirmado no escopo MVP (AD-007); presente no overlay de criação. _Ordem: depois de criar funcionar com texto livre._

**Acceptance Criteria**:

1. WHEN o operador digita um termo THEN o Worker SHALL retornar até N (ex: 6) diretórios do host que casam com o termo.
2. WHEN o termo está vazio THEN o sistema SHALL retornar uma lista de diretórios recentes/raízes conhecidas.
3. WHEN nenhum diretório casa THEN o sistema SHALL sinalizar `nenhum diretório` (a UI informa que um novo poderá ser criado — sujeito à regra de criação da story P2).
4. WHEN a busca é feita THEN SHALL ser restrita a raízes permitidas (ex: `~/dev`, `~/work`) — nunca varrer o filesystem inteiro.

**Independent Test**: Pedir sugestões com termo "port" e verificar que retorna diretórios existentes do host contendo "port", limitado a N.

---

### P4: Ciclo de vida — encerrar, renomear, retomar ⭐ MVP

**User Story**: Como operador, quero encerrar, renomear e retomar sessões, para gerenciá-las remotamente sem SSH.

**Why P1 (MVP)**: UC06/UC07 + RF002/RF003/RF004.

**Acceptance Criteria**:

1. WHEN o operador encerra uma sessão THEN o Worker SHALL executar `tmux kill-session -t <nome>` e a sessão SHALL passar a `stopped`, preservando seu registro (histórico não é apagado).
2. WHEN o operador renomeia uma sessão THEN o Worker SHALL executar `tmux rename-session` e o novo nome SHALL refletir na próxima discovery.
3. WHEN o operador retoma uma sessão `detached` THEN o Worker SHALL anexá-la/reativá-la e o estado SHALL voltar para `running` (ou `waiting_input` se aplicável).
4. WHEN encerrar/renomear/retomar referencia uma sessão que não existe mais no tmux THEN o sistema SHALL responder com erro `sessão inexistente` e reconciliar o estado para `stopped`.
5. WHEN uma sessão é renomeada THEN a identidade interna (id) SHALL ser preservada (rename não cria sessão nova).

**Independent Test**: Criar sessão, renomear, encerrar — verificar cada transição via `tmux list-sessions` e no estado exposto.

---

### P5: Máquina de estados da sessão ⭐ MVP

**User Story**: Como sistema, preciso classificar cada sessão em um dos 7 estados, para que o dashboard e as notificações tenham um sinal confiável.

**Why P1 (MVP)**: Os 7 estados dirigem toda a UI (cores, filtros, badges).

**Acceptance Criteria**:

1. WHEN uma sessão tmux existe e está anexada/ativa com processo do agente vivo THEN o estado SHALL ser `running`.
2. WHEN a sessão existe mas não está anexada (`tmux` reporta sem clients) THEN o estado SHALL ser `detached`.
3. WHEN a sessão sumiu do tmux THEN o estado SHALL ser `stopped`.
4. WHEN o processo do agente terminou com falha (exit code ≠ 0 detectável) THEN o estado SHALL ser `error`.
5. WHEN os estados `waiting_input`, `waiting_external` e `completed` dependerem de leitura de conteúdo do pane THEN esta feature SHALL expor o campo de estado com valor básico (`running`/`detached`/`stopped`/`error`) e os demais SHALL ser refinados pela feature de captura de output _(dependência documentada)_.

**Independent Test**: Criar sessão (`running`), desanexar (`detached`), matar (`stopped`) — confirmar o estado reportado a cada passo.

---

## Edge Cases

- WHEN `tmux` não está instalado no host THEN o Worker SHALL logar erro claro e expor estado de "runtime indisponível" em vez de crashar.
- WHEN dois ciclos de discovery rodam concorrentemente THEN o sistema SHALL evitar dupla escrita (lock/serialização).
- WHEN o nome da sessão contém caracteres inválidos para tmux (`.`, `:`) THEN o sistema SHALL sanitizar ou rejeitar com mensagem clara.
- WHEN uma sessão externa usa um agente desconhecido THEN o sistema SHALL exibi-la como tipo `desconhecido` sem quebrar a UI.
- WHEN o diretório de trabalho informado é relativo THEN o sistema SHALL resolvê-lo de forma previsível (rejeitar ou resolver a partir do home) — definir no Design.
- WHEN a conexão com RabbitMQ/Mongo remoto cai durante uma operação THEN o Worker SHALL enfileirar/retentar localmente e não perder a operação solicitada _(estratégia no Design — AD-005)_.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| --- | --- | --- | --- |
| TMUX-01 | P1: Discovery de sessões | Design | Pending |
| TMUX-02 | P1: Incluir sessões externas | Design | Pending |
| TMUX-03 | P1: Reconciliação por ciclo | Design | Pending |
| TMUX-04 | P1: Inferência de tipo de agente | Design | Pending |
| TMUX-05 | P2: Criar sessão tmux + iniciar agente | Design | Pending |
| TMUX-06 | P2: Flags de modelo/esforço por provider | Design | Pending |
| TMUX-07 | P2: Validação nome duplicado / diretório inexistente | Design | Pending |
| TMUX-08 | P3: Autocomplete de diretório (raízes permitidas) | Design | Pending |
| TMUX-09 | P4: Encerrar (kill-session) preservando registro | Design | Pending |
| TMUX-10 | P4: Renomear preservando identidade | Design | Pending |
| TMUX-11 | P4: Retomar sessão detached | Design | Pending |
| TMUX-12 | P5: Máquina de estados (7 estados) | Design | Pending |

**ID format:** `TMUX-[NUMBER]`
**Status values:** Pending → In Design → In Tasks → Implementing → Verified
**Coverage:** 12 total, 0 mapeados a tasks, 12 unmapped ⚠️ (mapear na fase de Tasks)

---

## Open Questions / Notas de Incerteza

> ⚠️ **Não fabricar flags de CLI.** O mapeamento exato de `modelo` e `esforço de raciocínio` para flags de cada CLI (`claude`, `codex`, `gemini`, `opencode`) precisa ser verificado na fase de Design (docs oficiais / Context7 / `--help` de cada CLI). Não assumir nomes de flags aqui.

- Como detectar de forma confiável o **exit code / falha** do processo do agente dentro do pane tmux? (Design)
- Quais **raízes de diretório** são permitidas para o autocomplete? (default sugerido: `~/dev`, `~/work` — confirmar)
- O Worker mantém estado **em memória** entre ciclos ou persiste tudo no MongoDB remoto? (Design — AD-004)

---

## Success Criteria

- [ ] Subir o Worker descobre todas as sessões tmux existentes (internas + externas) em ≤ 5s
- [ ] Criar uma sessão pelo fluxo resulta em sessão tmux com agente rodando em ≤ 3s
- [ ] Encerrar / renomear / retomar refletem no tmux e no estado exposto sem inconsistência
- [ ] Toda sessão sempre tem um estado válido dentre os 7; transições básicas (running/detached/stopped/error) são determinísticas
- [ ] Nenhuma operação derruba o Worker — falhas viram erros tratados
