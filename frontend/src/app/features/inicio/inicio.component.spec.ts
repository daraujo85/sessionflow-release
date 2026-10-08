import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { Router } from '@angular/router';
import { of, throwError } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { InicioComponent } from './inicio.component';
import { API_BASE_URL, ApiService } from '../../core/api.service';
import { SseService } from '../../core/sse.service';
import { SessionCloneService } from '../../core/session-clone.service';
import { JarvisAudioService } from '../../core/jarvis-audio.service';
import { WorkersStore } from '../../core/workers-store';
import { AuthService } from '../../core/auth.service';

const BASE = 'http://localhost:8000';

describe('InicioComponent — sessões compartilhadas', () => {
  let fixture: ReturnType<typeof TestBed.createComponent<InicioComponent>>;
  let component: InicioComponent;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [InicioComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: API_BASE_URL, useValue: BASE },
        { provide: Router, useValue: { navigate: vi.fn() } },
        { provide: SseService, useValue: { connected: () => true, connect: vi.fn(), events: () => [], notifications: () => [], taskDoneFlash: () => null, taskDoneToast: () => null } },
        { provide: SessionCloneService, useValue: { clone: vi.fn() } },
        { provide: JarvisAudioService, useValue: { speakingSessionId: () => null } },
        { provide: WorkersStore, useValue: { hasMultipleHosts: () => false, hostname: () => null, emoji: () => null } },
        { provide: AuthService, useValue: { email: () => null } },
      ],
    });
    fixture = TestBed.createComponent(InicioComponent);
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
      if (req.request.url.endsWith('/remote-sessions')) {
        req.flush({ items: [], total: 0 });
      } else if (req.request.url.endsWith('/tasks')) {
        req.flush({ items: [], total: 0 });
      } else {
        req.flush({ items: [], total: 0 });
      }
    }
  }

  it('remove o bookmark compartilhado da Home depois do DELETE', () => {
    component['remoteSessions'].set([
      {
        id: 'remote-1',
        label: 'Outra conta',
        url: 'https://remote.example/s/session?k=token',
        created_at: null,
      },
    ]);

    component['removeRemoteSession'](component['remoteSessions']()[0]);

    const request = httpMock.expectOne(`${BASE}/remote-sessions/remote-1`);
    expect(request.request.method).toBe('DELETE');
    request.flush(null);

    expect(component['remoteSessions']()).toEqual([]);
  });
});

describe('InicioComponent — resposta rápida no hover', () => {
  let fixture: ReturnType<typeof TestBed.createComponent<InicioComponent>>;
  let component: InicioComponent;
  let httpMock: HttpTestingController;
  let apiSendInputSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    apiSendInputSpy = vi.fn().mockReturnValue(of(undefined));
    TestBed.configureTestingModule({
      imports: [InicioComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: API_BASE_URL, useValue: BASE },
        { provide: Router, useValue: { navigate: vi.fn() } },
        { provide: SseService, useValue: { connected: () => true, connect: vi.fn(), events: () => [], notifications: () => [], taskDoneFlash: () => null, taskDoneToast: () => null } },
        { provide: SessionCloneService, useValue: { clone: vi.fn() } },
        { provide: JarvisAudioService, useValue: { speakingSessionId: () => null } },
        { provide: WorkersStore, useValue: { hasMultipleHosts: () => false, hostname: () => null, emoji: () => null } },
        { provide: AuthService, useValue: { email: () => 'test@test.com' } },
        {
          provide: ApiService,
          useValue: {
            sendInput: apiSendInputSpy,
            listSessions: () => of([]),
            getTasks: () => of([]),
            listRemoteSessions: () => of([]),
          },
        },
      ],
    });
    fixture = TestBed.createComponent(InicioComponent);
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
      req.flush({ items: [], total: 0 });
    }
  }

  it('toggleQuickReply alterna o estado e salva no localStorage', () => {
    vi.stubGlobal('localStorage', { setItem: vi.fn(), getItem: vi.fn().mockReturnValue(null) });

    expect(component.quickReplyEnabled()).toBe(true);

    component.toggleQuickReply();

    expect(component.quickReplyEnabled()).toBe(false);
    expect(localStorage.setItem).toHaveBeenCalledWith('sf.inicio.quickReply', 'false');
  });

  it('openQuickReply abre o input para a sessão', () => {
    const mockEvent = { stopPropagation: vi.fn() } as unknown as MouseEvent;

    component.openQuickReply('session-123', mockEvent);

    expect(component.quickReplySession()).toBe('session-123');
    expect(component.quickReplyText()).toBe('');
    expect(component.quickReplyError()).toBeNull();
  });

  it('openQuickReply não abre se toggle está desligado', () => {
    component.quickReplyEnabled.set(false);
    const mockEvent = { stopPropagation: vi.fn() } as unknown as MouseEvent;

    component.openQuickReply('session-123', mockEvent);

    expect(component.quickReplySession()).toBeNull();
  });

  it('closeQuickReply limpa o estado', () => {
    component.quickReplySession.set('session-123');
    component.quickReplyText.set('some text');
    component.quickReplyError.set('error');

    component.closeQuickReply();

    expect(component.quickReplySession()).toBeNull();
    expect(component.quickReplyText()).toBe('');
    expect(component.quickReplyError()).toBeNull();
  });

  it('sendQuickReply chama API e limpa ao sucesso', () => {
    apiSendInputSpy.mockReturnValue(of({}));
    component.quickReplySession.set('session-123');
    component.quickReplyText.set('  Hello world  ');

    component.sendQuickReply('session-123', { preventDefault: vi.fn() } as unknown as KeyboardEvent);

    expect(apiSendInputSpy).toHaveBeenCalledWith('session-123', 'Hello world', true);
    expect(component.quickReplySending()).toBe(false);
    expect(component.quickReplyText()).toBe('');
    expect(component.quickReplySession()).toBeNull();
  });

  it('sendQuickReply mostra erro em falha e mantém o texto', () => {
    apiSendInputSpy.mockReturnValue(throwError(() => new Error('Network error')));
    component.quickReplySession.set('session-123');
    component.quickReplyText.set('Hello');

    component.sendQuickReply('session-123', { preventDefault: vi.fn() } as unknown as KeyboardEvent);

    expect(component.quickReplySending()).toBe(false);
    expect(component.quickReplyError()).toBe('Erro ao enviar');
    expect(component.quickReplyText()).toBe('Hello');
  });

  it('sendQuickReply ignora texto vazio', () => {
    component.quickReplyText.set('   ');
    component.sendQuickReply('session-123', { preventDefault: vi.fn() } as unknown as KeyboardEvent);
    expect(apiSendInputSpy).not.toHaveBeenCalled();
  });

  it('onQuickReplyKeydown com Enter chama sendQuickReply', () => {
    const sendSpy = vi.spyOn(component, 'sendQuickReply');
    const event = { key: 'Enter', stopPropagation: vi.fn(), preventDefault: vi.fn() } as unknown as KeyboardEvent;

    component.onQuickReplyKeydown('session-1', event);

    expect(sendSpy).toHaveBeenCalledWith('session-1', event);
  });

  it('onQuickReplyKeydown com Escape chama closeQuickReply', () => {
    const closeSpy = vi.spyOn(component, 'closeQuickReply');
    const event = { key: 'Escape', stopPropagation: vi.fn() } as unknown as KeyboardEvent;

    component.onQuickReplyKeydown('session-1', event);

    expect(closeSpy).toHaveBeenCalled();
  });

  it('onQuickReplyClick faz stopPropagation para evitar navegação', () => {
    const event = { stopPropagation: vi.fn() } as unknown as MouseEvent;

    component.onQuickReplyClick(event);

    expect(event.stopPropagation).toHaveBeenCalled();
  });
});
