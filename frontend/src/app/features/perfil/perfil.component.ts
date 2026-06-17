import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  OnDestroy,
  OnInit,
  computed,
  effect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { Router } from '@angular/router';
import { ApiService } from '../../core/api.service';
import { SseService } from '../../core/sse.service';
import { AuthService } from '../../core/auth.service';
import { PwaInstallService } from '../../core/pwa-install.service';
import { NotifyService } from '../../core/notify.service';
import {
  Session,
  SessionStatus,
  UsageInfo,
  WorkerStatus,
} from '../../core/models';

/** Lado máximo (px) para onde a foto é redimensionada antes de enviar. */
const PHOTO_MAX_SIDE = 256;

/** Session statuses considered "active right now". */
const ACTIVE_STATUSES: readonly SessionStatus[] = ['running', 'waiting_input'];

/**
 * Profile screen ("Perfil"). Pixel-for-pixel with the mockup (showPerfil).
 * The Worker status card is honestly derived from the live SSE connection
 * (there is no dedicated Worker endpoint yet); host/uptime stay "—" rather
 * than being fabricated. Stats come from listSessions. The "Sair" action is
 * a placeholder for now.
 */
@Component({
  selector: 'sf-perfil',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="sf-perfil">
      <div class="sf-title-row">
        <span class="sf-title">Perfil</span>
      </div>

      <!-- Identity -->
      <div class="sf-identity">
        <button
          type="button"
          class="sf-avatar"
          (click)="pickPhoto()"
          [attr.aria-label]="photo() ? 'Trocar foto de perfil' : 'Adicionar foto de perfil'"
        >
          @if (photo()) {
            <img class="sf-avatar-img" [src]="photo()" alt="" />
          } @else {
            <span aria-hidden="true">D</span>
          }
          <span class="sf-avatar-cam" aria-hidden="true">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" />
              <circle cx="12" cy="13" r="4" />
            </svg>
          </span>
        </button>
        <input
          #fileInput
          type="file"
          accept="image/*"
          hidden
          (change)="onPhotoSelected($event)"
        />
        <div class="sf-identity-body">
          <div class="sf-name">Diego</div>
          <div class="sf-role">Operador · sessionflow.local</div>
          @if (photo()) {
            <button type="button" class="sf-photo-remove" (click)="removePhoto()">
              Remover foto
            </button>
          }
        </div>
      </div>

      <!-- Worker status -->
      <div class="sf-worker">
        <span
          class="sf-worker-dot"
          [class.sf-pulse]="connected()"
          [style.background]="connected() ? '#34D399' : '#7A8090'"
        ></span>
        <div class="sf-worker-body">
          <div class="sf-worker-label">{{ workerTitle() }}</div>
          <div class="sf-worker-meta">{{ workerMeta() }}</div>
        </div>
        <span
          class="sf-worker-pill"
          [style.color]="connected() ? '#34D399' : '#8A90A0'"
          [style.background]="
            connected() ? 'rgba(52,211,153,.14)' : 'rgba(138,144,160,.14)'
          "
        >
          {{ connected() ? 'online' : 'offline' }}
        </span>
      </div>

      <!-- Stats -->
      <div class="sf-stats">
        <div class="sf-stat">
          <div class="sf-stat-value">{{ sessionsToday() }}</div>
          <div class="sf-stat-label">Sessões hoje</div>
        </div>
        <div class="sf-stat">
          <div class="sf-stat-value sf-stat-accent">{{ activeNow() }}</div>
          <div class="sf-stat-label">Ativas agora</div>
        </div>
      </div>

      <!-- Limites de uso (reais) -->
      <div class="sf-limits">
        <div class="sf-limits-head">Limites de uso</div>
        @if (claudeLimits(); as cl) {
          <div class="sf-limit">
            <div class="sf-limit-row">
              <span class="sf-limit-name">Claude · sessão (5h)</span>
              <span class="sf-limit-pct">{{ fmtPct(cl.session_pct) }}</span>
            </div>
            <div class="sf-limit-bar">
              <span [style.width.%]="cl.session_pct ?? 0"></span>
            </div>
          </div>
          <div class="sf-limit">
            <div class="sf-limit-row">
              <span class="sf-limit-name">Claude · semana</span>
              <span class="sf-limit-pct">{{ fmtPct(cl.week_pct) }}</span>
            </div>
            <div class="sf-limit-bar">
              <span [style.width.%]="cl.week_pct ?? 0"></span>
            </div>
          </div>
        } @else {
          <div class="sf-limit-empty">Sem dados de uso ainda.</div>
        }
        <div class="sf-limit-note">
          Gemini, Codex e OpenCode não expõem uso — sem dados disponíveis.
        </div>
      </div>

      <!-- Settings -->
      <div class="sf-settings">
        @for (s of settings(); track s.key; let first = $first) {
          <div
            class="sf-setting"
            [class.sf-divider]="!first"
            [class.sf-clickable]="s.kind !== 'toggle' || !s.disabled"
            (click)="onRowClick(s)"
          >
            <span class="sf-setting-icon" aria-hidden="true">
              @switch (s.key) {
                @case ('push') {
                  <svg
                    width="17"
                    height="17"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
                    <path d="M13.7 21a2 2 0 0 1-3.4 0" />
                  </svg>
                }
                @case ('realtime') {
                  <svg
                    width="17"
                    height="17"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M13 2 3 14h7l-1 8 10-12h-7l1-8z" />
                  </svg>
                }
                @case ('dark') {
                  <svg
                    width="17"
                    height="17"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z" />
                  </svg>
                }
                @case ('milestones') {
                  <svg
                    width="17"
                    height="17"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M9 6h11M9 12h11M9 18h11M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2" />
                  </svg>
                }
                @case ('lang') {
                  <svg
                    width="17"
                    height="17"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <circle cx="12" cy="12" r="9" />
                    <path
                      d="M3 12h18M12 3a14 14 0 0 1 0 18 14 14 0 0 1 0-18z"
                    />
                  </svg>
                }
              }
            </span>
            <span class="sf-setting-label">
              {{ s.title }}
              @if (s.soon) {
                <span class="sf-tag">em breve</span>
              }
            </span>

            @if (s.kind === 'toggle') {
              <button
                type="button"
                role="switch"
                class="sf-switch"
                [class.sf-switch-on]="s.value"
                [disabled]="s.disabled"
                [attr.aria-checked]="s.value"
                [attr.aria-label]="s.title"
                (click)="$event.stopPropagation(); toggle(s.key)"
              >
                <span class="sf-knob"></span>
              </button>
            } @else {
              <span class="sf-setting-value">{{ s.display }}</span>
            }
          </div>
        }
      </div>

      <!-- Testar notificação do sistema (confirma se aparece no device) -->
      @if (notify.permission() === 'granted') {
        <button type="button" class="sf-test-notif" (click)="testNotify()">
          Testar notificação
        </button>
      }

      <!-- Instalar como app (PWA) -->
      @if (canInstall()) {
        <button type="button" class="sf-install" (click)="installApp()">
          <span class="sf-install-icon" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 3v12M7 10l5 5 5-5" />
              <path d="M5 21h14" />
            </svg>
          </span>
          <span class="sf-install-body">
            <span class="sf-install-title">Instalar como app</span>
            <span class="sf-install-sub">Abre em tela cheia, igual a um app nativo</span>
          </span>
        </button>
        @if (showIosHelp()) {
          <div class="sf-ios-help">
            No Safari, toque em <strong>Compartilhar</strong>
            <span class="sf-ios-share" aria-hidden="true">⬆️</span> e depois em
            <strong>Adicionar à Tela de Início</strong>.
          </div>
        }
      }

      <!-- Logout -->
      <div class="sf-logout" (click)="logout()">Sair</div>
    </section>
  `,
  styles: [
    `
      :host {
        display: block;
      }

      .sf-perfil {
        padding: 6px 20px 120px;
      }

      .sf-title-row {
        padding: 10px 0 22px;
      }
      .sf-title {
        font-size: 28px;
        font-weight: 700;
        color: #f4f5f7;
        letter-spacing: -0.6px;
      }

      /* Identity */
      .sf-identity {
        display: flex;
        align-items: center;
        gap: 14px;
        margin-bottom: 22px;
      }
      .sf-avatar {
        position: relative;
        width: 58px;
        height: 58px;
        border-radius: 18px;
        flex: none;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 24px;
        font-weight: 800;
        color: #06231d;
        background: linear-gradient(150deg, #2cecc4, #00a482);
        border: none;
        padding: 0;
        cursor: pointer;
        overflow: visible;
      }
      .sf-avatar-img {
        width: 100%;
        height: 100%;
        border-radius: 18px;
        object-fit: cover;
      }
      .sf-avatar-cam {
        position: absolute;
        right: -4px;
        bottom: -4px;
        width: 24px;
        height: 24px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        color: #f4f5f7;
        background: #22272a;
        border: 2px solid #0e1113;
      }
      .sf-photo-remove {
        margin-top: 4px;
        padding: 0;
        background: none;
        border: none;
        color: #8a90a0;
        font-size: 12.5px;
        cursor: pointer;
        text-decoration: underline;
      }
      .sf-photo-remove:hover {
        color: #f87171;
      }
      .sf-name {
        font-size: 19px;
        font-weight: 700;
        color: #f4f5f7;
      }
      .sf-role {
        font-size: 13.5px;
        color: #8a90a0;
      }

      /* Worker */
      .sf-worker {
        background: #181c1b;
        border: 1px solid #283230;
        border-radius: 18px;
        padding: 16px;
        margin-bottom: 18px;
        display: flex;
        align-items: center;
        gap: 13px;
      }
      .sf-worker-dot {
        width: 11px;
        height: 11px;
        border-radius: 50%;
        flex: none;
      }
      .sf-pulse {
        animation: sf-pulse-green 2.4s infinite;
      }
      @keyframes sf-pulse-green {
        0% {
          box-shadow: 0 0 0 0 rgba(0, 228, 180, 0.5);
        }
        70% {
          box-shadow: 0 0 0 7px rgba(0, 228, 180, 0);
        }
        100% {
          box-shadow: 0 0 0 0 rgba(0, 228, 180, 0);
        }
      }
      @media (prefers-reduced-motion: reduce) {
        .sf-pulse {
          animation: none;
        }
      }
      .sf-worker-body {
        flex: 1;
        min-width: 0;
      }
      .sf-worker-label {
        font-size: 15px;
        font-weight: 600;
        color: #f4f5f7;
      }
      .sf-worker-meta {
        font-size: 12.5px;
        color: #7a8090;
        font-family: 'JetBrains Mono', monospace;
        margin-top: 2px;
      }
      .sf-worker-pill {
        font-size: 11px;
        font-weight: 700;
        padding: 4px 9px;
        border-radius: 8px;
        flex: none;
      }

      /* Stats */
      .sf-stats {
        display: flex;
        gap: 12px;
        margin-bottom: 18px;
      }
      .sf-stat {
        flex: 1;
        background: #181c1b;
        border: 1px solid #283230;
        border-radius: 16px;
        padding: 15px;
      }
      .sf-stat-value {
        font-size: 24px;
        font-weight: 800;
        color: #f4f5f7;
      }
      .sf-stat-accent {
        color: #00e4b4;
      }
      .sf-stat-label {
        font-size: 12.5px;
        color: #7a8090;
        margin-top: 2px;
      }

      /* Limites de uso */
      .sf-limits {
        background: #181c1b;
        border: 1px solid #283230;
        border-radius: 18px;
        padding: 16px;
        margin-bottom: 18px;
      }
      .sf-limits-head {
        font-size: 13px;
        font-weight: 700;
        letter-spacing: 0.3px;
        text-transform: uppercase;
        color: #7a8090;
        margin-bottom: 12px;
      }
      .sf-limit {
        margin-bottom: 12px;
      }
      .sf-limit-row {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        margin-bottom: 6px;
      }
      .sf-limit-name {
        font-size: 13.5px;
        color: #f4f5f7;
      }
      .sf-limit-pct {
        font-size: 13.5px;
        font-weight: 700;
        color: #00e4b4;
        font-family: 'JetBrains Mono', monospace;
      }
      .sf-limit-bar {
        height: 6px;
        border-radius: 999px;
        background: #2a3130;
        overflow: hidden;
      }
      .sf-limit-bar > span {
        display: block;
        height: 100%;
        border-radius: 999px;
        background: linear-gradient(90deg, #2cecc4, #00a482);
        transition: width 0.3s ease;
      }
      .sf-limit-empty {
        font-size: 13px;
        color: #7a8090;
      }
      .sf-limit-note {
        margin-top: 8px;
        font-size: 11.5px;
        color: #6b7280;
      }

      /* Settings list */
      .sf-settings {
        background: #181c1b;
        border: 1px solid #283230;
        border-radius: 18px;
        overflow: hidden;
      }
      .sf-setting {
        display: flex;
        align-items: center;
        gap: 13px;
        padding: 15px 16px;
      }
      .sf-clickable {
        cursor: pointer;
      }
      .sf-divider {
        border-top: 1px solid #23262f;
      }
      .sf-setting-icon {
        width: 30px;
        height: 30px;
        border-radius: 9px;
        flex: none;
        display: flex;
        align-items: center;
        justify-content: center;
        color: #9aa0ae;
        background: #22272a;
      }
      .sf-setting-label {
        flex: 1;
        font-size: 15px;
        font-weight: 500;
        color: #f4f5f7;
        display: inline-flex;
        align-items: center;
        gap: 8px;
      }
      .sf-tag {
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 0.4px;
        text-transform: uppercase;
        color: #7a8090;
        background: #22272a;
        border: 1px solid #283230;
        padding: 2px 7px;
        border-radius: 6px;
      }
      .sf-setting-value {
        font-size: 13.5px;
        color: #7a8090;
        flex: none;
      }

      /* Switch — 44x26 radius 13px, on = #00A482 */
      .sf-switch {
        width: 44px;
        height: 26px;
        border-radius: 13px;
        flex: none;
        padding: 3px;
        box-sizing: border-box;
        background: #2a3130;
        border: none;
        cursor: pointer;
        display: flex;
        justify-content: flex-start;
        transition: background 0.2s;
      }
      .sf-switch-on {
        background: #00a482;
        justify-content: flex-end;
      }
      .sf-switch:disabled {
        opacity: 0.45;
        cursor: not-allowed;
      }
      .sf-knob {
        width: 20px;
        height: 20px;
        border-radius: 50%;
        background: #fff;
      }

      /* Testar notificação */
      .sf-test-notif {
        width: 100%;
        margin-top: 12px;
        padding: 12px;
        background: none;
        border: 1px dashed #283230;
        border-radius: 14px;
        color: #8a90a0;
        font-size: 13.5px;
        font-weight: 600;
        cursor: pointer;
      }

      /* Instalar como app */
      .sf-install {
        width: 100%;
        margin-top: 18px;
        display: flex;
        align-items: center;
        gap: 13px;
        padding: 15px 16px;
        text-align: left;
        background: #181c1b;
        border: 1px solid #283230;
        border-radius: 18px;
        cursor: pointer;
      }
      .sf-install-icon {
        width: 34px;
        height: 34px;
        border-radius: 10px;
        flex: none;
        display: flex;
        align-items: center;
        justify-content: center;
        color: #06231d;
        background: linear-gradient(150deg, #2cecc4, #00a482);
      }
      .sf-install-body {
        flex: 1;
        min-width: 0;
        display: flex;
        flex-direction: column;
      }
      .sf-install-title {
        font-size: 15px;
        font-weight: 600;
        color: #f4f5f7;
      }
      .sf-install-sub {
        font-size: 12.5px;
        color: #7a8090;
        margin-top: 2px;
      }
      .sf-ios-help {
        margin-top: 10px;
        padding: 13px 15px;
        background: #181c1b;
        border: 1px solid #283230;
        border-radius: 14px;
        font-size: 13px;
        line-height: 1.5;
        color: #b9bfca;
      }
      .sf-ios-help strong {
        color: #f4f5f7;
        font-weight: 600;
      }
      .sf-ios-share {
        font-style: normal;
      }

      /* Logout */
      .sf-logout {
        margin-top: 18px;
        text-align: center;
        padding: 15px;
        border-radius: 14px;
        border: 1px solid #3a2326;
        color: #f87171;
        font-size: 15px;
        font-weight: 600;
        cursor: pointer;
      }
    `,
  ],
})
export class PerfilComponent implements OnInit, OnDestroy {
  private readonly api = inject(ApiService);
  private readonly sse = inject(SseService);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly pwa = inject(PwaInstallService);
  protected readonly notify = inject(NotifyService);

  /** Input de arquivo escondido, disparado pelo clique no avatar. */
  private readonly fileInput = viewChild<ElementRef<HTMLInputElement>>('fileInput');

  /** Foto de perfil (data URL) persistida no cliente — null = inicial "D". */
  readonly photo = signal<string | null>(null);

  /**
   * Mostra a opção de instalar (prompt nativo ou instruções iOS). Computed para
   * reagir quando `beforeinstallprompt` chega depois da construção do componente.
   */
  readonly canInstall = computed(() => this.pwa.shouldOffer());
  /** Quando true, exibe as instruções manuais do iOS/Safari. */
  readonly showIosHelp = signal(false);

  /** All sessions loaded from the API, refreshed on SSE activity. */
  private readonly sessions = signal<Session[]>([]);

  /** Status REAL do Worker (heartbeat do host) — null antes de carregar. */
  private readonly worker = signal<WorkerStatus | null>(null);
  /** Limites de uso reais (hoje só Claude). */
  private readonly usage = signal<UsageInfo | null>(null);

  /** Online = heartbeat recente do worker; SSE como reforço. */
  readonly connected = computed(
    () => this.worker()?.online === true || this.sse.connected(),
  );

  readonly workerTitle = computed(() => {
    const host = this.worker()?.hostname;
    if (!this.connected()) {
      return 'Worker desconectado';
    }
    return host ? `Worker · ${host}` : 'Worker conectado';
  });

  /** Host · uptime em mono — tempo real do worker, "—" quando desconhecido. */
  readonly workerMeta = computed(() => {
    const w = this.worker();
    const host = w?.hostname ?? '—';
    const up = w?.online ? formatUptime(w.uptime_seconds) : '—';
    return `${host} · uptime ${up}`;
  });

  /** Limites do Claude (barras de % sessão/semana), ou null se sem dados. */
  readonly claudeLimits = computed(() => this.usage()?.claude ?? null);

  /** Active right now = running or waiting_input. */
  readonly activeNow = computed(
    () =>
      this.sessions().filter((s) => ACTIVE_STATUSES.includes(s.status)).length,
  );

  /**
   * Sessions "today". The Session model carries no reliable created/updated
   * timestamp, so we cannot filter by date with confidence. We surface the
   * total session count as the closest honest figure rather than fabricating
   * a per-day number.
   */
  readonly sessionsToday = computed(() => this.sessions().length);

  /** Local-only settings state (no persistence/endpoint yet — Fase 2). */
  private readonly realtimeEnabled = signal(true);
  private readonly darkEnabled = signal(true);
  /** Auto-instruir sessões a trabalhar em tarefas/marcos (setting global). */
  private readonly milestonesAuto = signal(true);

  readonly settings = computed<SettingRow[]>(() => [
    {
      key: 'push',
      kind: 'toggle',
      title: 'Notificações',
      value: this.notify.permission() === 'granted',
      // Sem suporte ou já negada pelo SO → não dá pra ligar pelo app.
      disabled:
        this.notify.permission() === 'unsupported' ||
        this.notify.permission() === 'denied',
    },
    {
      key: 'realtime',
      kind: 'toggle',
      title: 'Tempo real (SSE)',
      value: this.realtimeEnabled(),
    },
    {
      key: 'milestones',
      kind: 'toggle',
      title: 'Trabalhar em tarefas',
      value: this.milestonesAuto(),
    },
    {
      key: 'dark',
      kind: 'toggle',
      title: 'Tema escuro',
      value: this.darkEnabled(),
    },
    {
      key: 'lang',
      kind: 'value',
      title: 'Idioma',
      display: 'Português (BR)',
    },
  ]);

  private lastEventCount = 0;

  constructor() {
    // Live updates: re-fetch sessions whenever the SSE event buffer grows.
    effect(() => {
      const count = this.sse.events().length;
      if (count !== this.lastEventCount) {
        this.lastEventCount = count;
        this.reloadSessions();
      }
    });
  }

  private pollTimer: ReturnType<typeof setInterval> | null = null;

  ngOnInit(): void {
    if (!this.sse.connected()) {
      this.sse.connect();
    }
    this.reloadSessions();
    this.reloadWorker();
    this.reloadUsage();
    // Setting global de auto-instruir tarefas.
    this.api.getSettings().subscribe({
      next: (s) => this.milestonesAuto.set(s.milestones_auto),
      error: () => {
        /* mantém default (on) */
      },
    });
    // Foto vem do servidor (vale em qualquer dispositivo).
    this.api.getProfile().subscribe({
      next: (p) => this.photo.set(p.photo ?? null),
      error: () => {
        /* sem foto / offline — mantém vazio */
      },
    });
    // Worker faz heartbeat a cada ~10s; atualizamos status/limites a cada 15s.
    this.pollTimer = setInterval(() => {
      this.reloadWorker();
      this.reloadUsage();
    }, 15000);
  }

  ngOnDestroy(): void {
    if (this.pollTimer !== null) {
      clearInterval(this.pollTimer);
    }
  }

  private reloadSessions(): void {
    this.api.listSessions().subscribe({
      next: (list) => this.sessions.set(list ?? []),
      error: () => {
        /* keep last known state */
      },
    });
  }

  private reloadWorker(): void {
    this.api.getWorker().subscribe({
      next: (w) => this.worker.set(w),
      error: () => {
        /* keep last known state */
      },
    });
  }

  private reloadUsage(): void {
    this.api.getUsage().subscribe({
      next: (u) => this.usage.set(u),
      error: () => {
        /* keep last known state */
      },
    });
  }

  /** Row tap — value rows (e.g. Idioma) are placeholders for now. */
  onRowClick(s: SettingRow): void {
    if (s.kind === 'value') {
      // 'lang' link — no language switcher wired yet.
    }
  }

  /** Toggles a local setting. Push stays locked (Fase 2). */
  toggle(key: SettingRow['key']): void {
    switch (key) {
      case 'push':
        // Só dá pra PEDIR a permissão; revogar é só nas configs do SO.
        if (this.notify.permission() !== 'granted') {
          void this.notify.requestPermission();
        }
        break;
      case 'realtime':
        this.realtimeEnabled.update((v) => !v);
        break;
      case 'dark':
        this.darkEnabled.update((v) => !v);
        break;
      case 'milestones': {
        const next = !this.milestonesAuto();
        this.milestonesAuto.set(next); // otimista
        this.api.setSettings(next).subscribe({
          next: (s) => this.milestonesAuto.set(s.milestones_auto),
          error: () => this.milestonesAuto.set(!next), // reverte em erro
        });
        break;
      }
      default:
        // 'lang' has no toggle.
        break;
    }
  }

  /** Formata um percentual real (0–100) ou "—" quando ausente. */
  protected fmtPct(p: number | null | undefined): string {
    return p == null ? '—' : `${Math.round(p)}%`;
  }

  /** Abre o seletor de arquivo para escolher a foto de perfil. */
  pickPhoto(): void {
    this.fileInput()?.nativeElement.click();
  }

  /** Lê a imagem escolhida, redimensiona e persiste como data URL. */
  onPhotoSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) {
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const src = String(reader.result);
      this.downscale(src)
        .then((dataUrl) => this.persistPhoto(dataUrl))
        // Se o canvas falhar, tenta o original (melhor que perder a foto).
        .catch(() => this.persistPhoto(src));
    };
    reader.readAsDataURL(file);
    // Permite re-selecionar o mesmo arquivo depois.
    input.value = '';
  }

  /** Salva a foto NO SERVIDOR (não em localStorage) e reflete na UI. */
  private persistPhoto(dataUrl: string): void {
    this.photo.set(dataUrl); // otimista
    this.api.setProfilePhoto(dataUrl).subscribe({
      next: (r) => this.photo.set(r.photo ?? dataUrl),
      error: () => {
        /* mantém a otimista; próximo load reconcilia */
      },
    });
  }

  /** Remove a foto no servidor e volta para o avatar com a inicial. */
  removePhoto(): void {
    this.photo.set(null);
    this.api.clearProfilePhoto().subscribe({
      next: () => this.photo.set(null),
      error: () => {
        /* ignora */
      },
    });
  }

  /**
   * Redimensiona a imagem para no máximo {@link PHOTO_MAX_SIDE}px de lado
   * (mantendo proporção) e devolve um JPEG comprimido — evita estourar a
   * cota do localStorage com fotos grandes.
   */
  private downscale(src: string): Promise<string> {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const scale = Math.min(1, PHOTO_MAX_SIDE / Math.max(img.width, img.height));
        const w = Math.round(img.width * scale);
        const h = Math.round(img.height * scale);
        const canvas = document.createElement('canvas');
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext('2d');
        if (!ctx) {
          reject(new Error('no 2d context'));
          return;
        }
        ctx.drawImage(img, 0, 0, w, h);
        resolve(canvas.toDataURL('image/jpeg', 0.85));
      };
      img.onerror = () => reject(new Error('image load failed'));
      img.src = src;
    });
  }

  /** Instala o app: prompt nativo (Chromium) ou instruções no iOS. */
  async installApp(): Promise<void> {
    if (this.pwa.canPrompt()) {
      // canInstall é computed → reage sozinho quando o prompt é consumido.
      await this.pwa.promptInstall();
      return;
    }
    if (this.pwa.isIos) {
      this.showIosHelp.update((v) => !v);
    }
  }

  /** Dispara uma notificação do sistema de teste (diagnóstico). */
  testNotify(): void {
    void this.notify.notify('SessionFlow', {
      body: 'Notificação de teste ✅ — está funcionando!',
      tag: 'sf-test',
    });
  }

  /** Encerra a sessão e volta para o login. */
  logout(): void {
    this.auth.logout();
    this.router.navigate(['/login']);
  }
}

/** Formata segundos de uptime em "Xd Yh", "Xh Ymin" ou "Xmin". */
function formatUptime(seconds: number | null | undefined): string {
  if (seconds == null || seconds < 0) {
    return '—';
  }
  const m = Math.floor(seconds / 60);
  if (m < 1) {
    return '<1min';
  }
  const days = Math.floor(m / 1440);
  const hours = Math.floor((m % 1440) / 60);
  const mins = m % 60;
  if (days > 0) {
    return `${days}d ${hours}h`;
  }
  if (hours > 0) {
    return `${hours}h ${mins}min`;
  }
  return `${mins}min`;
}

/** A single row in the settings list (toggle or read-only value). */
interface SettingRow {
  key: 'push' | 'realtime' | 'dark' | 'lang' | 'milestones';
  kind: 'toggle' | 'value';
  title: string;
  /** Toggle state (toggle rows only). */
  value?: boolean;
  /** Read-only display text (value rows only). */
  display?: string;
  disabled?: boolean;
  soon?: boolean;
}
