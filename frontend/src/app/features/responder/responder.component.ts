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
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../core/api.service';
import { OutputLine, Session } from '../../core/models';
import { SseService } from '../../core/sse.service';
import { agentMeta } from '../../shared/status-color';
import { AudioRecorderComponent } from '../../shared/audio-recorder/audio-recorder.component';
import { AudioRecorderService } from '../../shared/audio-recorder/audio-recorder.service';

/** A quick-reply chip with its mockup color treatment. */
interface QuickReply {
  readonly label: string;
  /** 'approve' = green tint, 'reject' = red tint, 'neutral' = dark/neutral. */
  readonly tone: 'approve' | 'reject' | 'neutral';
}

/** Pre-canned answers shown as chips above the textarea (mockup "FEEDBACK"). */
const QUICK_REPLIES: readonly QuickReply[] = [
  { label: 'Aprovar', tone: 'approve' },
  { label: 'Rejeitar', tone: 'reject' },
  { label: 'Refazer', tone: 'neutral' },
  { label: 'Continuar', tone: 'neutral' },
  { label: 'Rodar os testes', tone: 'neutral' },
];

/** Fallback question when the agent's output carries no `ask` line. */
const FALLBACK_ASK = 'O agente está aguardando a sua decisão para continuar.';

@Component({
  selector: 'sf-responder',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, AudioRecorderComponent],
  template: `
    <section class="sf-responder">
      <header class="sf-head">
        <h1 class="sf-title">Responder</h1>
        <p class="sf-sub">
          {{ waiting().length }}
          {{ waiting().length === 1 ? 'sessão' : 'sessões' }} aguardando a sua
          decisão.
        </p>
      </header>

      @if (loading()) {
        <p class="sf-msg">Carregando…</p>
      } @else if (error()) {
        <p class="sf-msg sf-msg--error">
          Não foi possível carregar as sessões.
        </p>
      } @else if (waiting().length === 0) {
        <div class="sf-empty">
          <p class="sf-empty__title">Tudo em dia</p>
          <p class="sf-empty__sub">Nenhuma sessão aguardando resposta.</p>
        </div>
      } @else {
        <!-- Session picker (only shown when more than one is waiting). -->
        @if (waiting().length > 1) {
          <nav class="sf-chips" aria-label="Sessões aguardando">
            @for (s of waiting(); track s.id) {
              <button
                type="button"
                class="sf-chip"
                [class.is-active]="s.id === selectedId()"
                (click)="select(s.id)"
              >
                <span
                  class="sf-chip-dot"
                  [style.background]="agent(s).color"
                ></span>
                {{ displayName(s) }}
              </button>
            }
          </nav>
        }

        @let s = selected();
        @if (s) {
          <article class="sf-card">
            <header class="sf-card-head">
              <span
                class="sf-avatar"
                [style.background]="agent(s).color + '29'"
                [style.color]="agent(s).color"
                >{{ agent(s).short }}</span
              >
              <span class="sf-card-meta">
                <span class="sf-name">{{ displayName(s) }}</span>
                <span class="sf-status">● Aguardando feedback{{ waitedFor(s) }}</span>
              </span>
            </header>

            <div class="sf-ask">
              <span class="sf-ask-prompt">{{ agent(s).cmd || 'agent' }}&gt;</span>
              <span class="sf-ask-text" [innerHTML]="askHtml()"></span>
            </div>
          </article>

          <p class="sf-section">Respostas rápidas</p>
          <div class="sf-quick" role="group" aria-label="Respostas rápidas">
            @for (q of quickReplies; track q.label) {
              <button
                type="button"
                class="sf-quick-chip"
                [class.is-approve]="q.tone === 'approve'"
                [class.is-reject]="q.tone === 'reject'"
                (click)="fill(q.label)"
              >
                {{ q.label }}
              </button>
            }
          </div>

          <p class="sf-section">Sua resposta</p>
          <textarea
            class="sf-textarea"
            placeholder="Escreva uma resposta para o agente…"
            [(ngModel)]="reply"
            [disabled]="sending()"
          ></textarea>

          @if (sendError()) {
            <p class="sf-msg sf-msg--error">Falha ao enviar. Tente de novo.</p>
          }

          <div class="sf-actions">
            <div class="sf-rec-wrap" [class.is-recording]="recording()">
              <sf-audio-recorder
                [sessionId]="s.id"
                (transcribing)="onTranscribing($event)"
                (uploaded)="onAudioUploaded()"
              ></sf-audio-recorder>
            </div>

            <span class="sf-rec-hint">{{ hint() }}</span>

            <button
              type="button"
              class="sf-send"
              [disabled]="!canSend()"
              (click)="send()"
            >
              <span>{{ sending() ? 'Enviando…' : 'Enviar' }}</span>
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="#04140f"
                stroke-width="2.4"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
              >
                <path d="M22 2 11 13" />
                <path d="M22 2 15 22l-4-9-9-4 20-7z" />
              </svg>
            </button>
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
        color: #e7eae9;
      }
      .sf-responder {
        padding: 16px 16px 96px;
        max-width: 720px;
        margin: 0 auto;
      }
      .sf-head {
        padding: 4px 0 14px;
      }
      .sf-title {
        margin: 0;
        font-size: 28px;
        font-weight: 700;
        letter-spacing: -0.6px;
        color: #f4f5f7;
      }
      .sf-sub {
        margin: 4px 0 0;
        font-size: 14px;
        color: #8a90a0;
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

      .sf-chips {
        display: flex;
        gap: 8px;
        overflow-x: auto;
        padding: 4px 0 14px;
        scrollbar-width: none;
      }
      .sf-chips::-webkit-scrollbar {
        display: none;
      }
      .sf-chip {
        flex: 0 0 auto;
        display: inline-flex;
        align-items: center;
        gap: 7px;
        appearance: none;
        border: 1px solid #283230;
        background: #181c1b;
        color: #9aa0ae;
        font: inherit;
        font-size: 13px;
        font-weight: 600;
        padding: 7px 14px;
        border-radius: 999px;
        cursor: pointer;
        transition: background 0.15s, color 0.15s, border-color 0.15s;
      }
      .sf-chip.is-active {
        background: #00e4b4;
        border-color: #00e4b4;
        color: #06231d;
      }
      .sf-chip-dot {
        width: 7px;
        height: 7px;
        border-radius: 50%;
        flex: 0 0 auto;
      }

      .sf-card {
        border: 1px solid #4a3c1c;
        background: #181c1b;
        border-radius: 18px;
        padding: 16px;
      }
      .sf-card-head {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 13px;
      }
      .sf-avatar {
        flex: 0 0 auto;
        width: 34px;
        height: 34px;
        border-radius: 10px;
        display: grid;
        place-items: center;
        font-size: 11px;
        font-weight: 800;
      }
      .sf-card-meta {
        display: flex;
        flex-direction: column;
        gap: 1px;
        min-width: 0;
      }
      .sf-name {
        font-size: 15.5px;
        font-weight: 600;
        color: #f4f5f7;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .sf-status {
        font-size: 12.5px;
        color: #fbbf24;
      }

      .sf-ask {
        background: #0e1113;
        border: 1px solid #283230;
        border-radius: 12px;
        padding: 13px 14px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 12.5px;
        line-height: 1.6;
        color: #cdd2da;
      }
      .sf-ask-prompt {
        color: #7a8090;
      }
      .sf-ask-text {
        white-space: pre-wrap;
      }
      .sf-ask-text ::ng-deep .sf-hl {
        color: #00e4b4;
      }

      .sf-section {
        margin: 24px 0 10px;
        font-size: 13px;
        font-weight: 700;
        color: #8a90a0;
      }

      .sf-quick {
        display: flex;
        flex-wrap: wrap;
        gap: 9px;
      }
      .sf-quick-chip {
        appearance: none;
        border: 1px solid #283230;
        background: #181c1b;
        color: #f4f5f7;
        font: inherit;
        font-size: 13.5px;
        font-weight: 600;
        padding: 9px 15px;
        border-radius: 11px;
        cursor: pointer;
        transition: background 0.15s, border-color 0.15s, color 0.15s;
      }
      .sf-quick-chip.is-approve {
        color: #34d399;
        background: rgba(52, 211, 153, 0.14);
        border-color: rgba(52, 211, 153, 0.28);
      }
      .sf-quick-chip.is-reject {
        color: #f87171;
        background: rgba(248, 113, 113, 0.13);
        border-color: rgba(248, 113, 113, 0.28);
      }
      .sf-quick-chip:hover {
        filter: brightness(1.12);
      }

      .sf-textarea {
        display: block;
        width: 100%;
        box-sizing: border-box;
        resize: none;
        min-height: 96px;
        border: 1px solid #283230;
        background: #181c1b;
        color: #f4f5f7;
        font-family: 'Inter', sans-serif;
        font-size: 14.5px;
        line-height: 1.5;
        padding: 14px;
        border-radius: 14px;
        transition: border-color 0.15s;
      }
      .sf-textarea::placeholder {
        color: #6b7280;
      }
      .sf-textarea:focus {
        outline: none;
        border-color: #00e4b4;
      }
      .sf-textarea:disabled {
        opacity: 0.6;
      }

      .sf-actions {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-top: 14px;
      }

      /* Recorder: the shared 64px green button is re-skinned to the 54px
         circular dark/amber mockup spec without touching the shared file. */
      .sf-rec-wrap {
        flex: none;
      }
      .sf-rec-wrap ::ng-deep .sf-rec-btn {
        width: 54px;
        height: 54px;
        background: #181c1b;
        border: 1px solid #283230;
        color: #c9cdd6;
      }
      .sf-rec-wrap.is-recording ::ng-deep .sf-rec-btn,
      .sf-rec-wrap ::ng-deep .sf-rec-btn.is-recording {
        background: rgba(248, 113, 113, 0.16);
        border-color: rgba(248, 113, 113, 0.5);
        color: #f87171;
      }
      .sf-rec-hint {
        flex: 1;
        min-width: 0;
        font-size: 13.5px;
        color: #7a8090;
      }

      .sf-send {
        flex: none;
        display: flex;
        align-items: center;
        gap: 8px;
        appearance: none;
        border: none;
        height: 54px;
        padding: 0 22px;
        border-radius: 14px;
        background: linear-gradient(150deg, #00e4b4, #00a482);
        color: #04140f;
        font: inherit;
        font-size: 15px;
        font-weight: 700;
        cursor: pointer;
        box-shadow: 0 6px 16px -6px rgba(0, 200, 160, 0.6);
        transition: opacity 0.15s, transform 0.1s;
      }
      .sf-send:active {
        transform: scale(0.99);
      }
      .sf-send:disabled {
        opacity: 0.45;
        cursor: not-allowed;
      }
    `,
  ],
})
export class ResponderComponent {
  private readonly api = inject(ApiService);
  private readonly sse = inject(SseService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly sanitizer = inject(DomSanitizer);
  /** Singleton recorder service — read live `recording()` for the hint/skin. */
  private readonly recorder = inject(AudioRecorderService);

  protected readonly quickReplies = QUICK_REPLIES;

  /** True while the mic is capturing or an audio upload is in flight. */
  protected readonly recording = computed<boolean>(
    () => this.recorder.recording() || this.uploading(),
  );

  /** True only during the upload/transcription window. */
  private readonly uploading = signal<boolean>(false);

  /** Action-bar hint next to the mic ("Toque para gravar um áudio"). */
  protected readonly hint = computed<string>(() => {
    if (this.uploading()) {
      return 'Enviando o áudio…';
    }
    return this.recorder.recording()
      ? 'Gravando… toque para parar'
      : 'Toque para gravar um áudio';
  });

  protected readonly loading = signal<boolean>(true);
  protected readonly error = signal<boolean>(false);
  protected readonly sending = signal<boolean>(false);
  protected readonly sendError = signal<boolean>(false);

  /** Free-text answer bound to the textarea (two-way via ngModel). */
  protected readonly reply = signal<string>('');

  /** Sessions currently in `waiting_input` (kept live by SSE). */
  protected readonly waiting = signal<Session[]>([]);

  /** Id of the session being answered; null = pick the first available. */
  private readonly explicitId = signal<string | null>(null);

  /** The last `ask` line fetched for the selected session. */
  private readonly askLine = signal<string>('');

  /** Effective selected id: explicit choice, else the first waiting session. */
  protected readonly selectedId = computed<string | null>(() => {
    const chosen = this.explicitId();
    const list = this.waiting();
    if (chosen && list.some((s) => s.id === chosen)) {
      return chosen;
    }
    return list[0]?.id ?? null;
  });

  protected readonly selected = computed<Session | null>(() => {
    const id = this.selectedId();
    return this.waiting().find((s) => s.id === id) ?? null;
  });

  /** Question to show in the card (agent's `ask` line or a generic fallback). */
  protected readonly ask = computed<string>(
    () => this.askLine() || FALLBACK_ASK,
  );

  /**
   * The question rendered for the mono block: HTML-escaped, then file paths /
   * code tokens wrapped in a mint highlight span (mockup `auth.service.ts`).
   */
  protected readonly askHtml = computed<SafeHtml>(() => {
    const escaped = this.escapeHtml(this.ask());
    // Highlight code-ish tokens: foo.bar, file.ext, backtick spans.
    const highlighted = escaped
      .replace(/`([^`]+)`/g, '<span class="sf-hl">$1</span>')
      .replace(
        /(?<![\w.])([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+)/g,
        '<span class="sf-hl">$1</span>',
      );
    return this.sanitizer.bypassSecurityTrustHtml(highlighted);
  });

  protected readonly canSend = computed<boolean>(
    () => !!this.selected() && this.reply().trim().length > 0 && !this.sending(),
  );

  constructor() {
    this.load();

    this.sse.connect();
    this.destroyRef.onDestroy(() => this.sse.disconnect());

    // Any live frame that touches a session re-syncs the waiting list, so the
    // screen reflects sessions entering/leaving `waiting_input` in real time.
    effect(() => {
      const last = this.sse.lastEvent();
      if (last && 'session_id' in last && last.session_id) {
        this.refresh();
      }
    });

    // Whenever the selected session changes, fetch its latest agent question.
    effect(() => {
      const id = this.selectedId();
      if (id) {
        this.loadAsk(id);
      } else {
        this.askLine.set('');
      }
    });
  }

  protected select(id: string): void {
    this.explicitId.set(id);
    this.reply.set('');
    this.sendError.set(false);
  }

  protected fill(text: string): void {
    this.reply.set(text);
  }

  protected send(): void {
    const session = this.selected();
    const text = this.reply().trim();
    if (!session || !text || this.sending()) {
      return;
    }
    this.sending.set(true);
    this.sendError.set(false);
    this.api
      .sendInput(session.id, text)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.reply.set('');
          this.sending.set(false);
          // The session typically leaves `waiting_input` after answering.
          this.refresh();
        },
        error: () => {
          this.sending.set(false);
          this.sendError.set(true);
        },
      });
  }

  /** The recorder uploads the audio; backend transcribes & injects the input. */
  protected onAudioUploaded(): void {
    this.refresh();
  }

  /** Recorder reports start/stop of an upload; we use it to flip the hint. */
  protected onTranscribing(active: boolean): void {
    this.uploading.set(active);
  }

  /** Escapes the agent question before it is highlighted & rendered as HTML. */
  private escapeHtml(s: string): string {
    return s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  /**
   * Relative " · há X" suffix for the status line, derived from a timestamp
   * field if the session payload carries one (e.g. `updated_at`). Returns ''
   * when no usable timestamp is present.
   */
  protected waitedFor(s: Session): string {
    const raw =
      (s['updated_at'] as string | undefined) ??
      (s['last_activity_at'] as string | undefined) ??
      (s['created_at'] as string | undefined);
    if (!raw) {
      return '';
    }
    const then = new Date(raw).getTime();
    if (Number.isNaN(then)) {
      return '';
    }
    const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
    if (mins < 1) {
      return ' · há instantes';
    }
    if (mins < 60) {
      return ` · há ${mins} min`;
    }
    const hours = Math.round(mins / 60);
    if (hours < 24) {
      return ` · há ${hours} h`;
    }
    return ` · há ${Math.round(hours / 24)} d`;
  }

  protected agent(s: Session) {
    return agentMeta(s.agent_type);
  }

  protected displayName(s: Session): string {
    return s.display_name || s.tmux_name || s.id;
  }

  private load(): void {
    this.loading.set(true);
    this.error.set(false);
    this.api
      .listSessions('waiting_input')
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (list) => {
          this.waiting.set(list ?? []);
          this.loading.set(false);
        },
        error: () => {
          this.loading.set(false);
          this.error.set(true);
        },
      });
  }

  /** Quietly refetch the waiting list to track live status changes. */
  private refresh(): void {
    this.api
      .listSessions('waiting_input')
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (list) => this.waiting.set(list ?? []),
        error: () => {
          /* keep last known state on transient errors */
        },
      });
  }

  /** Fetches the output and keeps the text of the last `ask` line. */
  private loadAsk(id: string): void {
    this.api
      .getOutput(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (lines) => this.askLine.set(this.lastAsk(lines)),
        error: () => this.askLine.set(''),
      });
  }

  /** Returns the text of the last `ask` line, or '' if none is present. */
  private lastAsk(lines: OutputLine[]): string {
    for (let i = lines.length - 1; i >= 0; i--) {
      if (lines[i].line_type === 'ask') {
        return lines[i].text.trim();
      }
    }
    return '';
  }
}
