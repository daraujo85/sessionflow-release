import { Injectable, inject, signal } from '@angular/core';
import { ApiService } from './api.service';
import { SseService } from './sse.service';

const POLL_INTERVAL_MS = 400;
const TIMEOUT_MS = 15_000;
const TOAST_TIMEOUT_MS = 6_000;

/**
 * Coordena o fluxo completo de clonar sessão:
 * 1. Abre uma popup `about:blank` sincronamente (captura user activation).
 * 2. Dispara POST `/sessions/{id}/clone`.
 * 3. Aguarda resultado SSE e navega a popup — ou fecha e mostra erro.
 *
 * Popups são indexadas por `commandId` (não por sessionId, que ainda não
 * existe no momento do clique) para que clones concorrentes não fechem ou
 * naveguem a janela um do outro.
 */
@Injectable({ providedIn: 'root' })
export class SessionCloneService {
  private readonly api = inject(ApiService);
  private readonly sse = inject(SseService);

  /** Popups abertas, uma por comando de clone em andamento. */
  private readonly popups = new Map<string, Window | null>();
  private toastTimer: ReturnType<typeof setTimeout> | null = null;

  readonly errorToast = signal<string | null>(null);

  clone(sessionId: string): void {
    this.clearToastTimer();
    this.errorToast.set(null);

    // Abre popup SÍNCRONO antes do async — preserva user activation.
    // Nome único por chamada: um nome fixo faria o navegador reaproveitar a
    // mesma janela do SO entre clones concorrentes.
    const w = 760;
    const h = 860;
    const left = Math.round(window.screenX + (window.outerWidth - w) / 2);
    const top = Math.round(window.screenY + (window.outerHeight - h) / 2);
    const windowName = `sf-janela-clone-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    // Sem `noopener`: pela spec do HTML, `noopener` faz window.open retornar
    // sempre null — perderíamos a referência para navegar a popup depois.
    // O destino final é same-origin, então a referência não é um risco.
    const popup = window.open(
      'about:blank',
      windowName,
      `popup=yes,width=${w},height=${h},left=${left},top=${top}`,
    );

    this.api.cloneSession(sessionId).subscribe({
      next: ({ command_id }) => {
        this.popups.set(command_id, popup);
        this.watch(command_id);
      },
      error: (err) => {
        this.closeWindow(popup);
        this.showError(
          err?.error?.detail || err?.error?.message || 'falha ao clonar sessão',
        );
      },
    });
  }

  private watch(commandId: string): void {
    const startedAt = Date.now();
    const timer = setInterval(() => {
      if (this.pollOnce(commandId)) {
        clearInterval(timer);
        return;
      }
      if (Date.now() - startedAt >= TIMEOUT_MS) {
        clearInterval(timer);
        this.closePopup(commandId);
        this.showError('clonagem solicitada — confira na lista de sessões');
      }
    }, POLL_INTERVAL_MS);
  }

  /** Devolve true quando o comando já entregou sucesso ou erro. */
  private pollOnce(commandId: string): boolean {
    const result = this.sse.commandResult(commandId);
    if (!result) {
      return false;
    }
    if (result.ok && result.session_id) {
      const popup = this.popups.get(commandId);
      if (popup) {
        popup.location.href = `${location.origin}/sessao/${result.session_id}`;
      }
      this.popups.delete(commandId);
      return true;
    }
    if (!result.ok) {
      this.closePopup(commandId);
      this.showError(result.error || 'falha ao clonar sessão');
      return true;
    }
    return false;
  }

  private showError(message: string): void {
    this.clearToastTimer();
    this.errorToast.set(message);
    this.toastTimer = setTimeout(() => {
      this.errorToast.set(null);
      this.toastTimer = null;
    }, TOAST_TIMEOUT_MS);
  }

  /** Fecha e esquece a popup de UM comando específico. */
  private closePopup(commandId: string): void {
    this.closeWindow(this.popups.get(commandId));
    this.popups.delete(commandId);
  }

  private closeWindow(w: Window | null | undefined): void {
    if (w) {
      try {
        w.close();
      } catch {
        /* cross-origin guard */
      }
    }
  }

  private clearToastTimer(): void {
    if (this.toastTimer) {
      clearTimeout(this.toastTimer);
      this.toastTimer = null;
    }
  }
}
