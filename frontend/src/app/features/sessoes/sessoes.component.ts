import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Router } from '@angular/router';
import { ApiService } from '../../core/api.service';
import { Session, SessionStatus } from '../../core/models';
import { SseService } from '../../core/sse.service';
import { STATUS_META, agentMeta, isWorkerSession } from '../../shared/status-color';

/** One selectable filter chip. `status` undefined means "Todas". */
interface FilterChip {
  readonly key: string;
  readonly label: string;
  readonly status?: SessionStatus;
}

/** Filter chips shown horizontally above the list (mockup "SESSÕES"). */
const FILTERS: readonly FilterChip[] = [
  { key: 'all', label: 'Todas' },
  { key: 'running', label: 'Ativas', status: 'running' },
  { key: 'waiting_input', label: 'Aguardando', status: 'waiting_input' },
  { key: 'completed', label: 'Concluídas', status: 'completed' },
  { key: 'detached', label: 'Detached', status: 'detached' },
];

@Component({
  selector: 'sf-sessoes',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="sf-sessoes">
      <header class="sf-head">
        <h1 class="sf-title">Sessões</h1>
      </header>

      <nav class="sf-chips" role="tablist" aria-label="Filtrar sessões">
        @for (chip of filters; track chip.key) {
          <button
            type="button"
            role="tab"
            class="sf-chip"
            [class.is-active]="activeKey() === chip.key"
            [attr.aria-selected]="activeKey() === chip.key"
            (click)="selectFilter(chip)"
          >
            {{ chip.label }}
          </button>
        }
      </nav>

      @if (loading()) {
        <p class="sf-msg">Carregando…</p>
      } @else if (error()) {
        <p class="sf-msg sf-msg--error">Não foi possível carregar as sessões.</p>
      } @else if (visibleSessions().length === 0) {
        <div class="sf-empty">
          <p class="sf-empty__title">Nenhuma sessão</p>
          <p class="sf-empty__sub">Nada por aqui neste filtro.</p>
        </div>
      } @else {
        <ul class="sf-list">
          @for (s of visibleSessions(); track s.id) {
            <li>
              <button
                type="button"
                class="sf-card"
                (click)="open(s)"
                [attr.aria-label]="'Abrir sessão ' + displayName(s)"
              >
                <span class="sf-row">
                  <span
                    class="sf-avatar"
                    [style.color]="agent(s).color"
                    [style.background]="tint(agent(s).color, 0.16)"
                    >{{ agent(s).short }}</span
                  >

                  <span class="sf-info">
                    <span class="sf-name-row">
                      <span class="sf-name">{{ displayName(s) }}</span>
                      @if (isWorker(s)) {
                        <span class="sf-worker-chip" title="Worker / sub-agente">
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                               stroke="currentColor" stroke-width="2" stroke-linecap="round"
                               stroke-linejoin="round" aria-hidden="true">
                            <path d="M12 8V4H8" />
                            <rect width="16" height="12" x="4" y="8" rx="2" />
                            <path d="M2 14h2M20 14h2M15 13v2M9 13v2" />
                          </svg>
                          worker
                        </span>
                      }
                    </span>
                    <span class="mono sf-dir">{{ s.work_dir || '—' }}</span>
                  </span>

                  <svg
                    class="sf-chevron"
                    width="20"
                    height="20"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="#5A6072"
                    stroke-width="2.2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    aria-hidden="true"
                  >
                    <path d="M9 6l6 6-6 6" />
                  </svg>
                </span>

                <span class="sf-footer">
                  <span
                    class="sf-pill"
                    [style.color]="meta(s).color"
                    [style.background]="tint(meta(s).color, 0.13)"
                  >
                    <span class="sf-dot" [style.background]="meta(s).dot"></span>
                    {{ meta(s).label }}
                  </span>
                  @if (timeAgo(s)) {
                    <span class="sf-time">{{ timeAgo(s) }}</span>
                  }
                </span>
              </button>
            </li>
          }
        </ul>
      }
    </section>
  `,
  styles: [
    `
      :host {
        display: block;
        background: #0e1113;
        min-height: 100%;
        color: #e7eae9;
      }
      .sf-sessoes {
        padding: 6px 20px 120px;
        max-width: 720px;
        margin: 0 auto;
      }
      .sf-head {
        padding: 10px 0 16px;
      }
      .sf-title {
        margin: 0;
        font-size: 28px;
        font-weight: 700;
        color: #f4f5f7;
        letter-spacing: -0.6px;
      }

      .sf-chips {
        display: flex;
        gap: 8px;
        overflow-x: auto;
        margin: 0 -20px 18px;
        padding: 0 20px;
        scrollbar-width: none;
      }
      .sf-chips::-webkit-scrollbar {
        display: none;
      }
      .sf-chip {
        flex: 0 0 auto;
        appearance: none;
        border: 1px solid #283230;
        background: #181c1b;
        color: #c9cdd6;
        font: inherit;
        font-size: 13.5px;
        font-weight: 600;
        padding: 8px 15px;
        border-radius: 11px;
        cursor: pointer;
        transition: background 0.15s, color 0.15s, border-color 0.15s;
      }
      .sf-chip.is-active {
        background: #00e4b4;
        border-color: #00e4b4;
        color: #04140f;
      }

      .sf-msg {
        color: #9aa0ae;
        font-size: 14px;
        padding: 24px 4px;
      }
      .sf-msg--error {
        color: #f87171;
      }

      .sf-empty {
        text-align: center;
        padding: 56px 16px;
        color: #9aa0ae;
      }
      .sf-empty__title {
        margin: 0 0 6px;
        font-size: 15px;
        font-weight: 600;
        color: #e7eae9;
      }
      .sf-empty__sub {
        margin: 0;
        font-size: 13px;
      }

      .sf-list {
        list-style: none;
        margin: 0;
        padding: 0;
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      /* Telas largas: cards lado a lado em grid (não esticam 1 por linha). */
      @media (min-width: 768px) {
        .sf-list {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
        }
      }
      /* Desktop: solta o limite de 720px p/ o grid usar a largura toda. */
      @media (min-width: 1024px) {
        .sf-sessoes {
          max-width: none;
        }
      }
      .sf-card {
        display: block;
        width: 100%;
        text-align: left;
        appearance: none;
        border: 1px solid #283230;
        background: #181c1b;
        color: inherit;
        font: inherit;
        padding: 15px 16px;
        border-radius: 18px;
        cursor: pointer;
        transition: border-color 0.15s, background 0.15s;
      }
      .sf-card:active {
        background: #1d2221;
      }
      .sf-card:hover {
        border-color: #34403d;
      }

      .sf-row {
        display: flex;
        align-items: center;
        gap: 11px;
      }

      .sf-avatar {
        flex: 0 0 auto;
        width: 38px;
        height: 38px;
        border-radius: 11px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 12px;
        font-weight: 800;
        letter-spacing: 0.3px;
      }

      .sf-info {
        display: flex;
        flex-direction: column;
        min-width: 0;
        flex: 1;
      }
      .sf-name-row {
        display: flex;
        align-items: center;
        gap: 8px;
        min-width: 0;
      }
      .sf-name {
        font-size: 16px;
        font-weight: 600;
        color: #f4f5f7;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .sf-worker-chip {
        flex: none;
        display: inline-flex;
        align-items: center;
        gap: 4px;
        font-size: 10.5px;
        font-weight: 700;
        letter-spacing: 0.3px;
        color: #c084fc;
        background: rgba(192, 132, 252, 0.14);
        border: 1px solid rgba(192, 132, 252, 0.3);
        padding: 2px 7px;
        border-radius: 7px;
        white-space: nowrap;
      }
      .sf-dir {
        font-size: 12.5px;
        color: #7a8090;
        margin-top: 2px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .mono {
        font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, 'SF Mono',
          Menlo, Consolas, monospace;
      }

      .sf-chevron {
        flex: 0 0 auto;
      }

      .sf-footer {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-top: 13px;
        padding-top: 13px;
        border-top: 1px solid #23262f;
        gap: 10px;
        min-width: 0;
      }
      .sf-pill {
        display: inline-flex;
        align-items: center;
        gap: 7px;
        font-size: 12.5px;
        font-weight: 600;
        padding: 4px 10px;
        border-radius: 8px;
        white-space: nowrap;
      }
      .sf-dot {
        width: 7px;
        height: 7px;
        border-radius: 50%;
        flex: 0 0 auto;
      }
      .sf-time {
        font-size: 12.5px;
        color: #6b7180;
        white-space: nowrap;
        margin-left: auto;
      }
    `,
  ],
})
export class SessoesComponent {
  private readonly api = inject(ApiService);
  private readonly sse = inject(SseService);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly filters = FILTERS;

  protected readonly activeKey = signal<string>('all');
  protected readonly loading = signal<boolean>(true);
  protected readonly error = signal<boolean>(false);

  /** All sessions fetched from the API (live status applied via SSE). */
  private readonly sessions = signal<Session[]>([]);

  /** Sessions matching the active filter (client-side so SSE keeps it live). */
  protected readonly visibleSessions = computed<Session[]>(() => {
    const chip = this.filters.find((c) => c.key === this.activeKey());
    const wanted = chip?.status;
    const list = this.sessions();
    return wanted ? list.filter((s) => s.status === wanted) : list;
  });

  constructor() {
    this.load();

    // SSE drives live status updates. Each new event re-applies any status
    // changes carried on the session feed; we re-read the broker's events.
    this.sse.connect();
    this.destroyRef.onDestroy(() => this.sse.disconnect());

    // React to live frames: when an event references a known session, refresh
    // the list so statuses stay current without a manual reload.
    effect(() => {
      const last = this.sse.lastEvent();
      if (last && 'session_id' in last && last.session_id) {
        this.refreshStatuses();
      }
    });
  }

  protected selectFilter(chip: FilterChip): void {
    if (this.activeKey() === chip.key) {
      return;
    }
    this.activeKey.set(chip.key);
    // Refetch scoped to the chosen status (server-side filter); falls back to
    // client-side filtering on the cached list while the request is in flight.
    this.load(chip.status);
  }

  protected open(s: Session): void {
    void this.router.navigate(['/sessao', s.id]);
  }

  /** Worker/sub-agente pela convenção de nome (mostra chip ⑂ worker). */
  protected isWorker(s: Session): boolean {
    return isWorkerSession(s.tmux_name ?? s.display_name);
  }

  protected agent(s: Session) {
    return agentMeta(s.agent_type);
  }

  protected meta(s: Session) {
    return STATUS_META[s.status] ?? STATUS_META.detached;
  }

  /** Builds a translucent tint (`rgba`) from a `#rrggbb` color for backgrounds. */
  protected tint(hex: string, alpha: number): string {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
    if (!m) {
      return hex;
    }
    const n = parseInt(m[1], 16);
    const r = (n >> 16) & 255;
    const g = (n >> 8) & 255;
    const b = n & 255;
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  protected displayName(s: Session): string {
    return s.display_name || s.tmux_name || s.id;
  }

  protected timeAgo(s: Session): string {
    const raw =
      (s['updated_at'] as string | undefined) ??
      (s['created_at'] as string | undefined);
    if (!raw) {
      return '';
    }
    const then = Date.parse(raw);
    if (Number.isNaN(then)) {
      return '';
    }
    const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
    if (secs < 60) {
      return 'agora';
    }
    const mins = Math.floor(secs / 60);
    if (mins < 60) {
      return `há ${mins} min`;
    }
    const hours = Math.floor(mins / 60);
    if (hours < 24) {
      return `há ${hours} h`;
    }
    const days = Math.floor(hours / 24);
    return `há ${days} d`;
  }

  private load(status?: SessionStatus): void {
    this.loading.set(true);
    this.error.set(false);
    this.api
      .listSessions(status)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (list) => {
          this.sessions.set(list ?? []);
          this.loading.set(false);
        },
        error: () => {
          this.loading.set(false);
          this.error.set(true);
        },
      });
  }

  /** Quietly refetch the current filter to pick up live status changes. */
  private refreshStatuses(): void {
    const chip = this.filters.find((c) => c.key === this.activeKey());
    this.api
      .listSessions(chip?.status)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (list) => this.sessions.set(list ?? []),
        error: () => {
          /* keep last known state on transient errors */
        },
      });
  }
}
