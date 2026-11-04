import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { Router } from '@angular/router';
import { describe, expect, it, vi } from 'vitest';

import { CriarComponent } from './criar.component';
import { API_BASE_URL } from '../../core/api.service';
import { WorkersStore } from '../../core/workers-store';

const BASE = 'http://localhost:8000';

/**
 * Sessão "Comando" (agent_type="command"): pane tmux com o shell do host,
 * SEM agente LLM. Consequências no form:
 *   - não há Modelo nem Esforço (o endpoint /models é por-agente);
 *   - work_dir é OPCIONAL (vazio = HOME do host).
 */
describe('CriarComponent — sessão "Comando"', () => {
  let fixture: ReturnType<typeof TestBed.createComponent<CriarComponent>>;
  let component: CriarComponent;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [CriarComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: API_BASE_URL, useValue: BASE },
        { provide: Router, useValue: { navigate: vi.fn() } },
        {
          provide: WorkersStore,
          useValue: {
            workers: () => [],
            hasMultipleHosts: () => false,
            hostname: () => null,
            emoji: () => null,
            refresh: vi.fn(),
          },
        },
      ],
    });
    fixture = TestBed.createComponent(CriarComponent);
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
    fixture.detectChanges();
    drainBackgroundRequests();
  });

  afterEach(() => {
    drainBackgroundRequests();
    httpMock.verify();
  });

  function drainBackgroundRequests(): void {
    for (const req of httpMock.match(() => true)) {
      if (req.request.url.includes('/models')) {
        req.flush({ items: [] });
      } else {
        req.flush({});
      }
    }
  }

  it('oferece o card "Comando" na grade de agentes', () => {
    expect(component['agents']).toContain('command');
    const labels = Array.from(
      fixture.nativeElement.querySelectorAll('.agent-card .agent-label') as NodeListOf<Element>,
    ).map((el) => el.textContent?.trim());
    expect(labels).toContain('Comando');
  });

  it('selecionar "Comando" limpa modelo e esforço e NÃO busca /models', () => {
    component['model'].set('claude-tudao');
    component['effort'].set('Alto');

    component['selectAgent']('command');

    expect(component['model']()).toBe('');
    expect(component['freeModel']()).toBe('');
    expect(component['effort']()).toBeNull();
    expect(component['models']()).toEqual([]);
    expect(httpMock.match(() => false)).toEqual([]);
  });

  it('dispensa work_dir: só o slug é necessário para submeter', () => {
    component['name'].set('cmd mac');
    component['selectAgent']('command');

    expect(component['workDir']()).toBe('');
    expect(component['canSubmit']()).toBe(true);
  });

  it('outros agentes continuam exigindo work_dir', () => {
    component['name'].set('minha sessao');
    component['selectAgent']('codex');

    expect(component['canSubmit']()).toBe(false);
    component['onDirInput']('/tmp/x');
    expect(component['canSubmit']()).toBe(true);
  });

  it('envia agent_type=command sem model nem effort', () => {
    component['name'].set('lab mac');
    component['selectAgent']('command');

    component['submit']();

    const req = httpMock.expectOne(`${BASE}/sessions`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body.agent_type).toBe('command');
    expect(req.request.body.model).toBeNull();
    expect(req.request.body.effort).toBeNull();
    expect(req.request.body.work_dir).toBe('');
    req.flush({});
  });

  it('não esconde o body do modo "Rápida" quando o agente é comando', () => {
    component['mode'].set('rapida');
    component['selectAgent']('command');
    fixture.detectChanges();

    // O modo Rápida é renderizado dentro da cadeia @else do campo Modelo —
    // se o campo inteiro sumisse, o corpo do modo Rápida sumiria junto.
    expect(fixture.nativeElement.textContent).toContain('Criar sessão rápida');
  });
});
