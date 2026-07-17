import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NgIf } from '@angular/common';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';

import { ApiService } from '../../core/api.service';
import { SseService } from '../../core/sse.service';
import { Session } from '../../core/models';
import { STATUS_META, agentMeta } from '../../shared/status-color';
import { ansiToHtml, trimBlankEdges } from '../../shared/ansi-html';

/**
 * Painel LEVE de sessão (terminal ao vivo + composer básico), pensado pra ser
 * instanciado duas vezes lado a lado no modo "dividir tela" do Detalhe. Não
 * repete o conjunto completo de recursos do DetalheComponent (tarefas,
 * métricas, anexos, câmera, share, rename) — só o essencial pra ACOMPANHAR e
 * MANDAR mensagem/tecla, que é o caso de uso de comparar/conversar entre duas
 * sessões (ver `tools/sf send`).
 */
@Component({
  selector: 'app-session-panel',
  standalone: true,
  imports: [FormsModule, NgIf],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="panel">
      <header class="panel-header">
        <span class="dot" [style.background]="statusColor()"></span>
        <span class="name" [title]="session()?.tmux_name ?? sessionId()">{{
          session()?.display_name || session()?.tmux_name || sessionId()
        }}</span>
        <span class="agent" *ngIf="session() as s">{{ agentLabel(s.agent_type) }}</span>
        <button
          type="button"
          class="close-btn"
          title="Fechar este painel"
          (click)="closeRequested.emit()"
        >
          ✕
        </button>
      </header>

      <div class="term" #termEl (scroll)="onScroll()">
        <pre class="term-screen" [innerHTML]="screenHtml()"></pre>
      </div>

      <div class="keypad">
        <button type="button" (click)="sendKey('up')">↑</button>
        <button type="button" (click)="sendKey('down')">↓</button>
        <button type="button" (click)="sendKey('escape')">Esc</button>
        <button type="button" (click)="sendKey('ctrl-c')">Ctrl+C</button>
      </div>

      <form class="composer" (ngSubmit)="send()">
        <input
          type="text"
          [(ngModel)]="draft"
          name="draft"
          placeholder="Mensagem para {{ session()?.display_name || sessionId() }}…"
          autocomplete="off"
        />
        <button type="submit" [disabled]="!draft().trim()">Enviar</button>
      </form>
    </div>
  `,
  styles: [
    `
      .panel {
        display: flex;
        flex-direction: column;
        height: 100%;
        min-width: 0;
        border: 1px solid rgba(255, 255, 255, 0.08);
        background: #0b0d10;
      }
      .panel-header {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 8px 10px;
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);
        font-size: 13px;
        color: #d4d4d4;
      }
      .dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        flex: none;
      }
      .name {
        font-weight: 600;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .agent {
        font-size: 11px;
        opacity: 0.6;
      }
      .close-btn {
        margin-left: auto;
        background: none;
        border: none;
        color: #9aa0a6;
        cursor: pointer;
        font-size: 14px;
        padding: 2px 6px;
      }
      .close-btn:hover {
        color: #fff;
      }
      .term {
        flex: 1;
        overflow: auto;
        padding: 8px;
        min-height: 0;
      }
      .term-screen {
        margin: 0;
        font-family: 'SF Mono', Menlo, Consolas, monospace;
        font-size: 13px;
        line-height: 1.5;
        white-space: pre-wrap;
        word-break: break-word;
        color: #d4d4d4;
      }
      .keypad {
        display: flex;
        gap: 6px;
        padding: 6px 8px;
        border-top: 1px solid rgba(255, 255, 255, 0.06);
      }
      .keypad button {
        flex: 1;
        background: rgba(255, 255, 255, 0.06);
        border: none;
        border-radius: 6px;
        color: #d4d4d4;
        padding: 6px 0;
        font-size: 12px;
      }
      .composer {
        display: flex;
        gap: 6px;
        padding: 8px;
        border-top: 1px solid rgba(255, 255, 255, 0.08);
      }
      .composer input {
        flex: 1;
        background: rgba(255, 255, 255, 0.06);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 8px;
        padding: 8px 10px;
        color: #fff;
        font-size: 14px;
      }
      .composer button {
        background: #3b82f6;
        border: none;
        border-radius: 8px;
        color: #fff;
        padding: 0 14px;
        font-size: 14px;
      }
      .composer button:disabled {
        opacity: 0.4;
      }

      @media (max-width: 700px) {
        .panel {
          font-size: 13px;
        }
      }
    `,
  ],
})
export class SessionPanelComponent {
  private readonly api = inject(ApiService);
  private readonly sse = inject(SseService);
  private readonly sanitizer = inject(DomSanitizer);
  private readonly destroyRef = inject(DestroyRef);

  /** id da sessão exibida neste painel (muda quando o picker escolhe outra). */
  readonly sessionId = input.required<string>();
  /** Emitido quando o usuário clica em "fechar este painel" (✕). */
  readonly closeRequested = output<void>();

  private readonly termEl = viewChild<ElementRef<HTMLDivElement>>('termEl');

  protected readonly session = signal<Session | null>(null);
  protected readonly screen = signal<string>('');
  protected readonly draft = signal<string>('');

  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private lastScreenPushAt = '';
  private lastCols = 0;
  private lastRows = 0;
  private resizeObserver: ResizeObserver | null = null;

  protected readonly screenHtml = computed<SafeHtml>(() =>
    this.sanitizer.bypassSecurityTrustHtml(ansiToHtml(trimBlankEdges(this.screen()))),
  );

  protected readonly statusColor = computed(() => {
    const s = this.session();
    return s ? STATUS_META[s.status]?.color ?? '#6b7280' : '#6b7280';
  });

  constructor() {
    // Recarrega tudo sempre que o id de entrada muda (ou na 1ª vez).
    effect(() => {
      const id = this.sessionId();
      this.session.set(null);
      this.screen.set('');
      this.lastScreenPushAt = '';
      this.lastCols = 0;
      this.lastRows = 0;
      if (id) {
        this.loadSession(id);
        this.startPolling(id);
        this.syncTermSize(id);
      }
    });

    // Espelho pushado via SSE (mesmo canal global usado pelo resto do app —
    // ver frontend/src/app/core/sse.service.ts: connect() sem sessionId já
    // recebe frames "screen" de TODAS as sessões, casados por tmux_name).
    effect(() => {
      const tn = this.session()?.tmux_name;
      if (!tn) {
        return;
      }
      const scr = this.sse.screens()[tn];
      const at = scr?.at ?? '';
      if (scr && at !== this.lastScreenPushAt) {
        this.lastScreenPushAt = at;
        if (scr.text !== this.screen()) {
          this.screen.set(scr.text);
        }
      }
    });

    this.destroyRef.onDestroy(() => {
      if (this.pollTimer) {
        clearInterval(this.pollTimer);
      }
      this.resizeObserver?.disconnect();
    });
  }

  private loadSession(id: string): void {
    this.api
      .getSession(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({ next: (s) => this.session.set(s), error: () => {} });
  }

  private refreshScreen(id: string): void {
    this.api
      .getScreen(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (r) => this.screen.set(r.text ?? ''),
        error: () => {},
      });
  }

  private startPolling(id: string): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
    }
    this.refreshScreen(id);
    this.pollTimer = setInterval(() => this.refreshScreen(id), 4000);
  }

  /** Mede a área do painel e ajusta cols/rows do pane do tmux (best-effort). */
  private syncTermSize(id: string): void {
    // Espera o layout assentar (ex.: acabou de entrar no modo split).
    setTimeout(() => {
      const el = this.termEl()?.nativeElement;
      if (!el) {
        return;
      }
      const probe = document.createElement('span');
      probe.style.cssText = 'position:absolute;visibility:hidden;white-space:pre;';
      probe.style.font = '13px monospace';
      probe.textContent = '0'.repeat(100);
      el.appendChild(probe);
      const cw = probe.getBoundingClientRect().width / 100 || 8;
      probe.remove();

      const cols = Math.max(20, Math.floor((el.clientWidth - 16) / cw));
      const rows = Math.max(10, Math.floor((el.clientHeight - 16) / (13 * 1.5)));
      if (cols === this.lastCols && rows === this.lastRows) {
        return;
      }
      this.lastCols = cols;
      this.lastRows = rows;
      this.api
        .resizeSession(id, cols, rows)
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe({ error: () => {} });

      if (!this.resizeObserver) {
        this.resizeObserver = new ResizeObserver(() => this.syncTermSize(this.sessionId()));
        this.resizeObserver.observe(el);
      }
    }, 50);
  }

  protected agentLabel(agent: Session['agent_type']): string {
    return agentMeta(agent)?.label ?? agent;
  }

  protected onScroll(): void {
    // Sem "modo histórico" neste painel leve — o poll/SSE sempre substitui a
    // tela; deixamos o scroll nativo livre pro usuário olhar pra trás.
  }

  protected sendKey(key: 'up' | 'down' | 'escape' | 'ctrl-c'): void {
    const id = this.sessionId();
    if (!id) {
      return;
    }
    this.api.sendKey(id, key).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ error: () => {} });
  }

  protected send(): void {
    const id = this.sessionId();
    const text = this.draft().trim();
    if (!id || !text) {
      return;
    }
    this.api
      .sendInput(id, text, true)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({ error: () => {} });
    this.draft.set('');
  }
}
