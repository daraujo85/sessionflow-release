import { Injectable } from '@angular/core';

/**
 * Guarda o rascunho do input POR sessão, sobrevivendo à navegação (o detalhe é
 * destruído ao trocar de tela) e ao reload (persistido em localStorage). Limpo
 * ao enviar a mensagem.
 */
@Injectable({ providedIn: 'root' })
export class DraftStore {
  private readonly key = 'sf.drafts';
  private drafts: Record<string, string> = this.load();

  /** Rascunho da sessão (ou '' se não houver). */
  get(sessionId: string): string {
    return (sessionId && this.drafts[sessionId]) || '';
  }

  /** Salva o rascunho; texto vazio remove a entrada. */
  set(sessionId: string, text: string): void {
    if (!sessionId) {
      return;
    }
    if (text) {
      this.drafts[sessionId] = text;
    } else {
      delete this.drafts[sessionId];
    }
    this.save();
  }

  private load(): Record<string, string> {
    try {
      return JSON.parse(localStorage.getItem(this.key) || '{}');
    } catch {
      return {};
    }
  }

  private save(): void {
    try {
      localStorage.setItem(this.key, JSON.stringify(this.drafts));
    } catch {
      /* storage indisponível — ignora */
    }
  }
}
