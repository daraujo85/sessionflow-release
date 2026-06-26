import { Injectable, inject, signal } from '@angular/core';
import { API_BASE_URL } from './api.service';
import { AuthService } from './auth.service';
import { ShareSessionService } from './share-session.service';
import { NotifyService } from './notify.service';
import { EventItem } from './models';

/**
 * A line of terminal output streamed over SSE. The backend emits these on the
 * same channel as {@link EventItem}, discriminated by the presence of `seq`.
 */
export interface SseOutputLine {
  session_id: string;
  seq: number;
  text: string;
  line_type: string;
}

/**
 * Any payload decoded from an SSE `data:` frame. Either a structured event
 * (which carries an `id`) or a terminal output line (which carries a `seq`).
 */
export type SseEvent = EventItem | SseOutputLine;

/** Type guard: a decoded frame is an output line when it has a numeric `seq`. */
export function isOutputLine(e: SseEvent): e is SseOutputLine {
  return typeof (e as SseOutputLine).seq === 'number';
}

/** Frame de áudio do JARVIS (resumo falado) empurrado pelo worker via SSE. */
export interface JarvisAudioFrame {
  session_id: string;
  title?: string;
  text?: string;
  audio_b64: string;
  mime?: string;
  at?: string;
}

/** Maximum entries kept in each rolling buffer. */
const MAX_BUFFER = 500;

/** Initial reconnect delay in ms; doubles each attempt up to {@link MAX_BACKOFF}. */
const BASE_BACKOFF = 1000;
/** Cap for the reconnect backoff in ms. */
const MAX_BACKOFF = 30_000;

/**
 * Subscribes to the backend's server-sent events stream and exposes the
 * decoded events as reactive signals. Automatically reconnects with
 * exponential backoff on error until {@link disconnect} is called.
 */
@Injectable({ providedIn: 'root' })
export class SseService {
  private readonly baseUrl = inject(API_BASE_URL);
  private readonly auth = inject(AuthService);
  private readonly share = inject(ShareSessionService);
  private readonly notify = inject(NotifyService);

  /** Ids de eventos já notificados no sistema (evita duplicar em reconexão). */
  private readonly notifiedIds = new Set<string>();

  /** Whether the underlying EventSource is currently open. */
  readonly connected = signal(false);
  /** The most recently decoded frame, or null before the first message. */
  readonly lastEvent = signal<SseEvent | null>(null);
  /** Rolling buffer of decoded structured events (newest last). */
  readonly events = signal<EventItem[]>([]);
  /** Rolling buffer of terminal output lines (newest last). */
  readonly outputLines = signal<SseOutputLine[]>([]);
  /** Rolling buffer of notification-kind events. */
  readonly notifications = signal<EventItem[]>([]);
  /** Último espelho de tela por sessão (tmux_name) — empurrado pelo worker. */
  readonly screens = signal<Record<string, { text: string; at: string }>>({});
  /**
   * Último frame de áudio do JARVIS (resumo falado). Transiente: o worker
   * publica `type=jarvis_audio` com o áudio em base64; o {@link JarvisAudioService}
   * reage a este signal e toca no aparelho.
   */
  readonly jarvisAudio = signal<JarvisAudioFrame | null>(null);

  private source: EventSource | null = null;
  private sessionId?: string;
  private backoff = BASE_BACKOFF;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  /** When true, no further reconnection attempts are scheduled. */
  private stopped = false;

  /**
   * Opens an EventSource to `{apiBase}/events`, optionally scoped to a session.
   * Safe to call repeatedly; any existing connection is torn down first.
   */
  connect(sessionId?: string): void {
    // Idempotente: se já há conexão com o mesmo escopo, não derruba/reabre
    // (App e telas chamam connect()) — evita churn de reconexão.
    if (this.source && !this.stopped && this.sessionId === sessionId) {
      return;
    }
    this.disconnect();
    this.stopped = false;
    this.sessionId = sessionId;
    this.open();
  }

  /**
   * Zera o buffer em memória de notificações (o "Limpar todas" do sininho).
   * Some o badge na hora; novas notificações ao vivo voltam a entrar normal.
   * O backend guarda o watermark — o reload também volta limpo.
   */
  clearNotifications(): void {
    this.notifications.set([]);
  }

  /** Closes the stream and cancels any pending reconnect. */
  disconnect(): void {
    this.stopped = true;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.source) {
      this.source.close();
      this.source = null;
    }
    this.connected.set(false);
  }

  private open(): void {
    // Ambientes sem EventSource (SSR/testes jsdom) — no-op seguro.
    if (typeof EventSource === 'undefined') {
      return;
    }

    // EventSource não manda header Authorization → o token vai na query
    // (a API aceita ?token=<jwt> além do header Bearer).
    const params = new URLSearchParams();
    if (this.sessionId) {
      params.set('session', this.sessionId);
    }
    const token = this.auth.token();
    const shareToken = this.share.token();
    if (token) {
      params.set('token', token);
    } else if (shareToken) {
      // Convidado: o SSE também vai escopado pelo token de share (?k=).
      params.set('k', shareToken);
    }
    const qs = params.toString();
    const url = `${this.baseUrl}/events${qs ? `?${qs}` : ''}`;

    const source = new EventSource(url);
    this.source = source;

    source.onopen = () => {
      this.connected.set(true);
      // A healthy connection resets the backoff window.
      this.backoff = BASE_BACKOFF;
    };

    source.onmessage = (ev: MessageEvent) => this.handleMessage(ev);

    source.onerror = () => {
      this.connected.set(false);
      source.close();
      if (this.source === source) {
        this.source = null;
      }
      this.scheduleReconnect();
    };
  }

  private handleMessage(ev: MessageEvent): void {
    const raw = typeof ev.data === 'string' ? ev.data.trim() : '';
    // Ignore heartbeats / empty frames. Comment frames (": ping") never reach
    // onmessage, but guard against blank data lines anyway.
    if (!raw || raw.startsWith(':')) {
      return;
    }

    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      // Malformed JSON: ignore the frame entirely.
      return;
    }

    if (!parsed || typeof parsed !== 'object') {
      return;
    }

    // Espelho da tela empurrado pelo worker (kind="screen"): guarda só o último
    // por sessão (tmux_name). Não polui lastEvent/events/notifications.
    const raw2 = parsed as { kind?: string; session_id?: string; tmux_name?: string; text?: string; at?: string };
    if (raw2.kind === 'screen') {
      const key = raw2.session_id || raw2.tmux_name;
      if (key) {
        this.screens.update((m) => ({
          ...m,
          [key]: { text: raw2.text ?? '', at: raw2.at ?? '' },
        }));
      }
      return;
    }

    // JARVIS: áudio transiente (resumo falado). Não entra em events/notifications.
    const jv = parsed as { type?: string; audio_b64?: string };
    if (jv.type === 'jarvis_audio' && jv.audio_b64) {
      this.jarvisAudio.set(parsed as JarvisAudioFrame);
      return;
    }

    const frame = parsed as SseEvent;
    this.lastEvent.set(frame);

    if (isOutputLine(frame)) {
      this.outputLines.update((lines) => push(lines, frame));
      return;
    }

    // Structured event.
    this.events.update((list) => push(list, frame));
    // Eventos que pedem atenção do usuário também alimentam as notificações.
    if (
      frame.type === 'notification' ||
      frame.type === 'attention' ||
      frame.kind === 'attention' ||
      frame.kind === 'success'
    ) {
      this.notifications.update((list) => push(list, frame));
      this.fireSystemNotification(frame);
    }
  }

  /**
   * Dispara uma notificação do SISTEMA para o evento, uma única vez por id
   * (evita duplicar em reconexões). Best-effort — sem permissão é no-op.
   */
  private fireSystemNotification(frame: EventItem): void {
    const id = frame.id;
    if (id && this.notifiedIds.has(id)) {
      return;
    }
    if (id) {
      this.notifiedIds.add(id);
    }
    void this.notify.notify(frame.title || 'SessionFlow', {
      body: frame.desc || '',
      tag: id || frame.session_id || undefined,
      url: frame.session_id ? `/sessao/${frame.session_id}` : undefined,
    });
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.reconnectTimer !== null) {
      return;
    }
    const delay = this.backoff;
    this.backoff = Math.min(this.backoff * 2, MAX_BACKOFF);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (!this.stopped) {
        this.open();
      }
    }, delay);
  }
}

/** Appends `item` to `buffer`, trimming the oldest entries past {@link MAX_BUFFER}. */
function push<T>(buffer: T[], item: T): T[] {
  const next = [...buffer, item];
  return next.length > MAX_BUFFER ? next.slice(next.length - MAX_BUFFER) : next;
}
