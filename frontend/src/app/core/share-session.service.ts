import { Injectable, signal } from '@angular/core';

/**
 * Guarda o TOKEN do link compartilhável quando o app está em "modo convidado"
 * (rota `/s/:id?k=<token>`). Não há login: o token vem da URL e é injetado nas
 * chamadas à API (interceptor) e no SSE (query `?k=`), escopado pelo backend a
 * uma única sessão. Fora do modo convidado, fica `null` e tudo segue normal.
 */
@Injectable({ providedIn: 'root' })
export class ShareSessionService {
  /** Token de share ativo (null = não está em modo convidado). */
  readonly token = signal<string | null>(null);

  set(token: string | null): void {
    this.token.set(token && token.trim() ? token.trim() : null);
  }

  clear(): void {
    this.token.set(null);
  }

  /** Está em modo convidado (abriu via link compartilhável)? */
  isGuest(): boolean {
    return this.token() !== null;
  }
}
