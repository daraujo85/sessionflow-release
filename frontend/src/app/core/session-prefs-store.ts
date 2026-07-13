import { Injectable } from '@angular/core';

/**
 * Preferências de UI escolhidas POR sessão na tela de detalhe do terminal.
 * Sobrevivem à navegação (o detalhe é reusado/destruído ao trocar de sessão) e
 * ao reload (persistido em localStorage). Espelha o {@link DraftStore}, mas
 * guarda um objeto por sessão em vez de uma string.
 *
 * Campos ausentes = "usa o default" (o componente decide o fallback). Só
 * gravamos o que o usuário mexeu, pra não fixar defaults antigos.
 */
export interface SessionPrefs {
  /** "Modo ao vivo": encaminha a digitação pro pane (autocomplete do CLI). */
  liveMode?: boolean;
  /** Terminal congelado no histórico rolável (em vez do espelho ao vivo). */
  historyMode?: boolean;
  /** Teclado de navegação (setas/Enter/Esc) aberto. */
  keypadOpen?: boolean;
  /** Tamanho da fonte do terminal (px) — por sessão (o global é só o default). */
  termFont?: number;
}

@Injectable({ providedIn: 'root' })
export class SessionPrefsStore {
  private readonly key = 'sf.session.prefs';
  private prefs: Record<string, SessionPrefs> = this.load();

  /** Preferências salvas da sessão (objeto vazio se não houver). */
  get(sessionId: string): SessionPrefs {
    return (sessionId && this.prefs[sessionId]) || {};
  }

  /** Mescla ``patch`` nas preferências da sessão e persiste. */
  patch(sessionId: string, patch: SessionPrefs): void {
    if (!sessionId) {
      return;
    }
    this.prefs[sessionId] = { ...(this.prefs[sessionId] || {}), ...patch };
    this.save();
  }

  private load(): Record<string, SessionPrefs> {
    try {
      return JSON.parse(localStorage.getItem(this.key) || '{}');
    } catch {
      return {};
    }
  }

  private save(): void {
    try {
      localStorage.setItem(this.key, JSON.stringify(this.prefs));
    } catch {
      /* storage indisponível — ignora */
    }
  }
}
