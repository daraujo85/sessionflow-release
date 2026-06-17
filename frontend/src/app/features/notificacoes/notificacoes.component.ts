import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Location } from '@angular/common';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { Router } from '@angular/router';
import { ApiService } from '../../core/api.service';
import { SseService } from '../../core/sse.service';
import { EventItem, EventKind, Notification } from '../../core/models';

/** Atributos comuns dos SVGs inline (stroke-based, 18x18, viewBox 24). */
const SVG_ATTRS =
  'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"';

/** Paths SVG por glifo, replicados do mockup (iconHTML). */
const SVG_PATHS: Record<string, string> = {
  // sino (não usado por kind, mas mantido para referência do mock)
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
  // alerta -> attention
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 8v5"/><path d="M12 17h.01"/>',
  // check -> success
  check: '<circle cx="12" cy="12" r="9"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
  // info -> info
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 8h.01"/><path d="M11 12h1v4h1"/>',
  // unplug (tomada desconectada) -> warning
  unplug:
    '<path d="m19 5 3-3"/><path d="m2 22 3-3"/><path d="M6.3 14.3 9 17l-2 2-4-4 2-2 2.7 2.7"/><path d="M14.3 6.3 17 9l2-2-4-4-2 2 2.7 2.7"/>',
};

const svg = (name: string): string =>
  `<svg width="18" height="18" viewBox="0 0 24 24" ${SVG_ATTRS}>${SVG_PATHS[name] ?? ''}</svg>`;

/**
 * Config visual por categoria de notificação, replicando o objeto `nc` do
 * mockup: cor do glifo, tint de fundo do ícone, cor da borda do card e o
 * SVG inline correspondente.
 */
const KIND_META: Record<
  EventKind,
  { color: string; bg: string; border: string; icon: string }
> = {
  attention: {
    color: '#F87171',
    bg: 'rgba(248,113,113,.14)',
    border: '#3a2326',
    icon: svg('alert'),
  },
  info: {
    color: '#4796E3',
    bg: 'rgba(71,150,227,.14)',
    border: '#283230',
    icon: svg('info'),
  },
  warning: {
    color: '#FBBF24',
    bg: 'rgba(251,191,36,.14)',
    border: '#283230',
    icon: svg('unplug'),
  },
  success: {
    color: '#34D399',
    bg: 'rgba(52,211,153,.14)',
    border: '#283230',
    icon: svg('check'),
  },
};

@Component({
  selector: 'sf-notificacoes',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="sf-overlay notif">
      <header class="notif__header">
        <button
          type="button"
          class="notif__back"
          aria-label="Voltar"
          (click)="goBack()"
        >
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="#C9CDD6"
            stroke-width="2.2"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path d="M15 18l-6-6 6-6" />
          </svg>
        </button>
        <h1 class="notif__title">Notificações</h1>
      </header>

      <div class="notif__scroll">
        @if (items().length === 0) {
          <p class="notif__empty">Nenhuma notificação por aqui.</p>
        } @else {
          <ul class="notif__list">
            @for (n of items(); track n.id) {
              <li>
                <button
                  type="button"
                  class="card"
                  [class.card--clickable]="!!n.session_id"
                  [style.borderColor]="meta(n.kind).border"
                  (click)="open(n)"
                >
                  <span
                    class="card__icon"
                    [style.color]="meta(n.kind).color"
                    [style.background]="meta(n.kind).bg"
                    aria-hidden="true"
                    [innerHTML]="iconHtml(n.kind)"
                  ></span>
                  <span class="card__body">
                    <span class="card__head">
                      <span class="card__title">{{ n.title }}</span>
                      <time class="card__time">{{ relativeTime(n.at) }}</time>
                    </span>
                    @if (n.desc) {
                      <span class="card__desc">{{ n.desc }}</span>
                    }
                  </span>
                </button>
              </li>
            }
          </ul>
        }
      </div>
    </section>
  `,
  styles: [
    `
      /* Overlay full-screen, replicando .sf-screen do mockup. */
      .notif {
        position: absolute;
        inset: 0;
        display: flex;
        flex-direction: column;
        background: #0e1113;
        color: #f4f5f7;
      }

      /* Header: padding 6px 18px 16px, border-bottom #20262A. */
      .notif__header {
        flex: none;
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 6px 18px 16px;
        border-bottom: 1px solid #20262a;
      }

      /* Botão voltar: 38px, radius 11px, bg #181C1B, border #283230. */
      .notif__back {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 38px;
        height: 38px;
        flex: none;
        padding: 0;
        border: 1px solid #283230;
        border-radius: 11px;
        background: #181c1b;
        cursor: pointer;
      }

      .notif__title {
        margin: 0;
        font-size: 19px;
        font-weight: 700;
        color: #f4f5f7;
      }

      /* Lista rolável: padding 16px 20px 30px. */
      .notif__scroll {
        flex: 1;
        overflow-y: auto;
        padding: 16px 20px 30px;
      }

      .notif__empty {
        margin: 0;
        padding: 28px 4px;
        color: #6b7180;
        font-size: 13px;
        text-align: center;
      }

      .notif__list {
        list-style: none;
        margin: 0;
        padding: 0;
        display: flex;
        flex-direction: column;
        gap: 12px;
      }

      /* Card: bg #181C1B, radius 16px, padding 15px, flex gap 13px. */
      .card {
        width: 100%;
        display: flex;
        align-items: flex-start;
        gap: 13px;
        padding: 15px;
        background: #181c1b;
        border: 1px solid #283230;
        border-radius: 16px;
        text-align: left;
        cursor: default;
        font: inherit;
        color: inherit;
      }
      .card--clickable {
        cursor: pointer;
      }

      /* Ícone: 36px, radius 11px, cor + bg-tint por kind. */
      .card__icon {
        flex: none;
        display: flex;
        align-items: center;
        justify-content: center;
        width: 36px;
        height: 36px;
        border-radius: 11px;
      }

      .card__body {
        flex: 1;
        min-width: 0;
        display: flex;
        flex-direction: column;
      }

      .card__head {
        display: flex;
        align-items: baseline;
        gap: 8px;
      }

      .card__title {
        font-size: 15px;
        font-weight: 600;
        color: #f4f5f7;
      }

      /* Tempo: 11.5px #6B7180, empurrado para a direita. */
      .card__time {
        flex: none;
        margin-left: auto;
        font-size: 11.5px;
        color: #6b7180;
        white-space: nowrap;
      }

      /* Corpo: 13px #8A90A0, line-height 1.45, margin-top 3px. */
      .card__desc {
        margin-top: 3px;
        font-size: 13px;
        line-height: 1.45;
        color: #8a90a0;
      }
    `,
  ],
})
export class NotificacoesComponent {
  private readonly api = inject(ApiService);
  private readonly sse = inject(SseService);
  private readonly router = inject(Router);
  private readonly location = inject(Location);
  private readonly sanitizer = inject(DomSanitizer);
  private readonly destroyRef = inject(DestroyRef);

  /** Cache de SVGs sanitizados por kind (evita re-sanitizar a cada CD). */
  private readonly iconCache = new Map<EventKind, SafeHtml>();

  /** Notificações carregadas via HTTP (getNotifications). */
  private readonly fetched = signal<Notification[]>([]);

  /**
   * Lista final: merge das notificações HTTP com o stream ao vivo do SSE,
   * deduplicado por `id` (a entrada mais recente do mesmo id vence) e
   * ordenado do mais novo para o mais antigo por `at`.
   */
  readonly items = computed<EventItem[]>(() => {
    const byId = new Map<string, EventItem>();
    for (const n of this.fetched()) {
      byId.set(n.id, n);
    }
    // SSE depois => sobrescreve/atualiza a versão HTTP do mesmo id.
    for (const n of this.sse.notifications()) {
      byId.set(n.id, n);
    }
    return [...byId.values()].sort(
      (a, b) => new Date(b.at).getTime() - new Date(a.at).getTime(),
    );
  });

  constructor() {
    // Garante que o stream esteja aberto para receber notificações ao vivo.
    this.sse.connect();
    this.destroyRef.onDestroy(() => this.sse.disconnect());

    this.api
      .getNotifications()
      .pipe(takeUntilDestroyed())
      .subscribe({
        next: (list) => this.fetched.set(this.normalize(list)),
        error: () => this.fetched.set([]),
      });
  }

  meta(kind: EventKind): { color: string; bg: string; border: string; icon: string } {
    return KIND_META[kind] ?? KIND_META.info;
  }

  /** SVG inline (sanitizado) do ícone para o `kind` informado. */
  iconHtml(kind: EventKind): SafeHtml {
    const cached = this.iconCache.get(kind);
    if (cached) {
      return cached;
    }
    const html = this.sanitizer.bypassSecurityTrustHtml(this.meta(kind).icon);
    this.iconCache.set(kind, html);
    return html;
  }

  goBack(): void {
    this.location.back();
  }

  open(n: EventItem): void {
    if (n.session_id) {
      void this.router.navigate(['/sessao', n.session_id]);
    }
  }

  /**
   * Normaliza a resposta do endpoint, que pode vir como `EventItem[]`
   * ou como um envelope `{ items: EventItem[] }`.
   */
  private normalize(res: unknown): Notification[] {
    if (Array.isArray(res)) {
      return res as Notification[];
    }
    if (res && typeof res === 'object' && Array.isArray((res as any).items)) {
      return (res as { items: Notification[] }).items;
    }
    return [];
  }

  /** Tempo relativo curto em pt-BR (ex.: "agora", "5 min", "2 h", "3 d"). */
  relativeTime(at: string): string {
    const then = new Date(at).getTime();
    if (Number.isNaN(then)) {
      return '';
    }
    const diff = Math.max(0, Date.now() - then);
    const min = Math.floor(diff / 60_000);
    if (min < 1) return 'agora';
    if (min < 60) return `${min} min`;
    const h = Math.floor(min / 60);
    if (h < 24) return `${h} h`;
    const d = Math.floor(h / 24);
    return `${d} d`;
  }
}
