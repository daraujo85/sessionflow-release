import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiService } from './api.service';
import { SessionCloneService } from './session-clone.service';
import { SseService } from './sse.service';

describe('SessionCloneService', () => {
  let service: SessionCloneService;
  let api: { cloneSession: ReturnType<typeof vi.fn> };
  let sse: { commandResult: ReturnType<typeof vi.fn> };
  let openSpy: ReturnType<typeof vi.fn>;
  let popup: { location: { href: string }; close: ReturnType<typeof vi.fn> };

  beforeEach(() => {
    api = { cloneSession: vi.fn() };
    sse = { commandResult: vi.fn() };
    TestBed.configureTestingModule({
      providers: [
        { provide: ApiService, useValue: api },
        { provide: SseService, useValue: sse },
      ],
    });
    service = TestBed.inject(SessionCloneService);
    popup = { location: { href: '' }, close: vi.fn() };
    openSpy = vi.fn().mockReturnValue(popup);
    vi.stubGlobal('open', openSpy);
  });

  afterEach(() => vi.unstubAllGlobals());

  it('opens a blank popup synchronously (no noopener) and navigates it after SSE confirms the clone', () => {
    api.cloneSession.mockReturnValue(of({ command_id: 'cmd-1' }));

    service.clone('source-id');

    expect(openSpy).toHaveBeenCalledWith(
      'about:blank',
      expect.stringMatching(/^sf-janela-clone-/),
      expect.stringContaining('popup=yes'),
    );
    // noopener faria window.open sempre retornar null — nunca deve ser passado.
    expect(openSpy.mock.calls[0][2]).not.toContain('noopener');
    expect(popup.location.href).toBe('');
    sse.commandResult.mockReturnValue({ command_id: 'cmd-1', ok: true, session_id: 'clone-id' });

    expect(service['pollOnce']('cmd-1')).toBe(true);
    expect(popup.location.href).toContain('/sessao/clone-id');
    expect(popup.close).not.toHaveBeenCalled();
  });

  it('keeps concurrent clones isolated: resolving the first never touches the second popup', () => {
    const popupA = { location: { href: '' }, close: vi.fn() };
    const popupB = { location: { href: '' }, close: vi.fn() };
    openSpy.mockReturnValueOnce(popupA).mockReturnValueOnce(popupB);
    api.cloneSession
      .mockReturnValueOnce(of({ command_id: 'cmd-a' }))
      .mockReturnValueOnce(of({ command_id: 'cmd-b' }));

    service.clone('source-a');
    service.clone('source-b');

    sse.commandResult.mockReturnValue({ command_id: 'cmd-a', ok: true, session_id: 'clone-a' });
    expect(service['pollOnce']('cmd-a')).toBe(true);

    expect(popupA.location.href).toContain('/sessao/clone-a');
    expect(popupB.location.href).toBe('');
    expect(popupA.close).not.toHaveBeenCalled();
    expect(popupB.close).not.toHaveBeenCalled();
  });

  it('closes the pending popup and shows the worker error', () => {
    api.cloneSession.mockReturnValue(of({ command_id: 'cmd-2' }));
    service.clone('source-id');
    sse.commandResult.mockReturnValue({ command_id: 'cmd-2', ok: false, error: 'disco cheio' });

    expect(service['pollOnce']('cmd-2')).toBe(true);
    expect(popup.close).toHaveBeenCalledOnce();
    expect(service.errorToast()).toBe('disco cheio');
  });

  it('closes the pending popup when the clone request fails', () => {
    api.cloneSession.mockReturnValue(
      throwError(() => ({ error: { detail: 'sessão não pode ser clonada' } })),
    );

    service.clone('source-id');

    expect(popup.close).toHaveBeenCalledOnce();
    expect(service.errorToast()).toBe('sessão não pode ser clonada');
  });
});
