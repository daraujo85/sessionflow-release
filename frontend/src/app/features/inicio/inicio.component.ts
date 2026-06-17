import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { Router } from '@angular/router';
import { ApiService } from '../../core/api.service';
import { SseService } from '../../core/sse.service';
import { Session, SessionStatus, Task } from '../../core/models';
import { STATUS_META, agentMeta, isWorkerSession } from '../../shared/status-color';

/** Session statuses considered "active" on the home screen. */
const ACTIVE_STATUSES: readonly SessionStatus[] = ['running', 'waiting_input'];

/**
 * Home screen ("Início"). Shows a greeting, the live count of active sessions,
 * the active-session cards and the most recent tasks. Subscribes to the SSE
 * stream so the lists update live as the backend emits events.
 */
@Component({
  selector: 'sf-inicio',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="sf-inicio">
      <!-- Header -->
      <header class="sf-header">
        <div class="sf-brand">
          <span class="sf-logo" aria-hidden="true">
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="#06231d"
              stroke-width="2.6"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path d="M17 7H9a3 3 0 0 0 0 6h6a3 3 0 0 1 0 6H6" />
            </svg>
          </span>
          <span class="sf-brand-name">SessionFlow</span>
        </div>

        <button
          type="button"
          class="sf-bell"
          aria-label="Notificações"
          (click)="openNotifications()"
        >
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="#C9CDD6"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
            <path d="M13.7 21a2 2 0 0 1-3.4 0" />
          </svg>
          @if (notifCount() > 0) {
            <span class="sf-bell-badge">{{ notifCount() }}</span>
          }
        </button>
      </header>

      <!-- Greeting -->
      <h1 class="sf-greeting">Boa noite, Diego 👋</h1>
      <p class="sf-active-count">{{ activeCountLabel() }}</p>

      <!-- Active sessions -->
      <div class="sf-section-head">
        <h2>Sessões ativas</h2>
        <button type="button" class="sf-link" (click)="goSessoes()">
          Ver todas
        </button>
      </div>

      @if (activeSessions().length > 0) {
        <div class="sf-cards">
          @for (s of activeSessions(); track s.id) {
            <button type="button" class="sf-card" (click)="openSession(s.id)">
              <span
                class="sf-dot"
                [class.sf-pulse]="s.status === 'running'"
                [class.sf-pulse-amber]="s.status === 'waiting_input'"
                [style.background]="statusMeta(s.status).dot"
              ></span>
              <span class="sf-card-body">
                <span class="sf-card-top">
                  <span class="sf-card-name">{{ displayName(s) }}</span>
                  <span
                    class="sf-agent"
                    [style.color]="agent(s).color"
                    [style.background]="agentBg(s)"
                    >{{ agent(s).short }}</span
                  >
                  @if (isWorker(s)) {
                    <span class="sf-worker-chip" title="Worker / sub-agente">
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
                           stroke="currentColor" stroke-width="2" stroke-linecap="round"
                           stroke-linejoin="round" aria-hidden="true">
                        <path d="M12 8V4H8" />
                        <rect width="16" height="12" x="4" y="8" rx="2" />
                        <path d="M2 14h2M20 14h2M15 13v2M9 13v2" />
                      </svg>
                    </span>
                  }
                </span>
                <span class="sf-card-status" [style.color]="statusMeta(s.status).color">{{
                  statusMeta(s.status).label
                }}</span>
                <span class="sf-card-sub mono">{{ subline(s) }}</span>
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
              >
                <path d="M9 6l6 6-6 6" />
              </svg>
            </button>
          }
        </div>
      } @else {
        <p class="sf-empty">Nenhuma sessão ativa no momento.</p>
      }

      <!-- Tarefas (marcos do agente, via .sessionflow/milestones.json) -->
      <div class="sf-section-head">
        <h2>Tarefas</h2>
        <button type="button" class="sf-link" (click)="goTimeline()">
          Ver todas
        </button>
      </div>

      @if (tasks().length > 0) {
        <div class="sf-task-filters">
          @for (f of taskFilters; track f.key) {
            <button
              type="button"
              class="sf-tfilter"
              [class.sel]="taskStatus() === f.key"
              (click)="taskStatus.set(f.key)"
            >
              {{ f.label }}
            </button>
          }
          @if (taskSessions().length > 1) {
            <select
              class="sf-tsessel"
              (change)="taskSession.set($any($event.target).value)"
            >
              <option value="">Todas as sessões</option>
              @for (s of taskSessions(); track s) {
                <option [value]="s" [selected]="taskSession() === s">{{ s }}</option>
              }
            </select>
          }
        </div>
      }

      @if (recentTasks().length > 0) {
        <div class="sf-task-list">
          @for (t of recentTasks(); track t.id; let first = $first) {
            <div
              class="sf-task"
              [class.sf-task-divider]="!first"
              [class.sf-task-clickable]="!!t.session_id"
              (click)="openTaskSession(t)"
            >
              <span
                class="sf-task-icon"
                [style.color]="taskMeta(t.state).color"
                [style.background]="taskMeta(t.state).bg"
              >
                @switch (t.state) {
                  @case ('done') {
                    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5" /></svg>
                  }
                  @case ('doing') {
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 6v6l4 2" /><circle cx="12" cy="12" r="9" /></svg>
                  }
                  @case ('blocked') {
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9" /><path d="M5.6 5.6 18.4 18.4" /></svg>
                  }
                  @case ('attention') {
                    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8v5" /><path d="M12 17h.01" /><circle cx="12" cy="12" r="9" /></svg>
                  }
                  @default {
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.5" /></svg>
                  }
                }
              </span>
              <span class="sf-task-body">
                <span class="sf-task-title">{{ t.title }}</span>
                <span class="sf-task-meta" [style.color]="taskMeta(t.state).color">{{
                  taskMeta(t.state).label
                }}</span>
              </span>
              <span class="sf-task-session mono">{{ sessionShort(t) }}</span>
            </div>
          }
        </div>
      } @else {
        <p class="sf-empty">
          {{ tasks().length > 0 ? 'Nenhuma tarefa com esse filtro.' : 'Nenhuma tarefa ainda.' }}
        </p>
      }
    </section>
  `,
  styles: [
    `
      :host {
        display: block;
      }

      .sf-inicio {
        padding: 6px 20px 120px;
      }

      /* Header */
      .sf-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 8px 0 18px;
      }
      .sf-brand {
        display: flex;
        align-items: center;
        gap: 11px;
      }
      .sf-logo {
        width: 36px;
        height: 36px;
        border-radius: 11px;
        background: linear-gradient(150deg, #2cecc4, #00a482);
        display: flex;
        align-items: center;
        justify-content: center;
        box-shadow: 0 6px 16px -4px rgba(0, 200, 160, 0.55);
      }
      .sf-brand-name {
        font-size: 20px;
        font-weight: 700;
        color: var(--text-strong);
        letter-spacing: -0.3px;
      }
      .sf-bell {
        position: relative;
        width: 42px;
        height: 42px;
        border-radius: 13px;
        background: var(--surface-card);
        border: 1px solid var(--border-default);
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        padding: 0;
      }
      .sf-bell-badge {
        position: absolute;
        top: -5px;
        right: -5px;
        min-width: 19px;
        height: 19px;
        padding: 0 5px;
        border-radius: 10px;
        background: var(--color-accent-strong);
        color: var(--text-on-accent);
        font-size: 11px;
        font-weight: 800;
        display: flex;
        align-items: center;
        justify-content: center;
        border: 2px solid var(--surface-page);
      }

      /* Greeting */
      .sf-greeting {
        font-size: 26px;
        font-weight: 700;
        color: var(--text-strong);
        letter-spacing: -0.5px;
        margin: 0;
      }
      .sf-active-count {
        font-size: 16px;
        font-weight: 600;
        color: var(--color-accent);
        margin: 4px 0 0;
      }

      /* Section headers */
      .sf-section-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin: 28px 0 14px;
      }
      .sf-section-head h2 {
        font-size: 18px;
        font-weight: 700;
        color: var(--text-strong);
        margin: 0;
      }
      .sf-link {
        font-size: 14px;
        font-weight: 600;
        color: var(--color-accent);
        cursor: pointer;
        background: none;
        border: none;
        padding: 0;
      }

      /* Active session cards */
      .sf-cards {
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      /* Telas largas: sessões ativas em grid. */
      @media (min-width: 768px) {
        .sf-cards {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
        }
      }
      .sf-card {
        background: var(--surface-card);
        border: 1px solid var(--border-default);
        border-radius: 18px;
        padding: 16px;
        display: flex;
        align-items: center;
        gap: 13px;
        cursor: pointer;
        text-align: left;
        width: 100%;
      }
      .sf-dot {
        width: 11px;
        height: 11px;
        border-radius: 50%;
        flex: none;
      }
      .sf-pulse {
        animation: sf-pulse 1.6s var(--ease-standard) infinite;
      }
      @keyframes sf-pulse {
        0% {
          box-shadow: 0 0 0 0 rgba(52, 211, 153, 0.55);
        }
        70% {
          box-shadow: 0 0 0 7px rgba(52, 211, 153, 0);
        }
        100% {
          box-shadow: 0 0 0 0 rgba(52, 211, 153, 0);
        }
      }
      .sf-pulse-amber {
        animation: sf-pulse-amber 1.6s var(--ease-standard) infinite;
      }
      @keyframes sf-pulse-amber {
        0% {
          box-shadow: 0 0 0 0 rgba(251, 191, 36, 0.55);
        }
        70% {
          box-shadow: 0 0 0 7px rgba(251, 191, 36, 0);
        }
        100% {
          box-shadow: 0 0 0 0 rgba(251, 191, 36, 0);
        }
      }
      @media (prefers-reduced-motion: reduce) {
        .sf-pulse,
        .sf-pulse-amber {
          animation: none;
        }
      }
      .sf-card-body {
        flex: 1;
        min-width: 0;
        display: flex;
        flex-direction: column;
      }
      .sf-card-top {
        display: flex;
        align-items: center;
        gap: 8px;
        min-width: 0;
      }
      .sf-card-name {
        font-size: 16px;
        font-weight: 600;
        color: var(--text-strong);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .sf-agent {
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 0.4px;
        padding: 2px 7px;
        border-radius: 6px;
        flex: none;
      }
      .sf-worker-chip {
        flex: none;
        display: inline-flex;
        align-items: center;
        color: #c084fc;
        background: rgba(192, 132, 252, 0.14);
        border: 1px solid rgba(192, 132, 252, 0.3);
        padding: 2px 5px;
        border-radius: 6px;
      }
      .sf-card-status {
        font-size: 13.5px;
        margin-top: 3px;
      }
      .sf-card-sub {
        font-size: 13px;
        color: #7a8090;
        margin-top: 2px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .sf-chevron {
        flex: none;
      }

      /* Tasks */
      .sf-task-filters {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 6px;
        margin-bottom: 12px;
      }
      .sf-tfilter {
        padding: 5px 11px;
        border-radius: 999px;
        border: 1px solid #283230;
        background: #181c1b;
        color: #9aa0ae;
        font-size: 12.5px;
        font-weight: 600;
        cursor: pointer;
        -webkit-tap-highlight-color: transparent;
      }
      .sf-tfilter.sel {
        color: #06231d;
        background: var(--color-accent, #00e4b4);
        border-color: transparent;
      }
      .sf-tsessel {
        margin-left: auto;
        max-width: 160px;
        padding: 5px 8px;
        border-radius: 9px;
        border: 1px solid #283230;
        background: #181c1b;
        color: #d4d4d4;
        font-size: 12.5px;
        font-family: inherit;
      }
      .sf-task-list {
        background: var(--surface-card);
        border: 1px solid var(--border-default);
        border-radius: 18px;
        overflow: hidden;
      }
      .sf-task {
        display: flex;
        align-items: center;
        gap: 13px;
        padding: 15px 16px;
      }
      .sf-task-divider {
        border-top: 1px solid #23262f;
      }
      .sf-task-clickable {
        cursor: pointer;
        -webkit-tap-highlight-color: transparent;
      }
      .sf-task-clickable:hover {
        background: #1c2422;
      }
      .sf-task-icon {
        width: 30px;
        height: 30px;
        border-radius: 9px;
        flex: none;
        display: flex;
        align-items: center;
        justify-content: center;
      }
      .sf-task-body {
        flex: 1;
        min-width: 0;
        display: flex;
        flex-direction: column;
      }
      .sf-task-title {
        font-size: 15px;
        font-weight: 600;
        color: var(--text-strong);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .sf-task-meta {
        font-size: 12.5px;
        margin-top: 1px;
      }
      /* Chip com o NOME real da sessão (ellipsis só se muito longo). */
      .sf-task-session {
        flex: none;
        max-width: 120px;
        padding: 3px 9px;
        border-radius: 8px;
        background: #22272a;
        border: 1px solid #283230;
        color: #9aa0ae;
        font-size: 11px;
        font-weight: 600;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }

      /* Empty state */
      .sf-empty {
        font-size: 14px;
        color: var(--text-muted);
        margin: 0;
        padding: 4px 2px;
      }
    `,
  ],
})
export class InicioComponent implements OnInit {
  private readonly api = inject(ApiService);
  private readonly sse = inject(SseService);
  private readonly router = inject(Router);

  /** All sessions loaded from the API, refreshed on SSE activity. */
  private readonly sessions = signal<Session[]>([]);
  /** Recent tasks loaded from the API. */
  protected readonly tasks = signal<Task[]>([]);

  /**
   * Badge do sino = quantas sessões precisam de você AGORA (status
   * ``waiting_input``) + notificações novas que chegaram ao vivo nesta sessão.
   * Contar o status (e não só o buffer SSE) faz o badge sobreviver a recargas
   * e refletir o estado real de "tem resposta esperando".
   */
  readonly notifCount = computed(() => {
    const waiting = this.sessions().filter(
      (s) => s.status === 'waiting_input',
    ).length;
    return Math.max(waiting, this.sse.notifications().length);
  });

  /** Active sessions = running or waiting_input. */
  readonly activeSessions = computed(() =>
    this.sessions().filter((s) => ACTIVE_STATUSES.includes(s.status)),
  );

  readonly activeCountLabel = computed(() => {
    const n = this.activeSessions().length;
    return `${n} ${n === 1 ? 'sessão ativa' : 'sessões ativas'}`;
  });

  /** Filtros das tarefas: por status e por sessão. */
  readonly taskStatus = signal<'all' | 'todo' | 'doing' | 'done' | 'blocked'>('all');
  readonly taskSession = signal<string>('');
  readonly taskFilters: { key: 'all' | 'todo' | 'doing' | 'done' | 'blocked'; label: string }[] = [
    { key: 'all', label: 'Todas' },
    { key: 'doing', label: 'Em andamento' },
    { key: 'todo', label: 'A fazer' },
    { key: 'blocked', label: 'Bloqueadas' },
    { key: 'done', label: 'Concluídas' },
  ];
  /** Sessões distintas que têm tarefas (para o seletor). */
  readonly taskSessions = computed(() => {
    const set = new Set<string>();
    for (const t of this.tasks()) {
      if (t.session_id) set.add(t.session_id);
    }
    return [...set].sort();
  });

  readonly recentTasks = computed(() => {
    const st = this.taskStatus();
    const ses = this.taskSession();
    return this.tasks().filter(
      (t) =>
        (st === 'all' || t.state === st) && (!ses || t.session_id === ses),
    );
  });

  /** Tracks how many SSE events we have already reacted to. */
  private lastEventCount = 0;

  constructor() {
    // Live updates: whenever the SSE event buffer grows, re-fetch the lists.
    // Reading the signal inside an effect registers the dependency, so this
    // runs again on every new frame the service decodes.
    effect(() => {
      const count = this.sse.events().length;
      if (count !== this.lastEventCount) {
        this.lastEventCount = count;
        this.reloadSessions();
        this.reloadTasks();
      }
    });
  }

  ngOnInit(): void {
    if (!this.sse.connected()) {
      this.sse.connect();
    }
    this.reloadSessions();
    this.reloadTasks();
  }

  private reloadSessions(): void {
    this.api.listSessions().subscribe({
      next: (list) => this.sessions.set(list ?? []),
      error: () => {
        /* keep last known state */
      },
    });
  }

  private reloadTasks(): void {
    this.api.getTasks().subscribe({
      next: (list) => this.tasks.set(list ?? []),
      error: () => {
        /* keep last known state */
      },
    });
  }

  // --- View helpers ---

  statusMeta(status: SessionStatus) {
    return STATUS_META[status] ?? STATUS_META.detached;
  }

  agent(s: Session) {
    return agentMeta(s.agent_type);
  }

  /** Worker/sub-agente pela convenção de nome (chip ⑂). */
  isWorker(s: Session): boolean {
    return isWorkerSession(s.tmux_name ?? s.display_name);
  }

  agentBg(s: Session): string {
    return this.hexToRgba(this.agent(s).color, 0.16);
  }

  displayName(s: Session): string {
    return s.display_name || s.tmux_name || s.id;
  }

  subline(s: Session): string {
    return s.work_dir || this.agent(s).label;
  }

  taskMeta(state: Task['state']): { label: string; color: string; bg: string } {
    const map: Record<Task['state'], { label: string; color: string }> = {
      todo: { label: 'A fazer', color: 'var(--text-muted)' },
      doing: { label: 'Em andamento', color: 'var(--warning)' },
      blocked: { label: 'Bloqueada', color: 'var(--danger)' },
      done: { label: 'Concluída', color: 'var(--positive)' },
      attention: { label: 'Requer atenção', color: 'var(--warning)' },
    };
    const m = map[state] ?? map.todo;
    return { ...m, bg: this.cssVarToRgba(m.color) };
  }

  /** Nome da sessão da tarefa (chip à direita) — nome real, sem cortar a 6. */
  sessionShort(t: Task): string {
    return t.session_id ?? '';
  }

  // --- Navigation ---

  openNotifications(): void {
    this.router.navigate(['/notificacoes']);
  }
  goSessoes(): void {
    this.router.navigate(['/sessoes']);
  }
  goTimeline(): void {
    this.router.navigate(['/timeline']);
  }
  openSession(id: string): void {
    this.router.navigate(['/sessao', id]);
  }
  /** Clique na tarefa → abre a sessão onde ela está acontecendo. */
  openTaskSession(t: Task): void {
    if (t.session_id) {
      this.router.navigate(['/sessao', t.session_id]);
    }
  }

  // --- Color utils ---

  private hexToRgba(hex: string, alpha: number): string {
    const h = hex.replace('#', '');
    const full = h.length === 3
      ? h.split('').map((c) => c + c).join('')
      : h;
    const r = parseInt(full.slice(0, 2), 16);
    const g = parseInt(full.slice(2, 4), 16);
    const b = parseInt(full.slice(4, 6), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  /** For CSS-var colors we can't parse, fall back to color-mix for the tint. */
  private cssVarToRgba(color: string): string {
    if (color.startsWith('#')) {
      return this.hexToRgba(color, 0.16);
    }
    return `color-mix(in srgb, ${color} 16%, transparent)`;
  }
}
