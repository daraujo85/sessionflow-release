import { Component, signal, computed, inject } from '@angular/core';
import { RouterOutlet, Router, RouterLink, RouterLinkActive, NavigationEnd } from '@angular/router';
import { trigger, transition, style, animate } from '@angular/animations';
import { toSignal } from '@angular/core/rxjs-interop';
import { SwUpdate } from '@angular/service-worker';
import { filter, map, startWith } from 'rxjs/operators';
import { AuthService } from './core/auth.service';
import { SseService } from './core/sse.service';
import { NotifyService } from './core/notify.service';
import { JarvisAudioService } from './core/jarvis-audio.service';
import { EventCuesService } from './core/event-cues.service';

/** A bottom-nav entry: route + label + SVG path (icons from the mockup). */
export interface NavItem {
  /** Route path (without leading slash). */
  path: string;
  /** PT-BR label. */
  label: string;
  /** SVG `d` attribute for the 24x24 stroked icon. */
  d: string;
}

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './app.html',
  styleUrl: './app.css',
  animations: [
    // Subtle iOS-like ENTER for the routed page: fade + small rise.
    // Keyed by the route segment (see routeKey) so it re-fires on navigation.
    // Leave is instant (void) to avoid overlap jank between pages.
    trigger('pageFade', [
      transition('* => *', [
        style({ opacity: 0, transform: 'translateY(8px)' }),
        animate(
          '200ms cubic-bezier(0.22, 1, 0.36, 1)',
          style({ opacity: 1, transform: 'translateY(0)' }),
        ),
      ]),
    ]),
  ],
})
export class App {
  protected readonly title = signal('SessionFlow');
  /** True enquanto uma nova versão está sendo ativada (mostra o aviso). */
  protected readonly updating = signal(false);
  private readonly router = inject(Router);
  private readonly swUpdate = inject(SwUpdate, { optional: true });
  private readonly auth = inject(AuthService);
  private readonly sse = inject(SseService);
  private readonly notify = inject(NotifyService);
  private readonly jarvisAudio = inject(JarvisAudioService);
  private readonly eventCues = inject(EventCuesService);

  constructor() {
    // JARVIS: liga o pipeline de áudio (efeito SSE + desbloqueio de autoplay).
    this.jarvisAudio.init();
    // Avisos de evento (chime/voz discretos) — separado do JARVIS.
    this.eventCues.init();
    // SSE app-wide quando autenticado: garante que as notificações (sino +
    // sistema) cheguem em QUALQUER tela, não só na de notificações.
    if (this.auth.isAuthenticated()) {
      this.sse.connect();
      // Pede a permissão de notificação proativamente (1x) quando ainda não
      // decidida — senão o usuário logado nunca é perguntado.
      if (this.notify.permission() === 'default') {
        void this.notify.requestPermission();
      } else if (this.notify.permission() === 'granted') {
        // Já concedida → garante a subscrição Web Push (app fechado) registrada.
        void this.notify.enablePush();
      }
    }

    this.setupServiceWorker();
  }

  /**
   * Mantém o app sempre na última versão (evita Service Worker defasado, que
   * deixaria o usuário num build antigo — favicon/recursos/comportamento
   * desatualizados). Estratégia:
   *   - VERSION_READY → ativa e recarrega (1x);
   *   - checa por update no boot, a cada 5 min e SEMPRE que o app volta ao
   *     foco (reabrir a aba/PWA é o gatilho mais comum pós-deploy);
   *   - SW irrecuperável → recarrega do servidor.
   */
  private setupServiceWorker(): void {
    const sw = this.swUpdate;
    if (!sw?.isEnabled) {
      return;
    }
    sw.versionUpdates
      .pipe(filter((e) => e.type === 'VERSION_READY'))
      .subscribe(() => {
        // Aviso discreto antes de recarregar, pra ficar transparente que o
        // app se auto-atualizou (em vez de um reload "do nada").
        this.updating.set(true);
        sw.activateUpdate().then(() => {
          setTimeout(() => document.location.reload(), 1300);
        });
      });
    sw.unrecoverable.subscribe(() => document.location.reload());

    const check = () => {
      sw.checkForUpdate().catch(() => {
        /* offline/transitório — tenta de novo no próximo gatilho */
      });
    };
    check();
    setInterval(check, 5 * 60 * 1000);
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
          check();
        }
      });
    }
  }

  /** The five bottom-nav tabs (icon paths from `navDefs` in the mockup). */
  protected readonly navItems: readonly NavItem[] = [
    { path: 'inicio', label: 'Início', d: 'M3 11.5 12 4l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z' },
    { path: 'sessoes', label: 'Sessões', d: 'M4 5h16v14H4zM8 10l3 2-3 2M13.5 14h3' },
    { path: 'timeline', label: 'Timeline', d: 'M8 6h12M8 12h12M8 18h12M3.5 6h.01M3.5 12h.01M3.5 18h.01' },
    { path: 'perfil', label: 'Perfil', d: 'M16 8a4 4 0 1 1-8 0 4 4 0 0 1 8 0zM4 21v-1a6 6 0 0 1 12 0v1' },
  ];

  /** Current top-level route segment, tracked from router events. */
  private readonly currentUrl = toSignal(
    this.router.events.pipe(
      filter((e): e is NavigationEnd => e instanceof NavigationEnd),
      map((e) => e.urlAfterRedirects),
      startWith(this.router.url),
    ),
    { initialValue: this.router.url },
  );

  /** First path segment of the active URL (e.g. "inicio", "criar", "sessao"). */
  private readonly activeSegment = computed(
    () => (this.currentUrl() || '/').split('?')[0].split('/').filter(Boolean)[0] ?? 'inicio',
  );

  /**
   * Animation key for the page-enter transition. Uses the full URL path (sans
   * query) so the `@pageFade` trigger re-fires on every navigation — including
   * between sibling routes like `/sessao/a` → `/sessao/b`.
   */
  protected readonly routeKey = computed(
    () => (this.currentUrl() || '/').split('?')[0],
  );

  /** Telas sem chrome (full-screen): overlays e a tela de login. */
  protected readonly isOverlay = computed(() =>
    ['criar', 'sessao', 'notificacoes', 'login', 's'].includes(this.activeSegment()),
  );

  /** Login é full-bleed (sem a coluna de 480px do shell). */
  protected readonly isLogin = computed(() => this.activeSegment() === 'login');

  /** The FAB (+) is shown only on the Início and Sessões tabs. */
  protected readonly showFab = computed(() =>
    !this.isOverlay() && ['inicio', 'sessoes'].includes(this.activeSegment()),
  );
}
