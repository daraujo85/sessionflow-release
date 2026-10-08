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

/** A quick-reply chip with its mockup color treatment + inline icon. */
interface QuickReply {
  /** Visible chip caption. */
  readonly label: string;
  /** Text injected into the reply box when the chip is tapped. */
  readonly text: string;
  /** 'approve' = green tint, 'reject' = red tint, 'neutral' = dark/neutral. */
  readonly kind: 'approve' | 'reject' | 'neutral';
  /** Icon key resolved to an inline SVG in the template (see iconPaths). */
  readonly icon: 'check' | 'x' | 'refresh' | 'play' | 'test' | 'help' | 'dot';
  /** Full (untruncated) caption for the chip's title/aria-label, when relevant. */
  readonly full?: string;
}

/** Generic pre-canned answers used when the question type can't be inferred. */
const FALLBACK_REPLIES: readonly QuickReply[] = [
  { label: 'Aprovar', text: 'Aprovado, pode seguir.', kind: 'approve', icon: 'check' },
  { label: 'Rejeitar', text: 'Não, não faça isso.', kind: 'reject', icon: 'x' },
  { label: 'Continuar', text: 'Pode continuar.', kind: 'neutral', icon: 'play' },
  { label: 'Refazer', text: 'Refaz isso, por favor.', kind: 'neutral', icon: 'refresh' },
  {
    label: 'Rodar os testes',
    text: 'Rode os testes e me mostre o resultado.',
    kind: 'neutral',
    icon: 'test',
  },
];

/** Chips offered for a yes/no (decision) question. */
const YESNO_REPLIES: readonly QuickReply[] = [
  { label: 'Sim', text: 'Sim', kind: 'approve', icon: 'check' },
  { label: 'Não', text: 'Não', kind: 'reject', icon: 'x' },
  {
    label: 'Explica melhor',
    text: 'Pode explicar melhor antes de eu decidir?',
    kind: 'neutral',
    icon: 'help',
  },
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
                <span class="sf-status">
                  <span class="sf-status-dot" aria-hidden="true"></span>
                  Aguardando feedback{{ waitedFor(s) }}
                </span>
              </span>
            </header>

            <div class="sf-ask">
              <span class="sf-ask-label">O agente pergunta</span>
              <p class="sf-ask-text" [innerHTML]="askHtml()"></p>
            </div>
          </article>

          <p class="sf-section">Respostas rápidas</p>
          <div class="sf-quick" role="group" aria-label="Respostas rápidas">
            @for (q of quickReplies(); track q.text + '|' + q.label) {
              <button
                type="button"
                class="sf-quick-chip"
                [class.is-approve]="q.kind === 'approve'"
                [class.is-reject]="q.kind === 'reject'"
                [title]="q.full || q.label"
                [attr.aria-label]="q.full || q.label"
                (click)="fill(q.text)"
              >
                <svg
                  class="sf-quick-ico"
                  width="15"
                  height="15"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2.2"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  aria-hidden="true"
                  [innerHTML]="iconHtml(q.icon)"
                ></svg>
                {{ q.label }}
              </button>
            }
          </div>

          <p class="sf-section">Sua resposta</p>
          <div class="sf-reply">
            <textarea
              class="sf-textarea"
              placeholder="Escreva uma resposta para o agente…"
              [(ngModel)]="reply"
              [disabled]="sending()"
            ></textarea>

            @if (sendError()) {
              <p class="sf-msg sf-msg--error sf-send-err">
                Falha ao enviar. Tente de novo.
              </p>
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
                aria-label="Enviar resposta"
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
        border: 1px solid #283230;
        background: #15191a;
        border-radius: 18px;
        padding: 16px;
      }
      .sf-card-head {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 14px;
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
        display: inline-flex;
        align-items: center;
        gap: 6px;
        font-size: 12.5px;
        color: #fbbf24;
      }
      .sf-status-dot {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: #fbbf24;
        flex: 0 0 auto;
      }

      /* Question rendered as readable prose, quote-style with mint accent. */
      .sf-ask {
        position: relative;
        background: #0e1113;
        border: 1px solid #283230;
        border-left: 3px solid #34d399;
        border-radius: 12px;
        padding: 13px 15px;
      }
      .sf-ask-label {
        display: block;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.5px;
        text-transform: uppercase;
        color: #7a8090;
        margin-bottom: 6px;
      }
      .sf-ask-text {
        margin: 0;
        font-family: 'Inter', sans-serif;
        font-size: 15px;
        line-height: 1.5;
        color: #f4f5f7;
        white-space: pre-wrap;
        word-break: break-word;
      }
      .sf-ask-text ::ng-deep .sf-hl {
        color: #34d399;
        font-family: 'JetBrains Mono', ui-monospace, monospace;
        font-size: 0.92em;
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
        gap: 8px;
      }
      .sf-quick-chip {
        flex: 0 0 auto;
        display: inline-flex;
        align-items: center;
        gap: 7px;
        appearance: none;
        height: 38px;
        border: 1px solid #283230;
        background: #1e2422;
        color: #f4f5f7;
        font: inherit;
        font-size: 13.5px;
        font-weight: 600;
        padding: 0 14px;
        border-radius: 11px;
        cursor: pointer;
        -webkit-tap-highlight-color: transparent;
        transition: background 0.15s, border-color 0.15s, color 0.15s,
          filter 0.15s;
      }
      .sf-quick-ico {
        flex: 0 0 auto;
        opacity: 0.85;
      }
      .sf-quick-chip.is-approve {
        color: #34d399;
        background: rgba(52, 211, 153, 0.12);
        border-color: rgba(52, 211, 153, 0.28);
      }
      .sf-quick-chip.is-reject {
        color: #f87171;
        background: rgba(248, 113, 113, 0.11);
        border-color: rgba(248, 113, 113, 0.28);
      }
      .sf-quick-chip:hover {
        filter: brightness(1.14);
      }
      .sf-quick-chip:active {
        transform: scale(0.97);
      }

      .sf-reply {
        border: 1px solid #283230;
        background: #181c1b;
        border-radius: 16px;
        padding: 12px;
      }
      .sf-send-err {
        padding: 8px 2px 0;
        font-size: 13px;
      }

      .sf-textarea {
        display: block;
        width: 100%;
        box-sizing: border-box;
        resize: none;
        min-height: 92px;
        border: none;
        background: transparent;
        color: #f4f5f7;
        font-family: 'Inter', sans-serif;
        font-size: 14.5px;
        line-height: 1.5;
        padding: 4px 4px 0;
      }
      .sf-textarea::placeholder {
        color: #6b7280;
      }
      .sf-textarea:focus {
        outline: none;
      }
      .sf-reply:focus-within {
        border-color: #34d399;
      }
      .sf-textarea:disabled {
        opacity: 0.6;
      }

      .sf-actions {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-top: 10px;
        padding-top: 10px;
        border-top: 1px solid #20262a;
      }

      /* Recorder: the shared 64px green button is re-skinned to the 54px
         circular dark/amber mockup spec without touching the shared file. */
      .sf-rec-wrap {
        flex: none;
      }
      .sf-rec-wrap ::ng-deep .sf-rec-btn {
        width: 48px;
        height: 48px;
        border-radius: 13px;
        background: #1e2422;
        border: 1px solid #283230;
        color: #c9cdd6;
      }
      .sf-rec-wrap ::ng-deep .sf-rec-error {
        display: none;
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
        height: 48px;
        padding: 0 20px;
        border-radius: 13px;
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

  /**
   * Quick-reply chips derived from the agent's cleaned question (`ask()`):
   * yes/no decisions get Sim/Não/Explica; numbered or lettered pickers get one
   * chip per detected option; anything else falls back to the generic set.
   */
  protected readonly quickReplies = computed<readonly QuickReply[]>(() =>
    this.deriveQuickReplies(this.ask()),
  );

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

  /**
   * Question to show in the card: the agent's `ask` line CLEANED of terminal
   * cruft (prompt prefix, status glyphs, box-drawing chars) so it reads as
   * plain prose; falls back to a generic line when there's no question.
   */
  protected readonly ask = computed<string>(
    () => this.cleanAskText(this.askLine()) || FALLBACK_ASK,
  );

  /**
   * The cleaned question rendered as readable prose: HTML-escaped, then file
   * paths / code tokens wrapped in a mint highlight span (e.g. `auth.service.ts`).
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

  /**
   * Cleans a raw agent `ask` line into readable prose for display:
   *  - strips a leading agent prompt prefix (claude> / codex> / gemini> / ❯ / >)
   *  - strips leading status glyphs / bullets (● ⏺ ◯ ✻ ◎ ✽ * -)
   *  - removes box-drawing chars (U+2500–U+257F) and assorted TUI symbols
   *  - collapses runs of whitespace and trims.
   */
  private cleanAskText(raw: string): string {
    if (!raw) {
      return '';
    }
    let s = raw;
    // Drop box-drawing & block element glyphs, plus common TUI status symbols.
    s = s.replace(/[─-▟]/g, ' ');
    s = s.replace(
      /[●⏺◯○◌◎✻✽✦✧✺✶∗·•▪▸▶►➤➜→]/g,
      ' ',
    );
    // Strip a leading agent prompt prefix (with optional surrounding glyphs).
    s = s.replace(/^\s*(?:claude|codex|gemini|agent)\s*>+\s*/i, '');
    s = s.replace(/^\s*[❯>＞]+\s*/, '');
    // Strip leading bullet/asterisk/dash markers left at the start of the line.
    s = s.replace(/^\s*[*\-]+\s+/, '');
    // Collapse whitespace (incl. newlines) and trim.
    s = s.replace(/\s+/g, ' ').trim();
    return s;
  }

  /**
   * Derives contextual quick-reply chips from the cleaned question text:
   *  1. yes/no decision question  → Sim / Não / Explica melhor
   *  2. numbered/lettered picker   → one chip per detected option (cap ~6)
   *  3. otherwise                  → generic fallback set
   */
  private deriveQuickReplies(question: string): readonly QuickReply[] {
    const text = (question ?? '').trim();
    if (!text) {
      return FALLBACK_REPLIES;
    }

    // --- (1) Numbered / multiple-choice picker -----------------------------
    // Detect option markers at a line/segment start: 1) 2) 1. 2. a) [1] …
    const options = this.detectOptions(text);
    if (options.length >= 2) {
      return options.slice(0, 6).map((opt) => {
        const full = opt.label || opt.key;
        return {
          // Show the human option text; fall back to the marker when absent.
          label: this.truncate(full, 48),
          // Fill the marker (what TUI pickers expect); fall back to the label.
          text: opt.key || opt.label,
          kind: 'neutral' as const,
          icon: 'dot' as const,
          full,
        };
      });
    }

    // --- (2) Either / or (binary alternative) ------------------------------
    // "Quer que eu X, ou prefere que eu Y?" → two chips, one per alternative.
    const eitherOr = this.detectEitherOr(text);
    if (eitherOr) {
      return eitherOr;
    }

    // --- (3) Yes / No (decision) question ----------------------------------
    if (this.isYesNoQuestion(text)) {
      return YESNO_REPLIES;
    }

    // --- (4) Fallback ------------------------------------------------------
    return FALLBACK_REPLIES;
  }

  /**
   * Detects a clear "… ou …" binary alternative inside a real question (e.g.
   * "Quer que eu ajuste o X, ou prefere que eu espere o Y?") and emits two
   * `neutral` chips — one per alternative. Conservative on purpose: the question
   * must end in "?", split on a single " ou " into two reasonably balanced
   * clauses, and yield two non-trivial alternatives — so normal prose carrying
   * an incidental "ou" doesn't false-trigger. Returns null when no clean split
   * is found.
   */
  private detectEitherOr(text: string): readonly QuickReply[] | null {
    const t = text.trim();
    if (!t.endsWith('?')) {
      return null;
    }

    // Work on the question body (strip the trailing "?") and split on " ou ".
    const body = t.replace(/\?+\s*$/, '').trim();
    const parts = body.split(/\s+ou\s+/i);
    if (parts.length !== 2) {
      return null; // need exactly one "ou" dividing two clauses
    }

    let [left, right] = parts.map((p) => p.replace(/[,;:]\s*$/, '').trim());

    // The left clause usually carries a preamble before the real alternative
    // ("Por ora… só me alinhar. Quer que eu X, ou …"). Keep only the LAST
    // sentence, then its last comma-separated segment, as the first alternative.
    const leftSentences = left.split(/(?<=[.!?])\s+/).filter(Boolean);
    if (leftSentences.length > 1) {
      left = leftSentences[leftSentences.length - 1].trim();
    }
    const leftSegs = left.split(',').map((s) => s.trim()).filter(Boolean);
    if (leftSegs.length > 1) {
      left = leftSegs[leftSegs.length - 1];
    }

    const aText = this.stripChoiceLead(left);
    const bText = this.stripChoiceLead(right);

    // Require two reasonably balanced, non-trivial clauses.
    if (aText.length < 4 || bText.length < 4) {
      return null;
    }
    const ratio = aText.length / bText.length;
    if (ratio < 0.2 || ratio > 5) {
      return null;
    }

    return [
      {
        label: this.truncate(aText, 40),
        text: this.choiceReply(aText),
        kind: 'neutral' as const,
        icon: 'dot' as const,
        full: aText,
      },
      {
        label: this.truncate(bText, 40),
        text: this.choiceReply(bText),
        kind: 'neutral' as const,
        icon: 'dot' as const,
        full: bText,
      },
    ];
  }

  /**
   * Shortens one alternative into a concise label by stripping the choice
   * lead-in ("quer que eu" / "prefere que eu" / "que eu" / "você") and any
   * leftover question particles, leaving the bare action phrase.
   */
  private stripChoiceLead(clause: string): string {
    let s = clause.trim();
    s = s.replace(
      /^(?:e\s+)?(?:voc[êe]\s+)?(?:quer que eu|prefere que eu|gostaria que eu|prefere|quer|que eu|eu)\s+/i,
      '',
    );
    return s.replace(/\s+/g, ' ').trim();
  }

  /** Builds the reply text sent on tap for a chosen alternative. */
  private choiceReply(alternative: string): string {
    return alternative.trim();
  }

  /**
   * Parses a numbered/lettered picker out of the cleaned question, returning one
   * `{ key, label }` per detected option:
   *  - `key`   = the marker (e.g. "1" / "a") — what a TUI picker usually expects.
   *  - `label` = the human option text on the same line (trimmed), or '' if the
   *              marker carries no text (a bare list of numbers/letters).
   *
   * Each marker must sit at the start of a line/segment (start-of-string or after
   * whitespace) and be followed by an option separator — `1)` `1.` `1 -` `[1]`
   * `a)` `(a)` — so prose like "Node 18.x" or a mid-sentence "step 2" isn't
   * mistaken for a list item. Works on multiline output and on a single-line
   * option list (e.g. once `ask()` has collapsed newlines into spaces): markers
   * are located by index and each label runs up to the next marker.
   */
  private detectOptions(text: string): { key: string; label: string }[] {
    // Anchored at start-of-string or after whitespace; supports:
    //   [1]  (a)  1)  1.  1 -  a)  a.  a -
    const re =
      /(?:^|\s)(?:\[\s*([0-9]+|[a-zA-Z])\s*\]|\(\s*([0-9]+|[a-zA-Z])\s*\)|([0-9]+|[a-zA-Z])\s*[).]|([0-9]+|[a-zA-Z])\s+-)\s+/g;

    type Hit = { key: string; markerStart: number; labelStart: number };
    const hits: Hit[] = [];
    let m: RegExpExecArray | null;
    while ((m = re.exec(text)) !== null) {
      const key = (m[1] ?? m[2] ?? m[3] ?? m[4] ?? '').trim();
      if (!key) {
        continue;
      }
      // The match may absorb a leading whitespace char ((?:^|\s)); the marker
      // itself starts at the first non-space of the match.
      const lead = m[0].match(/^\s*/)?.[0].length ?? 0;
      hits.push({
        key,
        markerStart: m.index + lead,
        labelStart: re.lastIndex, // right after "marker + trailing space"
      });
    }

    const out: { key: string; label: string }[] = [];
    const seen = new Set<string>();
    for (let i = 0; i < hits.length; i++) {
      const keyLower = hits[i].key.toLowerCase();
      if (seen.has(keyLower)) {
        continue;
      }
      seen.add(keyLower);
      // Label runs from just after this marker up to where the NEXT marker begins.
      const end = i + 1 < hits.length ? hits[i + 1].markerStart : text.length;
      const label = text
        .slice(hits[i].labelStart, end)
        .replace(/\s+/g, ' ')
        .trim();
      out.push({ key: hits[i].key, label });
    }
    return out;
  }

  /** Truncates `s` to `max` chars, appending an ellipsis when shortened. */
  private truncate(s: string, max: number): string {
    const t = (s ?? '').trim();
    return t.length > max ? `${t.slice(0, max - 1).trimEnd()}…` : t;
  }

  /**
   * Heuristic: is this a yes/no decision question? Triggers on explicit y/n
   * hints, common decision verbs (PT/EN), an "ok?" prompt, or a short question
   * that simply ends in "?".
   */
  private isYesNoQuestion(text: string): boolean {
    const t = text.toLowerCase();
    const hints = [
      '(y/n)',
      '(s/n)',
      'y/n',
      's/n',
      'sim/não',
      'sim ou não',
    ];
    if (hints.some((h) => t.includes(h))) {
      return true;
    }
    const verbs = [
      'deseja',
      'confirma',
      'posso prosseguir',
      'posso',
      'quer que eu',
      'should i',
      'do you want',
      'shall i',
      'ok?',
    ];
    if (verbs.some((v) => t.includes(v))) {
      return true;
    }
    // A short question ending in "?" is very likely a yes/no decision.
    if (t.endsWith('?') && text.length <= 120) {
      return true;
    }
    return false;
  }

  /** Returns the inner SVG markup for a quick-reply icon key. */
  protected iconHtml(key: QuickReply['icon']): SafeHtml {
    const paths: Record<QuickReply['icon'], string> = {
      check: '<path d="M20 6 9 17l-5-5" />',
      x: '<path d="M18 6 6 18M6 6l12 12" />',
      refresh:
        '<path d="M3 12a9 9 0 0 1 15-6.7L21 8" /><path d="M21 3v5h-5" /><path d="M21 12a9 9 0 0 1-15 6.7L3 16" /><path d="M3 21v-5h5" />',
      play: '<path d="M6 4l14 8-14 8V4z" />',
      test: '<path d="M9 3h6M10 3v6.5L5 18a2 2 0 0 0 1.8 3h10.4A2 2 0 0 0 19 18l-5-8.5V3" />',
      help: '<circle cx="12" cy="12" r="9" /><path d="M9.5 9a2.5 2.5 0 1 1 3.4 2.3c-.8.4-1.4 1-1.4 1.9v.3" /><path d="M12 17h.01" />',
      dot: '<circle cx="12" cy="12" r="3.2" />',
    };
    return this.sanitizer.bypassSecurityTrustHtml(paths[key]);
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

  /**
   * Loads the agent's REAL question from the live terminal SCREEN.
   *
   * Interactive prompts are drawn on the tmux `capture-pane` mirror (the screen),
   * NOT the line-by-line output stream — so we pull `getScreen(id)` and extract
   * the prose question from it. The output stream's `ask` line is only used as a
   * fallback when the screen mirror is empty/unavailable.
   */
  private loadAsk(id: string): void {
    this.api
      .getScreen(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: ({ text }) => {
          const fromScreen = this.extractQuestion(text ?? '');
          if (fromScreen) {
            this.askLine.set(fromScreen);
          } else {
            this.loadAskFromOutput(id);
          }
        },
        error: () => this.loadAskFromOutput(id),
      });
  }

  /** Fallback source: the line stream's last `ask` line (pre-screen behavior). */
  private loadAskFromOutput(id: string): void {
    this.api
      .getOutput(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (lines) => this.askLine.set(this.lastAsk(lines)),
        error: () => this.askLine.set(''),
      });
  }

  /**
   * Extracts the agent's real question out of a raw tmux screen capture.
   *
   * The screen interleaves the agent's prose with TUI chrome: box-drawing
   * separators, the `❯` prompt line (which may hold a typed draft), the status
   * bar (JARVIS / bypass permissions / ⎇ / Opus·Sonnet / "for agents"), spinner
   * / activity lines, and tip lines. We drop all chrome, then take the LAST
   * contiguous paragraph of prose — preferring one that ends with a real `?` —
   * join its wrapped lines, and run it through `cleanAskText()`.
   *
   * Returns '' when no prose can be salvaged (caller then falls back).
   */
  private extractQuestion(screen: string): string {
    if (!screen) {
      return '';
    }
    const rawLines = screen.replace(/\r/g, '').split('\n');

    // Keep only the agent's prose lines, dropping every chrome line.
    const prose: string[] = [];
    for (const line of rawLines) {
      const t = line.trim();
      if (!t) {
        prose.push(''); // preserve paragraph breaks
        continue;
      }
      if (this.isChromeLine(t)) {
        continue;
      }
      prose.push(t);
    }

    // Group consecutive non-empty lines into paragraphs.
    const paragraphs: string[] = [];
    let cur: string[] = [];
    for (const line of prose) {
      if (line === '') {
        if (cur.length) {
          paragraphs.push(cur.join(' '));
          cur = [];
        }
      } else {
        cur.push(line);
      }
    }
    if (cur.length) {
      paragraphs.push(cur.join(' '));
    }
    if (!paragraphs.length) {
      return '';
    }

    // Prefer the LAST paragraph that actually contains a question mark.
    let chosen = '';
    for (let i = paragraphs.length - 1; i >= 0; i--) {
      if (paragraphs[i].includes('?')) {
        chosen = paragraphs[i];
        break;
      }
    }
    // Otherwise fall back to the last non-empty prose paragraph.
    if (!chosen) {
      chosen = paragraphs[paragraphs.length - 1];
    }

    return this.cleanAskText(chosen);
  }

  /**
   * True when a (trimmed, non-empty) screen line is TUI chrome rather than the
   * agent's prose: box-drawing separators, the prompt/draft line, the status
   * bar, spinner/activity lines, or a tip line.
   */
  private isChromeLine(t: string): boolean {
    // Box-drawing separator: mostly U+2500–U+257F glyphs (and spaces).
    const boxChars = (t.match(/[─-╿]/g) ?? []).length;
    if (boxChars > 0 && boxChars >= t.replace(/\s/g, '').length) {
      return true;
    }
    // ASCII separator line (------ / ====== / ______): ≥6 não-espaços e ≥80%
    // deles são separadores (não pega frases normais com hífens).
    const nonspace = t.replace(/\s/g, '').length;
    const seps = (t.match(/[-=_]/g) ?? []).length;
    if (nonspace >= 6 && seps >= nonspace * 0.8) {
      return true;
    }
    // Prompt line (and any typed draft after it) — ignore for the question.
    if (/^[❯>＞]/.test(t)) {
      return true;
    }
    // Status bar.
    if (
      /JARVIS|bypass permissions|for agents|⎇|\bOpus\b|\bSonnet\b/.test(t)
    ) {
      return true;
    }
    // Spinner / activity lines.
    if (/^[✻✽◯○◎⏺·•]/.test(t)) {
      return true;
    }
    if (/…\s*for\s+\d+\s*s\b/i.test(t) || /\btokens?\b/i.test(t)) {
      return true;
    }
    // Tip lines.
    if (t.includes('⎿') || /^Tip:/i.test(t)) {
      return true;
    }
    return false;
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
