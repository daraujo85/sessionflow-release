# Latência de input e scroll no terminal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Envio de texto no terminal da sessão parece instantâneo (eco otimista) e scroll do histórico não trava (menos tráfego de rede + buffer local maior).

**Architecture:** Frontend Angular (`detalhe.component.ts`) ganha feedback local imediato (reaproveitando o mecanismo de hint já existente) e remove o modo de forward tecla-a-tecla que não resolvia o problema. Backend reduz latência de reconciliação: API reusa conexão AMQP em vez de abrir uma por comando; worker corta retries desnecessários de Enter.

**Tech Stack:** Angular 18+ (signals, `@angular/build:unit-test`/Karma via `ng test`), FastAPI + aio-pika (API), Python asyncio + libtmux (worker, pytest).

**Spec:** `docs/adr/2026-08-20-terminal-input-scroll-latency-design.md`

## Global Constraints

- Sem eco/splice dentro do `screen`/`bufText` (mirror ANSI da tela remota) — feedback otimista só via UI separada (hint), nunca editando o conteúdo do `<pre>`.
- `liveMode` e tudo que dependia dele (forward por tecla, autocomplete de `/`) é removido, não mantido atrás de flag.
- Nenhuma mudança em `session_output`/`session_screen` (Mongo) ou em `output_capture.py` — fora de escopo.
- `session-panel.component.ts` (variante split) não usa nenhum destes mecanismos hoje — fora de escopo, não tocar.

---

### Task 1: Remover "modo ao vivo" (frontend)

**Files:**
- Modify: `frontend/src/app/features/detalhe/detalhe.component.ts` (template ~1333-1346, e métodos/signals ~4602-4619, 4859, 5577-5591, 5632-5657, 5659-5722)
- Modify: `frontend/src/app/core/session-prefs-store.ts:14`

**Interfaces:**
- Produces: `send()` simplificado (sem branch `liveMode`), `onDraftChange(value: string)` só grava draft (sem `scheduleForward`).
- Consumes: nada de tasks anteriores (primeira task).

- [ ] **Step 1: Remover o botão "modo ao vivo" do template**

Em `detalhe.component.ts`, apagar o `<button class="live-toggle" ... toggleLive() ...>` (linhas 1333-1346, o segundo `live-toggle`, o de `liveMode()`/`toggleLive()` — **não** apagar o de `keypadOpen()`/`toggleKeypad()` logo acima).

- [ ] **Step 2: Remover estado e métodos do modo ao vivo**

Apagar do component:
- `protected readonly liveMode = signal<boolean>(false);` (linha 4615) e o comentário acima.
- `private paneBuffer = '';` (linha 4617) e `private liveTimer: ReturnType<typeof setTimeout> | null = null;` (linha 4619).
- `this.liveMode.set(prefs.liveMode ?? false);` (linha 4859).
- Métodos `toggleLive()`, `scheduleForward()`, `flushForward()`, `forwardDiff()`, `backspacePane()` (linhas 5641-5722).

Em `inputPlaceholder` (linha 4594-4605), trocar:

```typescript
return this.liveMode()
  ? 'Digite — ao vivo no terminal…'
  : 'Enviar comando ao terminal…';
```

por:

```typescript
return 'Enviar comando ao terminal…';
```

Em `onDraftChange` (linha 5632-5639), trocar:

```typescript
protected onDraftChange(value: string): void {
  this.draft.set(value);
  this.drafts.set(this.id(), value); // persiste por sessão
  // Com anexo staged, o texto é LEGENDA dos arquivos — não encaminha ao vivo.
  if (this.liveMode() && this.pendingItems().length === 0) {
    this.scheduleForward();
  }
}
```

por:

```typescript
protected onDraftChange(value: string): void {
  this.draft.set(value);
  this.drafts.set(this.id(), value); // persiste por sessão
}
```

Em `send()` (linha 5566-5629), apagar o bloco inteiro do branch `if (this.liveMode()) { ... }` (linhas 5577-5591), mantendo o resto do método (o branch de caixa vazia e o branch de texto seguem exatamente como estão nesta task — o eco otimista entra na Task 2).

- [ ] **Step 3: Remover `liveMode` de `SessionPrefs`**

Em `frontend/src/app/core/session-prefs-store.ts`, apagar:

```typescript
  /** "Modo ao vivo": encaminha a digitação pro pane (autocomplete do CLI). */
  liveMode?: boolean;
```

- [ ] **Step 4: Compilar e verificar que não sobrou referência**

Rodar:

```bash
cd frontend && npx tsc --noEmit -p tsconfig.app.json
```

Esperado: sem erro. Se aparecer `liveMode`/`toggleLive`/`scheduleForward`/`forwardDiff`/`backspacePane`/`paneBuffer`/`liveTimer` não encontrado ou não usado, é sinal de referência esquecida — buscar e remover.

Rodar também:

```bash
grep -rn "liveMode\|toggleLive\|scheduleForward\|forwardDiff\|backspacePane" frontend/src/app/features/detalhe/detalhe.component.ts frontend/src/app/core/session-prefs-store.ts
```

Esperado: nenhum resultado.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/features/detalhe/detalhe.component.ts frontend/src/app/core/session-prefs-store.ts
git commit -m "refactor(frontend): remove modo ao vivo do terminal (substituído por eco otimista)"
```

---

### Task 2: Eco otimista no envio de texto (frontend)

**Files:**
- Modify: `frontend/src/app/features/detalhe/detalhe.component.ts` (métodos `send()`, `showHint`/`clearHint`/`warnHint` ~6276-6304, template do hint ~1283-1290)
- Test: `frontend/src/app/features/detalhe/detalhe.component.spec.ts` (novo)

**Interfaces:**
- Consumes: `send()` já simplificado pela Task 1 (sem branch `liveMode`).
- Produces: `actionHintKind` signal (`'info' | 'error' | null`), `dismissHint()` method — usados só dentro deste componente.

**Contexto:** o mecanismo de hint (`actionHint` signal, `showHint`/`warnHint`/`clearHint`, linhas 4399, 6276-6304) já mostra um balão "Enviando…" na hora do clique, antes da resposta HTTP — é o pedaço de "eco otimista" que já existe. Falta: (a) mostrar o texto de verdade digitado, não um rótulo genérico, e (b) no erro, manter visível até o usuário dispensar em vez de sumir sozinho.

- [ ] **Step 1: Escrever o teste (Angular TestBed, isolado do resto do componente)**

Criar `frontend/src/app/features/detalhe/detalhe.component.spec.ts`:

```typescript
import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { ActivatedRoute } from '@angular/router';
import { of } from 'rxjs';
import { DetalheComponent } from './detalhe.component';
import { API_BASE_URL } from '../../core/api.service';

const BASE = 'http://localhost:8000';

describe('DetalheComponent — eco otimista no envio', () => {
  let fixture: ReturnType<typeof TestBed.createComponent<DetalheComponent>>;
  let component: DetalheComponent;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [DetalheComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: API_BASE_URL, useValue: BASE },
        {
          provide: ActivatedRoute,
          useValue: { snapshot: { paramMap: { get: () => 'sess-1' } }, paramMap: of({ get: () => 'sess-1' }) },
        },
      ],
    });
    fixture = TestBed.createComponent(DetalheComponent);
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
    fixture.detectChanges();
  });

  afterEach(() => {
    httpMock.verify();
  });

  it('mostra o texto digitado como hint imediatamente, antes da resposta HTTP', () => {
    (component as any).draft.set('ls -la');
    (component as any).send();

    expect((component as any).actionHint()).toContain('ls -la');

    const req = httpMock.expectOne(`${BASE}/sessions/sess-1/input`);
    req.flush({});
  });

  it('marca o hint como erro (sem sumir sozinho) quando o envio falha', () => {
    (component as any).draft.set('ls -la');
    (component as any).send();

    const req = httpMock.expectOne(`${BASE}/sessions/sess-1/input`);
    req.flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });

    expect((component as any).actionHint()).toContain('ls -la');
    expect((component as any).actionHintKind()).toBe('error');
  });

  it('dismissHint() limpa um hint de erro', () => {
    (component as any).draft.set('ls -la');
    (component as any).send();
    const req = httpMock.expectOne(`${BASE}/sessions/sess-1/input`);
    req.flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });

    (component as any).dismissHint();

    expect((component as any).actionHint()).toBeNull();
  });
});
```

- [ ] **Step 2: Rodar e verificar que falha**

```bash
cd frontend && npx ng test --watch=false --include='**/detalhe.component.spec.ts'
```

Esperado: FAIL — `actionHintKind` não existe / hint não contém o texto digitado / hint some no erro.

- [ ] **Step 3: Implementar**

Em `detalhe.component.ts`, junto do `actionHint` (linha 4399), adicionar:

```typescript
/** 'error' = hint fica até o usuário dispensar (dismissHint()); senão auto-limpa. */
protected readonly actionHintKind = signal<'info' | 'error' | null>(null);
```

Substituir `showHint`/`warnHint`/`clearHint` (linhas 6276-6304) por:

```typescript
/** Liga o feedback "em trânsito" com um rótulo, até a tela mudar (ou 40s). */
private showHint(label: string): void {
  this.actionHint.set(label);
  this.actionHintKind.set('info');
  if (this.hintTimer) {
    clearTimeout(this.hintTimer);
  }
  // Rede de segurança: se a tela não mudar (ex.: nada injetado), some em 40s.
  this.hintTimer = setTimeout(() => this.clearHint(), 40000);
}

/** Aviso curto no mesmo balão do hint (some sozinho em ~4s). */
private warnHint(label: string): void {
  this.actionHint.set(label);
  this.actionHintKind.set('info');
  if (this.hintTimer) {
    clearTimeout(this.hintTimer);
  }
  this.hintTimer = setTimeout(() => this.clearHint(), 4000);
}

/** Erro no envio: fica visível até o usuário dispensar (sem timer). */
private errorHint(label: string): void {
  this.actionHint.set(label);
  this.actionHintKind.set('error');
  if (this.hintTimer) {
    clearTimeout(this.hintTimer);
    this.hintTimer = null;
  }
}

private clearHint(): void {
  if (this.actionHint() === null) {
    return;
  }
  this.actionHint.set(null);
  this.actionHintKind.set(null);
  if (this.hintTimer) {
    clearTimeout(this.hintTimer);
    this.hintTimer = null;
  }
}

/** Botão "×" do hint de erro. */
protected dismissHint(): void {
  this.clearHint();
}
```

Em `send()` (trecho de texto puro, hoje linhas ~5605-5628 após a Task 1 remover o branch de `liveMode`), trocar:

```typescript
const text = this.draft().trim();
// Sem eco local: o espelho de tela mostra o que foi digitado na caixa do
// agente no próximo poll (~1.2s).
this.sending.set(true);
this.showHint('Enviando…');
this.markWorkingLocal();
this.api
  .sendInput(this.id(), text)
  .pipe(takeUntilDestroyed(this.destroyRef))
  .subscribe({
    next: () => {
      this.draft.set('');
      this.drafts.set(id, '');
      this.sending.set(false);
      this.refreshBurst(); // pega o eco no pane assim que o worker injeta
    },
    error: () => {
      this.sending.set(false);
      this.clearHint(); // falhou → tira o aviso na hora
    },
  });
```

por:

```typescript
const text = this.draft().trim();
// Eco otimista: mostra o texto digitado na hora (balão separado, não mexe no
// espelho ANSI da tela remota — a posição da caixa de input lá varia por
// app). Some quando a tela real confirmar (refreshScreen → clearHint) ou
// vira erro persistente se o envio falhar.
this.sending.set(true);
this.showHint(`Enviando: "${text}"`);
this.markWorkingLocal();
this.draft.set('');
this.drafts.set(id, '');
this.api
  .sendInput(this.id(), text)
  .pipe(takeUntilDestroyed(this.destroyRef))
  .subscribe({
    next: () => {
      this.sending.set(false);
      this.refreshBurst(); // pega o eco no pane assim que o worker injeta
    },
    error: () => {
      this.sending.set(false);
      this.errorHint(`Falha ao enviar: "${text}"`);
    },
  });
```

Nota: `this.draft.set('')`/`this.drafts.set(id, '')` sobem pra ANTES do `subscribe` — o campo limpa na hora (parte do eco otimista: o usuário já vê que foi "aceito" localmente), o hint com o texto é que carrega a informação do que foi enviado até confirmar.

No template do hint (linhas 1283-1290), adicionar o botão de dispensar quando for erro:

```html
@if (actionHint(); as hint) {
  <!-- Feedback do "gap": algo enviado (texto/anexo/áudio), aguardando
       aparecer no terminal. Erro fica até o usuário dispensar. -->
  <div class="transcribing" [class.is-error]="actionHintKind() === 'error'" role="status" aria-live="polite">
    @if (actionHintKind() !== 'error') {
      <span class="transcribing-spinner" aria-hidden="true"></span>
    }
    <span>{{ hint }}</span>
    @if (actionHintKind() === 'error') {
      <button type="button" class="hint-dismiss" (click)="dismissHint()" aria-label="Dispensar aviso">×</button>
    }
  </div>
}
```

Adicionar estilo mínimo pro estado de erro e pro botão de dispensar perto do CSS existente de `.transcribing` (mesma seção de estilos do componente):

```css
.transcribing.is-error {
  border-color: #dc2626;
  color: #dc2626;
}
.hint-dismiss {
  margin-left: auto;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
  color: inherit;
}
```

- [ ] **Step 4: Rodar e verificar que passa**

```bash
cd frontend && npx ng test --watch=false --include='**/detalhe.component.spec.ts'
```

Esperado: PASS nos 3 testes.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/features/detalhe/detalhe.component.ts frontend/src/app/features/detalhe/detalhe.component.spec.ts
git commit -m "feat(frontend): eco otimista no envio de texto do terminal"
```

---

### Task 3: Reduzir refreshBurst de 6 requisições pra 1 (frontend)

**Files:**
- Modify: `frontend/src/app/features/detalhe/detalhe.component.ts:6606-6625`
- Test: `frontend/src/app/features/detalhe/detalhe.component.spec.ts` (adicionar caso)

**Interfaces:**
- Consumes: nada de tasks anteriores diretamente (método independente).
- Produces: nenhuma interface nova — comportamento interno de `refreshBurst()`.

- [ ] **Step 1: Escrever o teste**

No mesmo `detalhe.component.spec.ts` da Task 2, adicionar (dentro de um novo `describe`):

```typescript
describe('DetalheComponent — refreshBurst', () => {
  let fixture: ReturnType<typeof TestBed.createComponent<DetalheComponent>>;
  let component: DetalheComponent;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [DetalheComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: API_BASE_URL, useValue: BASE },
        {
          provide: ActivatedRoute,
          useValue: { snapshot: { paramMap: { get: () => 'sess-1' } }, paramMap: of({ get: () => 'sess-1' }) },
        },
      ],
    });
    fixture = TestBed.createComponent(DetalheComponent);
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
    fixture.detectChanges();
    httpMock.match(() => true).forEach((r) => r.flush({ text: '', scrollback: '', at: new Date().toISOString() }));
  });

  afterEach(() => {
    httpMock.verify();
  });

  it('dispara no máximo 2 GET /screen (imediato + 1 poll de segurança) em 3s', fakeAsync(() => {
    (component as any).refreshBurst();

    tick(3000);

    const reqs = httpMock.match(`${BASE}/sessions/sess-1/screen`);
    expect(reqs.length).toBeLessThanOrEqual(2);
    reqs.forEach((r) => r.flush({ text: '', scrollback: '', at: new Date().toISOString() }));
  }));
});
```

Adicionar o import `fakeAsync, tick` de `@angular/core/testing` no topo do spec.

- [ ] **Step 2: Rodar e verificar que falha**

```bash
cd frontend && npx ng test --watch=false --include='**/detalhe.component.spec.ts'
```

Esperado: FAIL — mais de 2 requisições (o código atual dispara 6).

- [ ] **Step 3: Implementar**

Em `detalhe.component.ts:6614-6625`, trocar:

```typescript
private refreshBurst(): void {
  for (const t of this.refreshBurstTimers) {
    clearTimeout(t);
  }
  this.refreshBurstTimers = [];
  this.refreshScreen(); // já: pode pegar a tela nova se o agente foi rápido
  // O comando de scroll vai por fila e o espelho só é regravado no ciclo do
  // worker (~0,6s). Cobrimos até ~2,5s p/ o conteúdo novo aparecer com certeza.
  for (const delay of [250, 600, 1000, 1600, 2400]) {
    this.refreshBurstTimers.push(setTimeout(() => this.refreshScreen(), delay));
  }
}
```

por:

```typescript
private refreshBurst(): void {
  for (const t of this.refreshBurstTimers) {
    clearTimeout(t);
  }
  this.refreshBurstTimers = [];
  this.refreshScreen(); // já: pode pegar a tela nova se o agente foi rápido
  // SSE é o caminho principal de sincronização agora; este é só um poll de
  // segurança caso o push falhe ou chegue fora de ordem.
  this.refreshBurstTimers.push(setTimeout(() => this.refreshScreen(), 1500));
}
```

- [ ] **Step 4: Rodar e verificar que passa**

```bash
cd frontend && npx ng test --watch=false --include='**/detalhe.component.spec.ts'
```

Esperado: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/features/detalhe/detalhe.component.ts frontend/src/app/features/detalhe/detalhe.component.spec.ts
git commit -m "perf(frontend): refreshBurst cai de 6 pra 1 poll de segurança (SSE é o caminho principal)"
```

---

### Task 4: Aumentar buffer client-side de scroll (frontend)

**Files:**
- Modify: `frontend/src/app/features/detalhe/detalhe.component.ts:4756`

**Interfaces:**
- Consumes: nada.
- Produces: nada — constante interna.

Mudança de uma linha; sem teste dedicado (constante, comportamento já coberto pelos testes existentes de `stitchScrollback`/buffer se houver — nenhuma lógica nova).

- [ ] **Step 1: Alterar a constante**

Em `detalhe.component.ts:4756`, trocar:

```typescript
private static readonly BUF_MAX_LINES = 5000;
```

por:

```typescript
private static readonly BUF_MAX_LINES = 30000;
```

- [ ] **Step 2: Verificar compilação**

```bash
cd frontend && npx tsc --noEmit -p tsconfig.app.json
```

Esperado: sem erro.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/features/detalhe/detalhe.component.ts
git commit -m "perf(frontend): buffer de scrollback local de 5000 pra 30000 linhas"
```

---

### Task 5: Reusar conexão AMQP no publisher (API)

**Files:**
- Modify: `api/app/publishers/command_publisher.py`
- Test: `api/app/tests/test_command_publisher.py` (novo)

**Interfaces:**
- Consumes: nada (módulo isolado, 23 call sites existentes de `publish_command(settings, type=..., payload=..., host_id=...)` continuam iguais — assinatura não muda).
- Produces: `publish_command` com mesma assinatura pública; conexão cacheada em módulo (`_get_connection`).

- [ ] **Step 1: Escrever o teste (mock de `aio_pika.connect_robust`, sem broker real)**

Criar `api/app/tests/test_command_publisher.py`:

```python
"""Teste de reuso de conexão AMQP no publisher (perf: evita handshake por comando)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.publishers import command_publisher
from app.config import Settings


class _FakeExchange:
    def __init__(self) -> None:
        self.publish = AsyncMock()


class _FakeChannel:
    def __init__(self) -> None:
        self._exchange = _FakeExchange()

    async def declare_exchange(self, *args, **kwargs):
        return self._exchange


class _FakeConnection:
    def __init__(self) -> None:
        self.is_closed = False
        self.channel = AsyncMock(side_effect=lambda: _FakeChannel())
        self.close = AsyncMock()


@pytest.fixture(autouse=True)
def _reset_cached_connection():
    command_publisher._connection = None
    yield
    command_publisher._connection = None


@pytest.mark.asyncio
async def test_publish_command_reuses_connection_across_calls(monkeypatch):
    fake_connection = _FakeConnection()
    connect_calls = []

    async def fake_connect_robust(uri):
        connect_calls.append(uri)
        return fake_connection

    monkeypatch.setattr(command_publisher.aio_pika, "connect_robust", fake_connect_robust)

    settings = Settings(rabbitmq_uri="amqp://fake")

    await command_publisher.publish_command(settings, type="noop", payload={})
    await command_publisher.publish_command(settings, type="noop", payload={})

    assert len(connect_calls) == 1  # conexão aberta uma vez só, reusada na 2ª chamada
    assert fake_connection.close.await_count == 0  # não fecha entre chamadas


@pytest.mark.asyncio
async def test_publish_command_reconnects_if_connection_closed(monkeypatch):
    fake_connection = _FakeConnection()
    connect_calls = []

    async def fake_connect_robust(uri):
        connect_calls.append(uri)
        return fake_connection

    monkeypatch.setattr(command_publisher.aio_pika, "connect_robust", fake_connect_robust)

    settings = Settings(rabbitmq_uri="amqp://fake")

    await command_publisher.publish_command(settings, type="noop", payload={})
    fake_connection.is_closed = True
    await command_publisher.publish_command(settings, type="noop", payload={})

    assert len(connect_calls) == 2  # conexão morta → reconecta
```

Ajustar `Settings(rabbitmq_uri="amqp://fake")` pro construtor real de `app.config.Settings` se os nomes de campo forem diferentes — checar `api/app/config.py` antes de rodar (campo usado hoje em `command_publisher.py:61` é `settings.effective_rabbitmq_uri`).

- [ ] **Step 2: Rodar e verificar que falha**

```bash
cd api && python -m pytest app/tests/test_command_publisher.py -v
```

Esperado: FAIL — `command_publisher._connection` não existe / `connect_robust` chamado 2x.

- [ ] **Step 3: Implementar**

Em `api/app/publishers/command_publisher.py`, adicionar após os imports/constantes existentes:

```python
import asyncio

_connection: aio_pika.abc.AbstractRobustConnection | None = None
_connection_lock = asyncio.Lock()


async def _get_connection(settings: Settings) -> aio_pika.abc.AbstractRobustConnection:
    """Conexão AMQP cacheada no processo — evita handshake TCP+AMQP a cada
    comando (era o gargalo de latência no envio de texto pro terminal).
    Reconecta se a conexão cacheada morreu."""
    global _connection
    async with _connection_lock:
        if _connection is None or _connection.is_closed:
            _connection = await aio_pika.connect_robust(settings.effective_rabbitmq_uri)
        return _connection
```

Trocar o corpo de `publish_command` (linhas 61-76):

```python
    connection = await aio_pika.connect_robust(settings.effective_rabbitmq_uri)
    try:
        channel = await connection.channel()
        exchange = await channel.declare_exchange(
            EXCHANGE_NAME,
            aio_pika.ExchangeType.DIRECT,
            durable=True,
        )
        message = aio_pika.Message(
            body=json.dumps(message_body).encode("utf-8"),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        await exchange.publish(message, routing_key=_routing_key_for(host_id))
    finally:
        await connection.close()
```

por:

```python
    connection = await _get_connection(settings)
    channel = await connection.channel()
    exchange = await channel.declare_exchange(
        EXCHANGE_NAME,
        aio_pika.ExchangeType.DIRECT,
        durable=True,
    )
    message = aio_pika.Message(
        body=json.dumps(message_body).encode("utf-8"),
        content_type="application/json",
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
    )
    await exchange.publish(message, routing_key=_routing_key_for(host_id))
```

(A conexão não é mais fechada ao fim de cada chamada — fica viva pro processo, reusada na próxima. O canal por chamada é barato e mantido simples.)

- [ ] **Step 4: Rodar e verificar que passa**

```bash
cd api && python -m pytest app/tests/test_command_publisher.py -v
```

Esperado: PASS nos 2 testes.

- [ ] **Step 5: Rodar a suíte de sessions_input pra garantir que nada quebrou**

```bash
cd api && python -m pytest app/tests/test_sessions_input.py -v
```

Esperado: PASS (ou SKIP se precisar de RabbitMQ real indisponível — mesmo comportamento de antes desta mudança).

- [ ] **Step 6: Commit**

```bash
git add api/app/publishers/command_publisher.py api/app/tests/test_command_publisher.py
git commit -m "perf(api): reusa conexão AMQP no publisher em vez de abrir uma por comando"
```

---

### Task 6: Reduzir retries de Enter no worker

**Files:**
- Modify: `worker/sessionflow_worker/command_consumer.py:2157-2180`
- Test: `worker/sessionflow_worker/tests/test_type_and_submit_timing.py` (novo — unitário, sem tmux/mongo/rabbit reais)

**Interfaces:**
- Consumes: nada (método isolado de `CommandConsumer`).
- Produces: mesmo método público `_type_and_submit(name, text)`, só muda os tempos internos.

- [ ] **Step 1: Escrever o teste (monkeypatch de `_send_keys`/`_send_key`/`_pane_tail`/`asyncio.sleep`, sem infra real)**

Criar `worker/sessionflow_worker/tests/test_type_and_submit_timing.py`:

```python
"""Teste unitário do timing de `_type_and_submit` — sem tmux/mongo/rabbit reais.

Monkeypatch de `_send_keys`/`_send_key`/`_pane_tail`/`asyncio.sleep` pra medir
quantos re-Enters e quanto tempo de sleep o método usa, sem precisar de
infraestrutura de verdade (é lógica pura de retry/timing).
"""

from __future__ import annotations

import asyncio

import pytest

from sessionflow_worker.command_consumer import CommandConsumer


def _make_consumer() -> CommandConsumer:
    # Bypassa __init__ (pede channel/db reais) — _type_and_submit só usa
    # self._send_keys/_send_key/_pane_tail, que serão monkeypatched abaixo.
    return object.__new__(CommandConsumer)


@pytest.mark.asyncio
async def test_type_and_submit_retries_enter_at_most_once(monkeypatch):
    consumer = _make_consumer()
    sleeps: list[float] = []
    send_key_calls: list[str] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(consumer, "_send_keys", lambda name, text, enter=False: None)
    monkeypatch.setattr(
        consumer, "_send_key", lambda name, key: send_key_calls.append(key)
    )
    # Tela nunca muda → simula Enter "engolido" em todas as tentativas.
    monkeypatch.setattr(consumer, "_pane_tail", lambda name, lines=12: "tela-parada")

    await consumer._type_and_submit("sftest-x", "oi")

    enters = [k for k in send_key_calls if k == "Enter"]
    # 1 Enter inicial + no máximo 1 retry (era até 2 retries antes).
    assert len(enters) <= 2

    # Sleeps de retry (exclui o primeiro, que é o sleep pré-Enter proporcional
    # ao texto) devem ser <= 0.5s cada (era 1.3s antes).
    retry_sleeps = sleeps[1:]
    assert all(s <= 0.5 for s in retry_sleeps)


@pytest.mark.asyncio
async def test_type_and_submit_stops_retrying_once_screen_changes(monkeypatch):
    consumer = _make_consumer()
    send_key_calls: list[str] = []
    screens = iter(["tela-a", "tela-b", "tela-c"])  # muda logo na 1ª verificação

    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(consumer, "_send_keys", lambda name, text, enter=False: None)
    monkeypatch.setattr(
        consumer, "_send_key", lambda name, key: send_key_calls.append(key)
    )
    monkeypatch.setattr(consumer, "_pane_tail", lambda name, lines=12: next(screens))

    await consumer._type_and_submit("sftest-x", "oi")

    enters = [k for k in send_key_calls if k == "Enter"]
    assert len(enters) == 1  # tela mudou na 1ª checagem → não reenvia
```

- [ ] **Step 2: Rodar e verificar que falha**

```bash
cd worker && python -m pytest sessionflow_worker/tests/test_type_and_submit_timing.py -v
```

Esperado: FAIL no teste de `retry_sleeps <= 0.5` (hoje é 1.3s) e possivelmente no de `len(enters) <= 2` dependendo do timing atual (hoje pode chegar a 3 Enters: 1 inicial + 2 retries).

- [ ] **Step 3: Implementar**

Em `command_consumer.py:2157-2180`, trocar:

```python
    async def _type_and_submit(self, name: str, text: str) -> None:
        """Digita ``text`` e SUBMETE com um Enter SEPARADO (após uma pausa).

        Por que não mandar texto+Enter juntos: TUIs com *bracketed paste* (ex.:
        Claude Code) englobam o Enter grudado no paste como uma quebra de linha —
        o texto fica no input e NÃO envia (o usuário precisava dar Enter à mão).
        Enviando o texto, esperando o paste "fechar" e então mandando o Enter como
        evento próprio, a submissão acontece de forma confiável.
        """
        self._send_keys(name, text, enter=False)
        # Pausa proporcional ao tamanho (paste maior demora mais a assentar).
        # Teto maior (1,2s): transcrições de áudio são longas e o paste demora.
        await asyncio.sleep(min(1.2, 0.15 + len(text) / 3000))
        before = self._pane_tail(name)
        self._send_key(name, "Enter")
        # VERIFICAÇÃO: se a tela não mudou após o Enter, ele foi engolido (paste
        # ainda fechando / agente lento) e o texto ficou PRESO no input — manda
        # Enter de novo (até 2x). Se o agente já reagiu (tela mudou), para.
        for _ in range(2):
            await asyncio.sleep(1.3)
            after = self._pane_tail(name)
            if not before or not after or after != before:
                break
            self._send_key(name, "Enter")
```

por:

```python
    async def _type_and_submit(self, name: str, text: str) -> None:
        """Digita ``text`` e SUBMETE com um Enter SEPARADO (após uma pausa).

        Por que não mandar texto+Enter juntos: TUIs com *bracketed paste* (ex.:
        Claude Code) englobam o Enter grudado no paste como uma quebra de linha —
        o texto fica no input e NÃO envia (o usuário precisava dar Enter à mão).
        Enviando o texto, esperando o paste "fechar" e então mandando o Enter como
        evento próprio, a submissão acontece de forma confiável.
        """
        self._send_keys(name, text, enter=False)
        # Pausa proporcional ao tamanho (paste maior demora mais a assentar).
        # Teto maior (1,2s): transcrições de áudio são longas e o paste demora.
        await asyncio.sleep(min(1.2, 0.15 + len(text) / 3000))
        before = self._pane_tail(name)
        self._send_key(name, "Enter")
        # VERIFICAÇÃO: se a tela não mudou após o Enter, ele foi engolido (paste
        # ainda fechando / agente lento) e o texto ficou PRESO no input — manda
        # Enter de novo (só 1x; a UI já não depende deste tempo pra dar feedback
        # ao usuário — eco otimista no frontend cobre isso; aqui é só
        # reconciliação da tela real, então fica mais curto).
        await asyncio.sleep(0.4)
        after = self._pane_tail(name)
        if before and after and after == before:
            self._send_key(name, "Enter")
```

- [ ] **Step 4: Rodar e verificar que passa**

```bash
cd worker && python -m pytest sessionflow_worker/tests/test_type_and_submit_timing.py -v
```

Esperado: PASS nos 2 testes.

- [ ] **Step 5: Rodar a suíte de integração de input do worker (se broker/tmux/mongo disponíveis) pra garantir que a submissão real ainda funciona**

```bash
cd worker && python -m pytest sessionflow_worker/tests/test_consumer_input.py -v -m integration
```

Esperado: PASS (ou SKIP se infra não estiver de pé — mesmo comportamento de antes).

- [ ] **Step 6: Commit**

```bash
git add worker/sessionflow_worker/command_consumer.py worker/sessionflow_worker/tests/test_type_and_submit_timing.py
git commit -m "perf(worker): reduz retries de Enter de 2x1.3s pra 1x0.4s (reconciliação mais rápida)"
```

---

## Ordem de execução

Tasks 1→4 (frontend) são independentes de 5-6 (backend) e podem rodar em paralelo por subagentes distintos. Dentro do frontend, Task 1 deve vir antes da Task 2 (Task 2 edita o `send()` que a Task 1 simplifica primeiro); Tasks 3 e 4 são independentes entre si e das demais.
