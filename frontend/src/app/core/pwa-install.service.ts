import { Injectable, signal } from '@angular/core';

/**
 * The `beforeinstallprompt` event (Chrome/Edge/Android). Not in lib.dom yet,
 * so we model the bits we use.
 */
interface BeforeInstallPromptEvent extends Event {
  prompt(): Promise<void>;
  readonly userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>;
}

/**
 * Oferece "instalar como app" (PWA). Em navegadores Chromium captura o evento
 * `beforeinstallprompt` e expõe um prompt nativo; no iOS/Safari (que não tem
 * esse evento) sinaliza para mostrar as instruções de "Adicionar à Tela de
 * Início". `installed`/standalone evitam oferecer quando já está instalado.
 */
@Injectable({ providedIn: 'root' })
export class PwaInstallService {
  private deferred: BeforeInstallPromptEvent | null = null;

  /** True quando o navegador disponibilizou o prompt nativo de instalação. */
  readonly canPrompt = signal(false);
  /** True após o app ter sido instalado nesta sessão. */
  readonly installed = signal(false);

  /** iOS Safari não dispara beforeinstallprompt → instalação é manual. */
  readonly isIos = this.detectIos();
  /** Já rodando como app instalado (display-mode: standalone). */
  readonly isStandalone = this.detectStandalone();

  constructor() {
    if (typeof window === 'undefined') {
      return;
    }
    window.addEventListener('beforeinstallprompt', (e: Event) => {
      // Impede o mini-infobar padrão; guardamos para disparar sob demanda.
      e.preventDefault();
      this.deferred = e as BeforeInstallPromptEvent;
      this.canPrompt.set(true);
    });
    window.addEventListener('appinstalled', () => {
      this.installed.set(true);
      this.canPrompt.set(false);
      this.deferred = null;
    });
  }

  /**
   * Deve oferecer a opção de instalar? Sim quando há prompt nativo disponível,
   * ou no iOS (instruções manuais) — e nunca quando já está instalado.
   */
  shouldOffer(): boolean {
    if (this.isStandalone || this.installed()) {
      return false;
    }
    return this.canPrompt() || this.isIos;
  }

  /** Dispara o prompt nativo. Retorna true se o usuário aceitou instalar. */
  async promptInstall(): Promise<boolean> {
    if (!this.deferred) {
      return false;
    }
    await this.deferred.prompt();
    const choice = await this.deferred.userChoice;
    this.deferred = null;
    this.canPrompt.set(false);
    return choice.outcome === 'accepted';
  }

  private detectIos(): boolean {
    if (typeof navigator === 'undefined') {
      return false;
    }
    const ua = navigator.userAgent || '';
    const iOSDevice = /iphone|ipad|ipod/i.test(ua);
    // iPadOS 13+ se apresenta como Mac com toque — detecta pelo touch.
    const iPadOS = /macintosh/i.test(ua) && (navigator.maxTouchPoints ?? 0) > 1;
    return iOSDevice || iPadOS;
  }

  private detectStandalone(): boolean {
    if (typeof window === 'undefined') {
      return false;
    }
    const mql = window.matchMedia?.('(display-mode: standalone)').matches ?? false;
    // Safari iOS usa a flag proprietária navigator.standalone.
    const iosStandalone = (navigator as unknown as { standalone?: boolean }).standalone === true;
    return mql || iosStandalone;
  }
}
