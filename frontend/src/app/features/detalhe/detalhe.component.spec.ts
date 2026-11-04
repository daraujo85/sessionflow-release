import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { ActivatedRoute } from '@angular/router';
import { BehaviorSubject, of } from 'rxjs';
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
          useValue: {
            snapshot: {
              paramMap: { get: () => 'sess-1' },
              queryParamMap: { get: () => null },
              data: {},
            },
            paramMap: of({ get: () => 'sess-1' }),
            queryParamMap: of({ get: () => null }),
          },
        },
      ],
    });
    fixture = TestBed.createComponent(DetalheComponent);
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
    fixture.detectChanges();
    drainBackgroundRequests(); // ngOnInit dispara workers/sessão/tela/marcos em paralelo
  });

  afterEach(() => {
    drainBackgroundRequests(); // refreshBurst() dispara uma tela extra após o POST de sucesso
    httpMock.verify();
  });

  /**
   * Responde com stubs neutros qualquer chamada de fundo (polling de tela,
   * lista de workers, sessão, instruct-milestones) que não é o foco do teste
   * — sem isso `httpMock.verify()` acusa requisição pendente.
   */
  function drainBackgroundRequests(): void {
    for (const req of httpMock.match(() => true)) {
      if (req.request.url.endsWith('/screen')) {
        req.flush({ text: '' });
      } else if (req.request.url.endsWith('/workers')) {
        req.flush([]);
      } else {
        req.flush({});
      }
    }
  }

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

  it('hint de erro sobrevive a um refreshScreen (poll/SSE) — só dismissHint() limpa', () => {
    (component as any).draft.set('ls -la');
    (component as any).send();
    const sendReq = httpMock.expectOne(`${BASE}/sessions/sess-1/input`);
    sendReq.flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });

    expect((component as any).actionHintKind()).toBe('error');

    // Simula a tela remota mudando (poll) enquanto o erro está visível.
    (component as any).refreshScreen();
    const screenReq = httpMock.expectOne(`${BASE}/sessions/sess-1/screen`);
    screenReq.flush({ text: 'novo conteúdo no terminal' });

    expect((component as any).actionHint()).toContain('ls -la');
    expect((component as any).actionHintKind()).toBe('error');

    (component as any).dismissHint();

    expect((component as any).actionHint()).toBeNull();
    expect((component as any).actionHintKind()).toBeNull();
  });

  it('restaura o texto digitado no draft quando o POST de envio falha', () => {
    (component as any).draft.set('ls -la');
    (component as any).send();

    // Eco otimista já limpou o draft antes da resposta HTTP.
    expect((component as any).draft()).toBe('');

    const req = httpMock.expectOne(`${BASE}/sessions/sess-1/input`);
    req.flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });

    expect((component as any).draft()).toBe('ls -la');
    expect((component as any).drafts.get('sess-1')).toBe('ls -la');
  });
});

describe('DetalheComponent — troca de sessão limpa hint de erro', () => {
  let fixture: ReturnType<typeof TestBed.createComponent<DetalheComponent>>;
  let component: DetalheComponent;
  let httpMock: HttpTestingController;
  let paramMap$: BehaviorSubject<{ get: (key: string) => string | null }>;

  function drainBackgroundRequests(): void {
    for (const req of httpMock.match(() => true)) {
      if (req.request.url.endsWith('/screen')) {
        req.flush({ text: '' });
      } else if (req.request.url.endsWith('/workers')) {
        req.flush([]);
      } else {
        req.flush({});
      }
    }
  }

  beforeEach(() => {
    paramMap$ = new BehaviorSubject({ get: (key: string) => (key === 'id' ? 'sess-1' : null) as string | null });
    TestBed.configureTestingModule({
      imports: [DetalheComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: API_BASE_URL, useValue: BASE },
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: {
              paramMap: { get: (key: string) => (key === 'id' ? 'sess-1' : null) as string | null },
              queryParamMap: { get: () => null },
              data: {},
            },
            paramMap: paramMap$,
            queryParamMap: of({ get: () => null }),
          },
        },
      ],
    });
    fixture = TestBed.createComponent(DetalheComponent);
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
    fixture.detectChanges();
    drainBackgroundRequests();
  });

  afterEach(() => {
    drainBackgroundRequests();
    httpMock.verify();
  });

  it('limpa o chip de erro da sessão anterior ao navegar pra outra sessão', () => {
    (component as any).draft.set('ls -la');
    (component as any).send();
    const req = httpMock.expectOne(`${BASE}/sessions/sess-1/input`);
    req.flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });

    expect((component as any).actionHintKind()).toBe('error');

    // Navega pra outra sessão.
    paramMap$.next({ get: (key: string) => (key === 'id' ? 'sess-2' : null) as string | null });
    drainBackgroundRequests();

    expect((component as any).actionHint()).toBeNull();
    expect((component as any).actionHintKind()).toBeNull();
  });
});

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
          useValue: {
            snapshot: { paramMap: { get: () => 'sess-1' }, queryParamMap: { get: () => null }, data: {} },
            paramMap: of({ get: () => 'sess-1' }),
            queryParamMap: of({ get: () => null }),
          },
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

  it('dispara no máximo 2 GET /screen (imediato + 1 poll de segurança) em 3s', async () => {
    (component as any).refreshBurst();

    await new Promise((resolve) => setTimeout(resolve, 3000));

    const reqs = httpMock.match(`${BASE}/sessions/sess-1/screen`);
    expect(reqs.length).toBeLessThanOrEqual(2);
    reqs.forEach((r) => r.flush({ text: '', scrollback: '', at: new Date().toISOString() }));
  });
});

describe('DetalheComponent — scroll nativo do histórico', () => {
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
          useValue: {
            snapshot: {
              paramMap: { get: () => 'sess-1' },
              queryParamMap: { get: () => null },
              data: {},
            },
            paramMap: of({ get: () => 'sess-1' }),
            queryParamMap: of({ get: () => null }),
          },
        },
      ],
    });
    fixture = TestBed.createComponent(DetalheComponent);
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
    fixture.detectChanges();
    for (const req of httpMock.match(() => true)) {
      req.flush(req.request.url.endsWith('/workers') ? [] : { text: '' });
    }
  });

  afterEach(() => {
    for (const req of httpMock.match(() => true)) {
      req.flush({});
    }
    httpMock.verify();
  });

  it('rolar pro topo só mostra a pill "ao vivo" — sem buscar histórico nem congelar', () => {
    (component as any).screen.set('linha1\nlinha2\nlinha3');
    const el = (component as any).termEl()!.nativeElement as HTMLElement;
    Object.defineProperty(el, 'scrollHeight', { value: 500, configurable: true });
    Object.defineProperty(el, 'clientHeight', { value: 100, configurable: true });
    el.scrollTop = 0;

    (component as any).onTermScroll();

    expect((component as any).showLivePill()).toBe(true);
    expect((component as any).stickToBottom).toBe(false);
    expect(httpMock.match((r) => r.url.includes('/output')).length).toBe(0);
  });

  it('▲ rola o container localmente (não manda tecla pro agente)', () => {
    const el = (component as any).termEl()!.nativeElement as HTMLElement;
    Object.defineProperty(el, 'clientHeight', { value: 200, configurable: true });
    const calls: ScrollToOptions[] = [];
    el.scrollBy = ((opts: ScrollToOptions) => calls.push(opts)) as any;

    (component as any).scrollTerm('up');

    expect(calls[0].top).toBe(-100);
    expect(httpMock.match((r) => r.url.endsWith('/key')).length).toBe(0);
  });

  it('refreshScreen usa o scrollback (histórico) quando vem no doc', () => {
    (component as any).refreshScreen();
    const req = httpMock.expectOne(`${BASE}/sessions/sess-1/screen`);
    req.flush({ text: 'tela', scrollback: 'antiga\ntela', at: null });

    expect((component as any).screen()).toBe('antiga\ntela');
  });

  it('modelo do card vem do SEGMENTO do modelo na statusline (não do bloco JEV)', () => {
    // Statusline real do Claude Code (hooks/jarvis-statusline.sh):
    //   ●JARVIS on │ worker ⎇ main │ claude-tudao │ ⚡JEV on 6✓
    // Pegar o ÚLTIMO segmento devolvia "⚡JEV on 6✓" no lugar do modelo.
    (component as any).screen.set(
      'conversa\n●JARVIS on │ worker ⎇ main │ claude-tudao │ ⚡JEV on 6✓',
    );

    expect((component as any).terminalModel()).toBe('claude-tudao');
    expect((component as any).modelLabel()).toBe('claude-tudao');
  });

  it('mostra o combo e o modelo usado entre parênteses', () => {
    (component as any).screen.set(
      '●JARVIS on │ worker ⎇ main │ claude-tudao │ ⚡JEV on 6✓',
    );
    // O modelo usado vem das métricas: o `model` que o 9router devolveu na
    // RESPOSTA (o combo é um roteador com failover — pode ser outro).
    (component as any).session.set({ metrics: { model: 'claude-opus-5-5' } });

    expect((component as any).modelLabel()).toBe('claude-tudao (claude-opus-5-5)');
  });

  it('não duplica o combo quando o modelo usado é o mesmo', () => {
    (component as any).screen.set(
      '●JARVIS on │ worker ⎇ main │ claude-tudao │ ⚡JEV on 6✓',
    );
    (component as any).session.set({ metrics: { model: 'Tudão' } });

    expect((component as any).modelLabel()).toBe('claude-tudao');
  });

  it('ignora token de modelo que apareça no branch (o modelo é o penúltimo)', () => {
    // O dir/branch é o MESMO segmento na statusline: se o branch tiver
    // "gpt" no nome, um "primeiro que casa" pegaria o branch, não o modelo.
    (component as any).screen.set(
      '●JARVIS on │ worker ⎇ feat/gpt-cache │ claude-tudao │ ⚡JEV on 6✓',
    );

    expect((component as any).terminalModel()).toBe('claude-tudao');
  });

  it('devolve vazio quando a statusline não tem o segmento de modelo', () => {
    // O campo MODELO é opcional no script: sem ele a linha fica
    // `JARVIS │ dir ⎇ branch │ JEV` — o dir NÃO pode virar o modelo.
    (component as any).screen.set(
      '●JARVIS on │ worker ⎇ main │ ⚡JEV on 6✓',
    );

    expect((component as any).terminalModel()).toBe('');
    expect((component as any).modelLabel()).toBe('—');
  });

  it('não usa o branch como modelo mesmo com token de modelo no nome', () => {
    // Sem o segmento MODELO, o penúltimo é o dir/branch — e ele carrega o
    // marcador "⎇". O "gpt" do branch não pode virar o modelo do card.
    (component as any).screen.set(
      '●JARVIS on │ worker ⎇ feat/gpt-cache │ ⚡JEV on 6✓',
    );

    expect((component as any).terminalModel()).toBe('');
  });

  it('aceita o rótulo amigável do modelo (Tudão) como modelo do combo', () => {
    // A statusline pode mostrar o rótulo ("Tudão") em vez do id
    // ("claude-tudao") — o worker usa os mesmos rótulos em metrics.model.
    (component as any).screen.set(
      '●JARVIS on │ worker ⎇ main │ Tudão │ ⚡JEV on 6✓',
    );

    expect((component as any).terminalModel()).toBe('Tudão');
  });
});

describe('DetalheComponent — fila de demandas', () => {
  const TEST_SESSION_ID = 'sess-1';
  let fixture: ReturnType<typeof TestBed.createComponent<DetalheComponent>>;
  let component: DetalheComponent;
  let httpMock: HttpTestingController;

  function drainBackgroundRequests(): void {
    for (const req of httpMock.match(() => true)) {
      if (req.request.url.endsWith('/screen')) {
        req.flush({ text: '' });
      } else if (req.request.url.endsWith('/workers')) {
        req.flush([]);
      } else if (req.request.url.endsWith(`/sessions/${TEST_SESSION_ID}/demands`)) {
        // deixa pro teste responder — não é ruído de fundo
        continue;
      } else {
        req.flush({});
      }
    }
  }

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [DetalheComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: API_BASE_URL, useValue: BASE },
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: {
              paramMap: { get: () => TEST_SESSION_ID },
              queryParamMap: { get: () => null },
              data: {},
            },
            paramMap: of({ get: () => TEST_SESSION_ID }),
            queryParamMap: of({ get: () => null }),
          },
        },
      ],
    });
    fixture = TestBed.createComponent(DetalheComponent);
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
    fixture.detectChanges();
    drainBackgroundRequests();
  });

  afterEach(() => {
    drainBackgroundRequests();
    httpMock.verify();
  });

  it('abre o painel e carrega a lista via GET /sessions/{id}/demands', () => {
    component['toggleDemands']();
    fixture.detectChanges();
    expect(component['demandsOpen']()).toBe(true);

    const req = httpMock.expectOne((r) => r.url.endsWith(`/sessions/${TEST_SESSION_ID}/demands`));
    req.flush({
      items: [
        {
          id: 'd1',
          base_session_id: TEST_SESSION_ID,
          text: 'fazer relatório mensal',
          status: 'pending',
          target_session_id: null,
          branch: null,
          created_at: null,
          started_at: null,
          completed_at: null,
          summary: null,
          error_note: null,
        },
      ],
      total: 1,
    });
    fixture.detectChanges();

    expect(component['demands']().length).toBe(1);
    const text = fixture.nativeElement.querySelector('.demand-text')?.textContent;
    expect(text).toContain('fazer relatório mensal');
  });

  it('cria uma demanda nova via POST e prepende na lista', () => {
    component['toggleDemands']();
    fixture.detectChanges();
    httpMock.expectOne((r) => r.url.endsWith(`/sessions/${TEST_SESSION_ID}/demands`)).flush({
      items: [],
      total: 0,
    });

    component['newDemandText'].set('nova demanda');
    component['createDemand']();

    const req = httpMock.expectOne(
      (r) => r.url.endsWith(`/sessions/${TEST_SESSION_ID}/demands`) && r.method === 'POST',
    );
    expect(req.request.body).toEqual({ text: 'nova demanda' });
    req.flush({
      id: 'd2',
      base_session_id: TEST_SESSION_ID,
      text: 'nova demanda',
      status: 'pending',
      target_session_id: null,
      branch: null,
      created_at: null,
      started_at: null,
      completed_at: null,
      summary: null,
      error_note: null,
    });
    fixture.detectChanges();

    expect(component['demands']()[0].text).toBe('nova demanda');
    expect(component['newDemandText']()).toBe('');
  });
});

describe('DetalheComponent — mover sessão de host', () => {
  const SID = 'sess-1';
  let fixture: ReturnType<typeof TestBed.createComponent<DetalheComponent>>;
  let component: DetalheComponent;
  let httpMock: HttpTestingController;

  const WORKERS = [
    {
      online: true, hostname: 'mac', display_name: 'MacBook', emoji: '💻', host_id: 'host-a',
      uptime_seconds: 1, started_at: null, updated_at: null,
      metrics: { cpu_pct: 10, mem_pct: 50, mem_total_gb: 16, mem_avail_gb: 8 },
    },
    {
      online: true, hostname: 'duck', display_name: 'Duck', emoji: '🦆', host_id: 'host-b',
      uptime_seconds: 1, started_at: null, updated_at: null,
      metrics: { cpu_pct: 23.4, mem_pct: 40, mem_total_gb: 32, mem_avail_gb: 12.5 },
    },
    {
      online: false, hostname: 'off', display_name: 'Desligado', emoji: null, host_id: 'host-c',
      uptime_seconds: null, started_at: null, updated_at: null,
    },
  ];

  function baseSession(extra: Record<string, unknown> = {}) {
    return {
      id: SID, tmux_name: 'sess', display_name: 'Sess', agent_type: 'claude',
      model: null, effort: null, work_dir: '/home/x/proj', status: 'running',
      origin: 'app', host_id: 'host-a', ...extra,
    };
  }

  /** Responde as chamadas de fundo; a sessão vem de `sessionDoc`. */
  function drain(sessionDoc: Record<string, unknown>): void {
    // Repete: responder a sessão dispara GET /tasks logo em seguida.
    for (let i = 0; i < 5; i++) {
      const pending = httpMock
        .match(() => true)
        .filter((r) => !r.request.url.endsWith(`/sessions/${SID}/move`));
      if (!pending.length) {
        return;
      }
      for (const req of pending) {
        flushBackground(req, sessionDoc);
      }
    }
  }

  function flushBackground(
    req: ReturnType<HttpTestingController['expectOne']>,
    sessionDoc: Record<string, unknown>,
  ): void {
    const url = req.request.url;
    if (url.endsWith('/screen')) {
      req.flush({ text: '' });
    } else if (url.endsWith('/workers')) {
      req.flush(WORKERS);
    } else if (url.endsWith(`/sessions/${SID}`) && req.request.method === 'GET') {
      req.flush(sessionDoc);
    } else {
      req.flush({});
    }
  }

  function setup(sessionDoc: Record<string, unknown>): void {
    TestBed.configureTestingModule({
      imports: [DetalheComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: API_BASE_URL, useValue: BASE },
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: {
              paramMap: { get: () => SID },
              queryParamMap: { get: () => null },
              data: {},
            },
            paramMap: of({ get: () => SID }),
            queryParamMap: of({ get: () => null }),
          },
        },
      ],
    });
    fixture = TestBed.createComponent(DetalheComponent);
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
    fixture.detectChanges();
    drain(sessionDoc);
    fixture.detectChanges();
  }

  afterEach(() => {
    vi.restoreAllMocks();
    drain(baseSession());
    httpMock.verify();
  });

  it('lista só hosts online diferentes do atual, com emoji, CPU e memória livre', () => {
    setup(baseSession());
    const opts = (component as any).moveHostOptions();
    expect(opts.map((o: { hostId: string }) => o.hostId)).toEqual(['host-b']);
    expect(opts[0].label).toContain('🦆');
    expect(opts[0].label).toContain('Duck');
    expect(opts[0].label).toContain('⚡ CPU 23%');
    expect(opts[0].label).toContain('🧠 12.5 GB livre');
  });

  it('seletor desabilitado com dica quando a sessão não é claude', async () => {
    setup(baseSession({ agent_type: 'codex' }));
    (component as any).openRename();
    fixture.detectChanges();
    await fixture.whenStable(); // ngModel aplica o disabled de forma assíncrona
    fixture.detectChanges();
    expect((component as any).moveBlockedReason()).toContain('claude');
    const sel = fixture.nativeElement.querySelector('select.move-host-select') as HTMLSelectElement;
    expect(sel).toBeTruthy();
    expect(sel.disabled).toBe(true);
  });

  it('seletor desabilitado com dica quando a sessão está em worktree', () => {
    setup(baseSession({ worktree_path: '/home/x/proj-clones/t1' }));
    expect((component as any).moveBlockedReason()).toContain('worktree');
  });

  it('salvar com host novo confirma e chama POST /move', () => {
    setup(baseSession());
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    (component as any).openRename();
    (component as any).moveTarget.set('host-b');
    (component as any).saveRename();

    expect(confirmSpy).toHaveBeenCalledOnce();
    expect(confirmSpy.mock.calls[0][0]).toContain('Duck');
    const req = httpMock.expectOne((r) => r.url.endsWith(`/sessions/${SID}/move`));
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ target_host_id: 'host-b' });
    req.flush({ command_id: 'c1', status: 'accepted' });
  });

  it('salvar com host novo e confirmação negada NÃO chama /move', () => {
    setup(baseSession());
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    (component as any).openRename();
    (component as any).moveTarget.set('host-b');
    (component as any).saveRename();
    httpMock.expectNone((r) => r.url.endsWith(`/sessions/${SID}/move`));
  });

  it('mostra "movendo para <host>…" com moving presente', () => {
    setup(baseSession({
      moving: {
        target_host_id: 'host-b', target_work_dir: '/srv/proj', phase: 'importing',
        error: null, started_at: '2026-10-03T00:00:00Z',
      },
    }));
    const el = fixture.nativeElement.querySelector('.move-badge') as HTMLElement;
    expect(el?.textContent).toContain('movendo para');
    expect(el?.textContent).toContain('Duck');
  });

  it('com phase=failed mostra o erro e permite fechar o aviso', () => {
    setup(baseSession({
      moving: {
        target_host_id: 'host-b', target_work_dir: '/srv/proj', phase: 'failed',
        error: 'Há mudanças não commitadas em MacBook: faça commit/push antes de mover.',
        started_at: '2026-10-03T00:00:00Z',
      },
    }));
    const el = fixture.nativeElement.querySelector('.move-badge--failed') as HTMLElement;
    expect(el?.textContent).toContain('mudanças não commitadas');
    (component as any).dismissMoveError();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.move-badge--failed')).toBeNull();
  });
});

/**
 * Sessão "Comando" (agent_type="command"): pane tmux com o shell do host,
 * SEM agente LLM. Não há modelo, tokens nem contexto — o bloco de métricas
 * vira um resumo mínimo ("Shell no host — sem agente") em vez de mostrar
 * tudo como "—".
 */
describe('DetalheComponent — sessão de comando', () => {
  const SID = 'sess-cmd';
  let fixture: ReturnType<typeof TestBed.createComponent<DetalheComponent>>;
  let component: DetalheComponent;
  let httpMock: HttpTestingController;

  function commandSession(): Record<string, unknown> {
    return {
      id: SID, tmux_name: 'cmd-mac', display_name: 'cmd-mac', agent_type: 'command',
      model: null, effort: null, work_dir: '/home/diego', status: 'running',
      origin: 'app', host_id: 'host-a',
    };
  }

  function drain(sessionDoc: Record<string, unknown>): void {
    for (let i = 0; i < 5; i++) {
      const pending = httpMock.match(() => true);
      if (!pending.length) {
        return;
      }
      for (const req of pending) {
        if (req.request.url.endsWith('/screen')) {
          req.flush({ text: '' });
        } else if (req.request.url.endsWith('/workers')) {
          req.flush([]);
        } else if (req.request.url.endsWith(`/sessions/${SID}`) && req.request.method === 'GET') {
          req.flush(sessionDoc);
        } else {
          req.flush({});
        }
      }
    }
  }

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [DetalheComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: API_BASE_URL, useValue: BASE },
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: {
              paramMap: { get: () => SID },
              queryParamMap: { get: () => null },
              data: {},
            },
            paramMap: of({ get: () => SID }),
            queryParamMap: of({ get: () => null }),
          },
        },
      ],
    });
    fixture = TestBed.createComponent(DetalheComponent);
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
    fixture.detectChanges();
    drain(commandSession());
    fixture.detectChanges();
  });

  afterEach(() => {
    drain(commandSession());
    httpMock.verify();
  });

  it('reconhece a sessão de comando (isCommand)', () => {
    expect((component as any).isCommand()).toBe(true);
  });

  it('mostra o resumo do shell em vez das métricas de modelo/contexto', () => {
    const metrics = fixture.nativeElement.querySelector('.metrics') as HTMLElement;
    expect(metrics?.getAttribute('aria-label')).toBe('Sessão de comando');
    expect(metrics?.textContent).toContain('Sessão de comando');
    expect(metrics?.textContent).toContain('Shell no host — sem agente');
    // Nada de "Contexto usado" (não há tokens numa sessão de shell).
    expect(metrics?.textContent).not.toContain('Contexto usado');
  });

  it('não mostra o bloco de métricas normal', () => {
    const labels = Array.from(
      fixture.nativeElement.querySelectorAll('.metrics') as NodeListOf<HTMLElement>,
    ).map((el) => el.getAttribute('aria-label'));
    expect(labels).toEqual(['Sessão de comando']);
  });
});
