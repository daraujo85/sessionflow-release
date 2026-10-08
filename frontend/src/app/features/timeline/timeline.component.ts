import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Router } from '@angular/router';
import { ApiService } from '../../core/api.service';
import { SseService } from '../../core/sse.service';
import { EventItem, EventKind } from '../../core/models';

/** Cor da bolinha da timeline por categoria de evento (mockup "TIMELINE"). */
const KIND_COLOR: Record<EventKind, string> = {
  attention: '#F87171',
  info: '#4796E3',
  warning: '#FBBF24',
  success: '#34D399',
};

/** Um dia agrupado, com rótulo ("Hoje"/"Ontem"/data) e seus eventos. */
interface DayGroup {
  /** Chave estável p/ track (YYYY-MM-DD em horário local). */
  key: string;
  /** Rótulo já formatado em pt-BR. */
  label: string;
  /** Eventos do dia, do mais novo para o mais antigo. */
  items: EventItem[];
}

@Component({
  selector: 'sf-timeline',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="sf-timeline">
      <header class="sf-head">
        <h1 class="sf-title">Timeline</h1>
      </header>

      @if (groups().length === 0) {
        <div class="sf-empty">
          <p class="sf-empty__title">Sem atividade</p>
          <p class="sf-empty__sub">Os eventos das suas sessões aparecem aqui.</p>
        </div>
      } @else {
        @for (group of groups(); track group.key) {
          <div class="sf-group">
            <h2 class="sf-group__label">{{ group.label }}</h2>
            <div class="sf-line">
              <span class="sf-line__rail" aria-hidden="true"></span>
              @for (ev of group.items; track ev.id) {
                <button
                  type="button"
                  class="sf-item"
                  [class.is-clickable]="!!ev.session_id"
                  [disabled]="!ev.session_id"
                  (click)="open(ev)"
                >
                  <span
                    class="sf-dot"
                    [style.background]="color(ev.kind)"
                    aria-hidden="true"
                  ></span>
                  <span class="sf-item__top">
                    <span class="sf-item__title">{{ ev.title }}</span>
                    <time class="sf-item__time">{{ hour(ev.at) }}</time>
                  </span>
                  @if (ev.desc) {
                    <span class="sf-item__desc">{{ ev.desc }}</span>
                  }
                </button>
              }
            </div>
          </div>
        }
      }
    </section>
  `,
  styles: [
    `
      :host {
        display: block;
        background: #0e1113;
        min-height: 100%;
        color: #f4f5f7;
      }
      .sf-timeline {
        padding: 6px 20px 120px;
        max-width: 720px;
        margin: 0 auto;
      }
      .sf-head {
        padding: 10px 0 18px;
      }
      .sf-title {
        margin: 0;
        font-size: 28px;
        font-weight: 700;
        color: #f4f5f7;
        letter-spacing: -0.6px;
      }

      .sf-empty {
        text-align: center;
        padding: 56px 16px;
        color: #8a90a0;
      }
      .sf-empty__title {
        margin: 0 0 6px;
        font-size: 15px;
        font-weight: 600;
        color: #f4f5f7;
      }
      .sf-empty__sub {
        margin: 0;
        font-size: 13px;
      }

      .sf-group {
        margin-bottom: 22px;
      }
      .sf-group__label {
        margin: 0 0 12px;
        font-size: 13px;
        font-weight: 700;
        color: #6b7180;
        letter-spacing: 0.5px;
        text-transform: uppercase;
      }

      /* Container da linha do tempo: trilho vertical fica em left:6px. */
      .sf-line {
        position: relative;
        padding-left: 26px;
      }
      .sf-line__rail {
        position: absolute;
        left: 6px;
        top: 6px;
        bottom: 6px;
        width: 2px;
        background: #283230;
      }

      .sf-item {
        position: relative;
        display: block;
        width: 100%;
        text-align: left;
        appearance: none;
        border: none;
        background: transparent;
        color: inherit;
        font: inherit;
        padding: 0 0 18px;
        cursor: default;
      }
      .sf-item.is-clickable {
        cursor: pointer;
      }
      .sf-item:disabled {
        cursor: default;
      }
      .sf-item.is-clickable:hover .sf-item__title {
        color: #00e4b4;
      }

      .sf-dot {
        position: absolute;
        left: -26px;
        top: 3px;
        width: 14px;
        height: 14px;
        border-radius: 50%;
        border: 3px solid #0e1113;
        box-sizing: border-box;
      }

      .sf-item__top {
        display: flex;
        align-items: baseline;
        gap: 8px;
      }
      .sf-item__title {
        font-size: 14.5px;
        font-weight: 600;
        color: #f4f5f7;
        transition: color 0.15s;
        min-width: 0;
      }
      .sf-item__time {
        font-size: 12px;
        color: #6b7180;
        white-space: nowrap;
        margin-left: auto;
        flex: 0 0 auto;
        font-variant-numeric: tabular-nums;
      }
      .sf-item__desc {
        display: block;
        font-size: 13px;
        color: #8a90a0;
        margin-top: 2px;
      }
    `,
  ],
})
export class TimelineComponent {
  private readonly api = inject(ApiService);
  private readonly sse = inject(SseService);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);

  /** Eventos carregados via HTTP (getHistory). */
  private readonly fetched = signal<EventItem[]>([]);

  /**
   * Lista final deduplicada por `id`: histórico HTTP + stream ao vivo do SSE.
   * A entrada do SSE (mais recente) sobrescreve a versão HTTP do mesmo id.
   */
  private readonly merged = computed<EventItem[]>(() => {
    const byId = new Map<string, EventItem>();
    for (const e of this.fetched()) {
      byId.set(e.id, e);
    }
    for (const e of this.sse.events()) {
      byId.set(e.id, e);
    }
    return [...byId.values()].sort(
      (a, b) => this.time(b.at) - this.time(a.at),
    );
  });

  /** Eventos agrupados por dia (do dia mais recente para o mais antigo). */
  protected readonly groups = computed<DayGroup[]>(() => {
    const order: string[] = [];
    const map = new Map<string, DayGroup>();

    for (const ev of this.merged()) {
      const d = new Date(ev.at);
      const key = Number.isNaN(d.getTime()) ? 'sem-data' : this.dayKey(d);
      let group = map.get(key);
      if (!group) {
        group = { key, label: this.dayLabel(d, key), items: [] };
        map.set(key, group);
        order.push(key);
      }
      group.items.push(ev);
    }

    return order.map((k) => map.get(k)!);
  });

  constructor() {
    // Abre o stream para receber eventos ao vivo; fecha ao destruir.
    this.sse.connect();
    this.destroyRef.onDestroy(() => this.sse.disconnect());

    this.api
      .getHistory()
      .pipe(takeUntilDestroyed())
      .subscribe({
        next: (list) => this.fetched.set(list ?? []),
        error: () => this.fetched.set([]),
      });
  }

  protected color(kind: EventKind): string {
    return KIND_COLOR[kind] ?? KIND_COLOR.info;
  }

  /** Hora local HH:MM do evento. */
  protected hour(at: string): string {
    const d = new Date(at);
    if (Number.isNaN(d.getTime())) {
      return '';
    }
    return d.toLocaleTimeString('pt-BR', {
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  protected open(ev: EventItem): void {
    if (ev.session_id) {
      void this.router.navigate(['/sessao', ev.session_id]);
    }
  }

  private time(at: string): number {
    const t = new Date(at).getTime();
    return Number.isNaN(t) ? 0 : t;
  }

  /** Chave YYYY-MM-DD em horário LOCAL (agrupa por dia do usuário). */
  private dayKey(d: Date): string {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }

  /** Rótulo do grupo: "Hoje", "Ontem" ou data por extenso em pt-BR. */
  private dayLabel(d: Date, key: string): string {
    if (key === 'sem-data') {
      return 'Sem data';
    }
    const now = new Date();
    if (key === this.dayKey(now)) {
      return 'Hoje';
    }
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    if (key === this.dayKey(yesterday)) {
      return 'Ontem';
    }
    return d.toLocaleDateString('pt-BR', {
      day: '2-digit',
      month: 'long',
      year:
        d.getFullYear() === now.getFullYear() ? undefined : 'numeric',
    });
  }
}
