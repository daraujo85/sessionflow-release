import { Injectable, inject, signal } from '@angular/core';
import { SwPush } from '@angular/service-worker';
import { firstValueFrom } from 'rxjs';
import { ApiService } from './api.service';

/** Estados possíveis da permissão de notificação do navegador. */
export type NotifyPermission = 'default' | 'granted' | 'denied' | 'unsupported';

/** Padrão de vibração (ms): buzz-pausa-buzz forte o bastante p/ sentir. Android
 * honra; iOS ignora. */
const VIBRATE_PATTERN: number[] = [300, 120, 300];

/**
 * Notificações do SISTEMA (Android/desktop) via Notifications API + Service
 * Worker. Funciona com o PWA aberto OU em segundo plano (processo vivo) — SEM
 * Firebase. O caso "navegador 100% encerrado" exige push/FCM (Fase 2).
 */
@Injectable({ providedIn: 'root' })
export class NotifyService {
  /** Permissão atual; reativo para a UI (Perfil) refletir o estado. */
  readonly permission = signal<NotifyPermission>(this.readPermission());

  /** True se o navegador suporta a Notifications API. */
  readonly supported = typeof Notification !== 'undefined';

  private readPermission(): NotifyPermission {
    if (typeof Notification === 'undefined') {
      return 'unsupported';
    }
    return Notification.permission as NotifyPermission;
  }

  private readonly swPush = inject(SwPush, { optional: true });
  private readonly api = inject(ApiService);

  /** Pede permissão ao usuário (precisa ser disparado por gesto/clique). */
  async requestPermission(): Promise<NotifyPermission> {
    if (typeof Notification === 'undefined') {
      this.permission.set('unsupported');
      return 'unsupported';
    }
    try {
      const result = (await Notification.requestPermission()) as NotifyPermission;
      this.permission.set(result);
      if (result === 'granted') {
        void this.enablePush(); // assina Web Push (app fechado) em background
      }
      return result;
    } catch {
      // Safari antigo usa callback; ignoramos e relemos o estado.
      const current = this.readPermission();
      this.permission.set(current);
      return current;
    }
  }

  /**
   * Assina o Web Push (VAPID) e registra a subscrição no servidor — habilita
   * notificação com o app FECHADO. Best-effort: sem SW/SwPush ou sem chave, é
   * no-op. Seguro chamar repetidamente.
   */
  async enablePush(): Promise<void> {
    const sw = this.swPush;
    if (!sw?.isEnabled || this.permission() !== 'granted') {
      return;
    }
    try {
      const { public_key } = await firstValueFrom(this.api.getVapidKey());
      if (!public_key) {
        return;
      }
      let sub = sw.subscription ? await firstValueFrom(sw.subscription) : null;
      if (!sub) {
        sub = await sw.requestSubscription({ serverPublicKey: public_key });
      }
      await firstValueFrom(this.api.subscribePush(sub.toJSON()));
    } catch {
      /* sem push (negado/sem SW/transitório) — ignora */
    }
  }

  /**
   * Dispara uma notificação do sistema. Prefere o Service Worker
   * (`showNotification`) — único caminho que funciona com a aba em segundo
   * plano —, caindo para `new Notification` quando não há SW registrado.
   * No-op silencioso sem permissão/suporte.
   */
  async notify(
    title: string,
    options: NotificationOptions & { url?: string } = {},
  ): Promise<void> {
    if (this.permission() !== 'granted') {
      return;
    }
    // Haptics: vibra o aparelho na hora (app em foreground). Android suporta;
    // iOS Safari/PWA ignora silenciosamente. O mesmo padrão também vai nas
    // opções da notificação (abaixo) p/ vibrar quando disparada pelo SW.
    try {
      navigator.vibrate?.(VIBRATE_PATTERN);
    } catch {
      /* sem suporte a vibração — silencioso */
    }
    const { url, ...rest } = options;
    // `onActionClick` é entendido pelo Service Worker do Angular (ngsw): ao
    // clicar, foca/abre o app e navega para a URL da sessão.
    const data: Record<string, unknown> = { url, ...(rest.data ?? {}) };
    if (url) {
      data['onActionClick'] = {
        default: { operation: 'navigateLastFocusedOrOpen', url },
      };
    }
    const opts: NotificationOptions & { vibrate?: number[] } = {
      icon: 'icons/icon-192x192-v2.png',
      // Badge = ícone monocromático da barra de status (Android usa só o alfa);
      // o ícone cheio aqui virava quadrado branco.
      badge: 'icons/badge-96.png',
      // Vibração ao exibir (Android; iOS ignora).
      vibrate: VIBRATE_PATTERN,
      ...rest,
      data,
    };
    // Caminho confiável: Service Worker. `ready` espera o SW ATIVO (em vez de
    // getRegistration(), que pode voltar vazio antes da ativação). No Android,
    // notificações SÓ funcionam via SW — `new Notification()` lança erro lá.
    try {
      if ('serviceWorker' in navigator) {
        const reg = await navigator.serviceWorker.ready;
        await reg.showNotification(title, opts);
        return;
      }
    } catch {
      /* sem SW ativo — tenta o construtor direto (desktop) */
    }
    // Fallback desktop (sem SW): construtor direto.
    try {
      // eslint-disable-next-line no-new
      new Notification(title, opts);
    } catch {
      /* ambiente sem suporte — ignora */
    }
  }
}
