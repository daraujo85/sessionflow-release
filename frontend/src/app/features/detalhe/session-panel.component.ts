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
import { WorkersStore } from '../../core/workers-store';
import { Session, TerminalKey } from '../../core/models';
import { STATUS_META, agentMeta } from '../../shared/status-color';
import { ansiToHtml, trimBlankEdges } from '../../shared/ansi-html';
import { AudioRecorderComponent } from '../../shared/audio-recorder/audio-recorder.component';

/**
 * Painel de sessão (terminal ao vivo + composer), instanciado N vezes lado a
 * lado no modo "dividir tela" do Detalhe. Mantém paridade com o composer do
 * DetalheComponent (anexar/drag&drop/paste, recorte de tela, câmera, áudio,
 * teclado completo) — só NÃO repete o que é meta-informação da sessão em si
 * (tarefas, métricas, share, rename, JARVIS): isso continua exclusivo da tela
 * cheia. Envio de anexo/foto/recorte aqui é direto (sem prévia com legenda
 * editável) — painel leve, prioriza simplicidade sobre a tela cheia.
 */
@Component({
  selector: 'app-session-panel',
  standalone: true,
  imports: [FormsModule, NgIf, AudioRecorderComponent],
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

      <div class="term" #termEl (scroll)="onScroll()" (mouseup)="onTermSelect()">
        <pre class="term-screen" [innerHTML]="screenHtml()"></pre>
        @if (copied()) {
          <div class="term-copied" role="status" aria-live="polite">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M5 12l4 4 10-10" />
            </svg>
            Copiado
          </div>
        }
      </div>

      <div class="keypad">
        <button type="button" (click)="sendKey('up')">↑</button>
        <button type="button" (click)="sendKey('down')">↓</button>
        <button type="button" (click)="sendKey('left')">←</button>
        <button type="button" (click)="sendKey('right')">→</button>
        <button type="button" (click)="sendKey('tab')">Tab</button>
        <button type="button" (click)="sendKey('space')">Espaço</button>
        <button type="button" (click)="sendKey('enter')">Enter</button>
        <button type="button" (click)="sendKey('escape')">Esc</button>
        <button type="button" (click)="sendKey('ctrl-c')">Ctrl+C</button>
      </div>

      <!-- Rodapé do composer: drag&drop de arquivo funciona na área toda
           (não só no botão), mesmo com o painel estreito no split de N vias. -->
      <footer
        class="inputbar"
        [class.drag-over]="dragOver()"
        (dragover)="onDragOver($event)"
        (dragleave)="onDragLeave($event)"
        (drop)="onDrop($event)"
      >
        @if (dragOver()) {
          <div class="drop-hint" aria-hidden="true">Solte para anexar</div>
        }
        <input
          #fileInput
          type="file"
          accept="image/*,*/*"
          multiple
          class="visually-hidden"
          (change)="onFileSelected($event)"
        />
        <form class="composer" (ngSubmit)="send()">
          <!-- "+" agrupa anexar/mic num popover só — o painel é estreito
               demais (1 de N) pra ter um botão por opção como na tela cheia. -->
          <button
            type="button"
            class="more-toggle"
            [class.is-on]="moreOpen()"
            (click)="moreOpen.set(!moreOpen())"
            aria-label="Mais opções (anexar arquivo, gravar áudio)"
            title="Mais opções"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M12 5v14M5 12h14" />
            </svg>
          </button>
          @if (moreOpen()) {
            <div class="more-backdrop" (click)="moreOpen.set(false)"></div>
            <div class="more-menu">
              <button
                type="button"
                class="more-item"
                [class.is-busy]="attaching()"
                (click)="pickFile()"
                aria-label="Anexar arquivo ou imagem"
                title="Anexar arquivo/imagem para o agente"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="m21.4 11.05-9.19 9.2a5 5 0 0 1-7.07-7.08l9.2-9.19a3.33 3.33 0 0 1 4.71 4.71l-9.2 9.2a1.67 1.67 0 0 1-2.36-2.36l8.49-8.49" />
                </svg>
                <span>Anexar</span>
              </button>
              @if (canScreenshot) {
                <button
                  type="button"
                  class="more-item"
                  (click)="takeShot(); moreOpen.set(false)"
                  aria-label="Capturar área da tela e anexar"
                  title="Recortar um pedaço da tela e anexar (não vai pra galeria)"
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                       stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2" />
                    <rect x="8.5" y="8.5" width="7" height="7" rx="1" />
                  </svg>
                  <span>Recortar tela</span>
                </button>
              }
              @if (canCamera) {
                <button
                  type="button"
                  class="more-item"
                  (click)="openCamera(); moreOpen.set(false)"
                  aria-label="Tirar foto com a câmera e anexar"
                  title="Tirar foto com a câmera para dar contexto ao agente"
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                       stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" />
                    <circle cx="12" cy="13" r="4" />
                  </svg>
                  <span>Câmera</span>
                </button>
              }
              @if (hostSupportsTranscription()) {
                <div class="more-item more-item--mic">
                  <sf-audio-recorder
                    [sessionId]="sessionId()"
                    (transcribing)="onAudioTranscribing($event)"
                    (uploaded)="onAudioUploaded()"
                  ></sf-audio-recorder>
                  <span>Gravar</span>
                </div>
              }
            </div>
          }
          <input
            type="text"
            [(ngModel)]="draft"
            name="draft"
            placeholder="Mensagem para {{ session()?.display_name || sessionId() }}…"
            autocomplete="off"
            (paste)="onPaste($event)"
            [disabled]="attaching()"
          />
          <button type="submit" class="send" [class.is-busy]="attaching()" [disabled]="!canSend()">
            @if (attaching()) {
              <span class="spinner" aria-hidden="true"></span>
            } @else {
              Enviar
            }
          </button>
        </form>
      </footer>

      <!-- Overlay de RECORTE do screenshot: arraste pra selecionar a área. A
           imagem fica só em memória (nunca vai pra galeria). Overlay é
           position:fixed (tela toda) — funciona igual dentro do split. -->
      @if (shotOpen()) {
        <div class="shot-overlay">
          <div class="shot-canvas">
            <img
              #shotImg
              class="shot-img"
              [src]="shotImgUrl()"
              draggable="false"
              (pointerdown)="shotDown($event)"
              (pointermove)="shotMove($event)"
              (pointerup)="shotUp()"
              alt="Captura da tela"
            />
            @if (shotSel(); as s) {
              <div
                class="shot-rect"
                [style.left.px]="s.x"
                [style.top.px]="s.y"
                [style.width.px]="s.w"
                [style.height.px]="s.h"
              ></div>
            }
          </div>
          <div class="shot-bar">
            <span class="shot-tip">Arraste para recortar, ou anexe tudo</span>
            <span class="shot-acts">
              <button type="button" class="shot-btn" (click)="cancelShot()">Cancelar</button>
              <button type="button" class="shot-btn" (click)="confirmShotFull()">Anexar tudo</button>
              <button type="button" class="shot-btn shot-btn--primary" [disabled]="!shotHasSel()" (click)="confirmShot()">
                Anexar recorte
              </button>
            </span>
          </div>
        </div>
      }

      <!-- Overlay da CÂMERA: preview ao vivo + "Tirar foto". -->
      @if (camOpen()) {
        <div class="shot-overlay">
          <div class="cam-stage">
            <video #camVideo class="cam-video" autoplay playsinline muted></video>
            @if (!camReady()) {
              <span class="cam-loading">abrindo câmera…</span>
            }
          </div>
          <div class="shot-bar">
            <span class="shot-tip">Enquadre e toque em “Tirar foto”</span>
            <span class="shot-acts">
              <button type="button" class="shot-btn" (click)="cancelCamera()">Cancelar</button>
              <button type="button" class="shot-btn" (click)="flipCamera()">Trocar câmera</button>
              <button type="button" class="shot-btn shot-btn--primary" [disabled]="!camReady()" (click)="capturePhoto()">
                Tirar foto
              </button>
            </span>
          </div>
        </div>
      }
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
      .term-copied {
        position: sticky;
        bottom: 12px;
        float: left;
        margin-left: 2px;
        z-index: 5;
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 5px 11px;
        border-radius: 999px;
        background: linear-gradient(150deg, #2cecc4, #00a482);
        color: #06231d;
        font-size: 11.5px;
        font-weight: 700;
        font-family: inherit;
        box-shadow: 0 4px 14px rgba(0, 0, 0, 0.4);
        pointer-events: none;
        animation: term-copied-in 0.15s ease-out;
      }
      .term-copied svg {
        flex: none;
      }
      @keyframes term-copied-in {
        from {
          opacity: 0;
          transform: translateY(6px);
        }
        to {
          opacity: 1;
          transform: none;
        }
      }
      .keypad {
        display: flex;
        gap: 6px;
        padding: 6px 8px;
        border-top: 1px solid rgba(255, 255, 255, 0.06);
        /* Painel estreito (1 de N) + agora 7 teclas: rola em vez de espremer
           cada botão até ficar ilegível. */
        overflow-x: auto;
        scrollbar-width: none;
      }
      .keypad::-webkit-scrollbar {
        display: none;
      }
      .keypad button {
        flex: none;
        min-width: 34px;
        background: rgba(255, 255, 255, 0.06);
        border: none;
        border-radius: 6px;
        color: #d4d4d4;
        padding: 6px 0;
        font-size: 12px;
      }
      .inputbar {
        position: relative;
        border-top: 1px solid rgba(255, 255, 255, 0.08);
      }
      .inputbar.drag-over {
        outline: 2px dashed #3b82f6;
        outline-offset: -2px;
      }
      .drop-hint {
        position: absolute;
        inset: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        background: rgba(59, 130, 246, 0.15);
        color: #cfe1ff;
        font-size: 12px;
        pointer-events: none;
        z-index: 1;
      }
      .visually-hidden {
        position: absolute;
        width: 1px;
        height: 1px;
        overflow: hidden;
        opacity: 0;
        pointer-events: none;
      }
      .composer {
        position: relative;
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 8px;
      }
      .composer input[type='text'] {
        flex: 1;
        min-width: 0;
        background: rgba(255, 255, 255, 0.06);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 8px;
        padding: 8px 10px;
        color: #fff;
        font-size: 14px;
      }
      .composer .send {
        flex: none;
        background: #3b82f6;
        border: none;
        border-radius: 8px;
        color: #fff;
        padding: 0 14px;
        height: 34px;
        font-size: 13px;
      }
      .composer .send:disabled {
        opacity: 0.4;
      }
      .spinner {
        display: inline-block;
        width: 12px;
        height: 12px;
        border: 2px solid rgba(255, 255, 255, 0.4);
        border-top-color: #fff;
        border-radius: 50%;
        animation: spin 0.7s linear infinite;
      }
      @keyframes spin {
        to {
          transform: rotate(360deg);
        }
      }
      /* "+" agrupador: só 1 botão inline mesmo com painel estreito; abre um
         popover pequeno pra cima (não empurra o layout dos outros painéis). */
      .more-toggle {
        flex: none;
        width: 34px;
        height: 34px;
        display: flex;
        align-items: center;
        justify-content: center;
        background: rgba(255, 255, 255, 0.06);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 8px;
        color: #d4d4d4;
      }
      .more-toggle.is-on {
        background: #3b82f6;
        color: #fff;
        border-color: #3b82f6;
      }
      .more-backdrop {
        position: fixed;
        inset: 0;
        z-index: 2;
      }
      .more-menu {
        position: absolute;
        bottom: calc(100% + 6px);
        left: 8px;
        z-index: 3;
        display: flex;
        flex-direction: column;
        gap: 4px;
        background: #1a1e22;
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 8px;
        padding: 6px;
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
        min-width: 120px;
      }
      .more-item {
        display: flex;
        align-items: center;
        gap: 8px;
        background: none;
        border: none;
        color: #d4d4d4;
        font-size: 12px;
        padding: 6px 8px;
        border-radius: 6px;
        text-align: left;
        white-space: nowrap;
      }
      button.more-item:hover {
        background: rgba(255, 255, 255, 0.08);
      }
      .more-item.is-busy {
        opacity: 0.6;
      }
      .more-item--mic {
        pointer-events: auto;
      }

      /* Overlays de recorte de tela / câmera — position:fixed (tela toda),
         mesmo estilo do Detalhe cheio; funcionam igual dentro de um painel
         do split (não ficam presos ao tamanho estreito do painel). */
      .shot-overlay {
        position: fixed;
        inset: 0;
        z-index: 50;
        display: flex;
        flex-direction: column;
        background: rgba(6, 8, 9, 0.92);
      }
      .shot-canvas {
        position: relative;
        flex: 1;
        min-height: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 12px;
        overflow: hidden;
      }
      .shot-img {
        max-width: 100%;
        max-height: 100%;
        object-fit: contain;
        user-select: none;
        touch-action: none;
        cursor: crosshair;
        border: 1px solid #263038;
      }
      .shot-rect {
        position: absolute;
        border: 2px solid #2cecc4;
        background: rgba(44, 236, 196, 0.14);
        pointer-events: none;
      }
      .shot-bar {
        flex: none;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        padding: 12px 16px calc(12px + env(safe-area-inset-bottom, 0px));
        background: #0e1113;
        border-top: 1px solid #20262a;
      }
      .shot-tip {
        font-size: 12.5px;
        color: #9fb0ad;
      }
      .shot-acts {
        display: flex;
        gap: 8px;
      }
      .shot-btn {
        appearance: none;
        background: transparent;
        border: 1px solid #283230;
        border-radius: 10px;
        color: #c9cdd6;
        font-size: 13px;
        font-weight: 600;
        padding: 8px 16px;
        cursor: pointer;
      }
      .shot-btn--primary {
        background: linear-gradient(150deg, #2cecc4, #00a482);
        border-color: transparent;
        color: #06231d;
      }
      .shot-btn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }
      .cam-stage {
        position: relative;
        flex: 1;
        min-height: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 12px;
        overflow: hidden;
      }
      .cam-video {
        max-width: 100%;
        max-height: 100%;
        object-fit: contain;
        background: #000;
        border: 1px solid #263038;
        border-radius: 8px;
      }
      .cam-loading {
        position: absolute;
        font-size: 13px;
        color: #9fb0ad;
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
  private readonly workers = inject(WorkersStore);

  /** id da sessão exibida neste painel (muda quando o picker escolhe outra). */
  readonly sessionId = input.required<string>();
  /** Emitido quando o usuário clica em "fechar este painel" (✕). */
  readonly closeRequested = output<void>();

  private readonly termEl = viewChild<ElementRef<HTMLDivElement>>('termEl');
  private readonly fileInput = viewChild<ElementRef<HTMLInputElement>>('fileInput');

  protected readonly session = signal<Session | null>(null);
  protected readonly screen = signal<string>('');
  protected readonly draft = signal<string>('');
  /** Popover "+ " (anexar/mic) — agrupado porque o painel é 1 de N, sem
   * espaço pra um botão por opção como na tela cheia (ver AD do split). */
  protected readonly moreOpen = signal<boolean>(false);
  protected readonly dragOver = signal<boolean>(false);
  protected readonly attaching = signal<boolean>(false);
  protected readonly copied = signal<boolean>(false);
  private copyTimer: ReturnType<typeof setTimeout> | null = null;

  /** Captura de tela (só onde o navegador suporta — desktop/Mac), igual ao Detalhe cheio. */
  protected readonly canScreenshot =
    typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getDisplayMedia;
  protected readonly shotOpen = signal<boolean>(false);
  protected readonly shotImgUrl = signal<string>('');
  protected readonly shotSel = signal<{ x: number; y: number; w: number; h: number } | null>(null);
  protected readonly shotHasSel = computed(() => {
    const s = this.shotSel();
    return !!s && s.w >= 4 && s.h >= 4;
  });
  private shotCanvas: HTMLCanvasElement | null = null;
  private shotStart: { x: number; y: number } | null = null;
  private readonly shotImg = viewChild<ElementRef<HTMLImageElement>>('shotImg');

  /** Câmera do aparelho — mesmo mecanismo do Detalhe cheio (getUserMedia). */
  protected readonly canCamera =
    typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia;
  protected readonly camOpen = signal<boolean>(false);
  protected readonly camReady = signal<boolean>(false);
  private readonly camVideo = viewChild<ElementRef<HTMLVideoElement>>('camVideo');
  private camStream: MediaStream | null = null;
  private camFacing: 'environment' | 'user' = 'environment';

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

  /** Mesmo gate de capabilities do Detalhe cheio (AD-011): esconde o mic se
   * o host desta sessão não suportar transcrição. */
  protected readonly hostSupportsTranscription = computed(() =>
    this.workers.supports(this.session()?.host_id, 'transcription'),
  );

  protected readonly canSend = computed(
    () => this.draft().trim().length > 0 && !this.attaching(),
  );

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
      this.camStream?.getTracks().forEach((t) => t.stop());
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

  /** Selecionou texto no terminal → copia pro clipboard e avisa ("Copiado"). */
  protected onTermSelect(): void {
    const sel = typeof window !== 'undefined' ? window.getSelection?.() : null;
    const text = sel?.toString() ?? '';
    if (!text.trim()) {
      return;
    }
    const el = this.termEl()?.nativeElement;
    if (el && sel?.anchorNode && !el.contains(sel.anchorNode)) {
      return; // seleção começou fora do terminal
    }
    navigator.clipboard
      ?.writeText(text)
      .then(() => this.flashCopied())
      .catch(() => {
        /* clipboard indisponível — silencioso */
      });
  }

  /** Mostra o toast "Copiado" e agenda o sumiço. */
  private flashCopied(): void {
    this.copied.set(true);
    if (this.copyTimer) {
      clearTimeout(this.copyTimer);
    }
    this.copyTimer = setTimeout(() => this.copied.set(false), 1500);
  }

  protected sendKey(key: TerminalKey): void {
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

  /** Abre o seletor de arquivo nativo (botão do popover "+"). */
  protected pickFile(): void {
    this.moreOpen.set(false);
    this.fileInput()?.nativeElement.click();
  }

  protected onFileSelected(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    const files = Array.from(input.files ?? []);
    input.value = ''; // permite escolher o MESMO arquivo de novo depois
    this.uploadFiles(files);
  }

  protected onDragOver(ev: DragEvent): void {
    ev.preventDefault();
    this.dragOver.set(true);
  }

  protected onDragLeave(ev: DragEvent): void {
    ev.preventDefault();
    this.dragOver.set(false);
  }

  protected onDrop(ev: DragEvent): void {
    ev.preventDefault();
    this.dragOver.set(false);
    const files = Array.from(ev.dataTransfer?.files ?? []);
    this.uploadFiles(files);
  }

  /**
   * Envia direto (sem prévia com legenda editável, ao contrário do composer
   * cheio) — painel leve, prioriza simplicidade. O texto já digitado (se
   * houver) vira a legenda que acompanha o(s) anexo(s).
   */
  private uploadFiles(files: File[]): void {
    const id = this.sessionId();
    if (!id || files.length === 0) {
      return;
    }
    this.moreOpen.set(false);
    this.attaching.set(true);
    const caption = this.draft().trim() || undefined;
    this.api
      .uploadFile(id, files, caption)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.draft.set('');
          this.attaching.set(false);
        },
        error: () => this.attaching.set(false),
      });
  }

  protected onAudioTranscribing(busy: boolean): void {
    this.attaching.set(busy);
  }

  protected onAudioUploaded(): void {
    this.moreOpen.set(false);
  }

  /** Colar (Cmd/Ctrl+V) com imagem(ns) na área de transferência → anexa
   * direto (mesmo caminho do drag&drop/anexar). Ignora colagem de texto. */
  protected onPaste(event: ClipboardEvent): void {
    const items = event.clipboardData?.items;
    if (!items) {
      return;
    }
    const ts = Date.now();
    const images: File[] = [];
    for (const it of Array.from(items)) {
      if (it.kind !== 'file' || !it.type.startsWith('image/')) {
        continue;
      }
      const file = it.getAsFile();
      if (!file) {
        continue;
      }
      images.push(
        file.name
          ? file
          : new File([file], `colado-${ts}-${images.length + 1}.png`, { type: file.type }),
      );
    }
    if (images.length) {
      event.preventDefault();
      this.uploadFiles(images);
    }
  }

  /** Captura a tela (getDisplayMedia), tira UM frame e abre o overlay de recorte. */
  protected async takeShot(): Promise<void> {
    const md = navigator.mediaDevices;
    if (!md?.getDisplayMedia) {
      return;
    }
    let stream: MediaStream | null = null;
    try {
      stream = await md.getDisplayMedia({ video: true, audio: false });
      const video = document.createElement('video');
      video.srcObject = stream;
      video.muted = true;
      await video.play();
      for (let i = 0; i < 20 && !video.videoWidth; i++) {
        await new Promise((r) => setTimeout(r, 50));
      }
      const w = video.videoWidth || 1280;
      const h = video.videoHeight || 720;
      const canvas = document.createElement('canvas');
      canvas.width = w;
      canvas.height = h;
      canvas.getContext('2d')?.drawImage(video, 0, 0, w, h);
      this.shotCanvas = canvas;
      this.shotImgUrl.set(canvas.toDataURL('image/png'));
      this.shotSel.set(null);
      this.shotStart = null;
      this.shotOpen.set(true);
    } catch {
      /* usuário cancelou o compartilhamento ou negou — silencioso */
    } finally {
      stream?.getTracks().forEach((t) => t.stop());
    }
  }

  protected shotDown(ev: PointerEvent): void {
    const img = this.shotImg()?.nativeElement;
    if (!img) {
      return;
    }
    const r = img.getBoundingClientRect();
    this.shotStart = { x: ev.clientX - r.left, y: ev.clientY - r.top };
    this.shotSel.set({ x: this.shotStart.x, y: this.shotStart.y, w: 0, h: 0 });
    try {
      img.setPointerCapture(ev.pointerId);
    } catch {
      /* sem captura de ponteiro — segue */
    }
  }

  protected shotMove(ev: PointerEvent): void {
    if (!this.shotStart) {
      return;
    }
    const img = this.shotImg()?.nativeElement;
    if (!img) {
      return;
    }
    const r = img.getBoundingClientRect();
    const cx = Math.max(0, Math.min(ev.clientX - r.left, r.width));
    const cy = Math.max(0, Math.min(ev.clientY - r.top, r.height));
    this.shotSel.set({
      x: Math.min(this.shotStart.x, cx),
      y: Math.min(this.shotStart.y, cy),
      w: Math.abs(cx - this.shotStart.x),
      h: Math.abs(cy - this.shotStart.y),
    });
  }

  protected shotUp(): void {
    this.shotStart = null;
  }

  /** Recorta a área selecionada → File PNG → anexa (envio direto). */
  protected confirmShot(): void {
    const sel = this.shotSel();
    const src = this.shotCanvas;
    const img = this.shotImg()?.nativeElement;
    if (!sel || !src || !img || sel.w < 4 || sel.h < 4) {
      return;
    }
    const scaleX = src.width / img.clientWidth;
    const scaleY = src.height / img.clientHeight;
    const out = document.createElement('canvas');
    out.width = Math.max(1, Math.round(sel.w * scaleX));
    out.height = Math.max(1, Math.round(sel.h * scaleY));
    out
      .getContext('2d')
      ?.drawImage(
        src,
        sel.x * scaleX,
        sel.y * scaleY,
        sel.w * scaleX,
        sel.h * scaleY,
        0,
        0,
        out.width,
        out.height,
      );
    out.toBlob((blob) => {
      if (blob) {
        this.uploadFiles([new File([blob], `recorte-${Date.now()}.png`, { type: 'image/png' })]);
      }
      this.closeShot();
    }, 'image/png');
  }

  /** Anexa a captura INTEIRA (aba/janela/tela toda), sem exigir recorte. */
  protected confirmShotFull(): void {
    const src = this.shotCanvas;
    if (!src) {
      return;
    }
    src.toBlob((blob) => {
      if (blob) {
        this.uploadFiles([new File([blob], `captura-${Date.now()}.png`, { type: 'image/png' })]);
      }
      this.closeShot();
    }, 'image/png');
  }

  protected cancelShot(): void {
    this.closeShot();
  }

  private closeShot(): void {
    this.shotOpen.set(false);
    this.shotSel.set(null);
    this.shotStart = null;
    this.shotCanvas = null;
    this.shotImgUrl.set('');
  }

  /** Abre a câmera do aparelho (getUserMedia) e mostra o preview ao vivo. */
  protected async openCamera(): Promise<void> {
    const md = navigator.mediaDevices;
    if (!md?.getUserMedia) {
      return;
    }
    let stream: MediaStream;
    try {
      stream = await md.getUserMedia(this.camConstraints());
    } catch {
      return; // permissão negada — silencioso (painel leve, sem balão de aviso)
    }
    this.camStream = stream;
    this.camReady.set(false);
    this.camOpen.set(true);
    setTimeout(() => this.attachCamStream(), 0);
  }

  private attachCamStream(): void {
    const video = this.camVideo()?.nativeElement;
    if (!video || !this.camStream) {
      return;
    }
    video.srcObject = this.camStream;
    video.muted = true;
    video
      .play()
      .then(() => this.camReady.set(true))
      .catch(() => this.camReady.set(true));
  }

  protected async flipCamera(): Promise<void> {
    this.camFacing = this.camFacing === 'environment' ? 'user' : 'environment';
    this.stopCamStream();
    this.camReady.set(false);
    try {
      this.camStream = await navigator.mediaDevices.getUserMedia(this.camConstraints());
      this.attachCamStream();
    } catch {
      this.closeCamera();
    }
  }

  private camConstraints(): MediaStreamConstraints {
    return {
      video: { facingMode: this.camFacing, width: { ideal: 3840 }, height: { ideal: 2160 } },
      audio: false,
    };
  }

  protected async capturePhoto(): Promise<void> {
    const blob = await this.grabPhotoBlob();
    if (blob) {
      const ext = blob.type === 'image/png' ? 'png' : 'jpg';
      this.uploadFiles([
        new File([blob], `foto-${Date.now()}.${ext}`, { type: blob.type || 'image/jpeg' }),
      ]);
    }
    this.closeCamera();
  }

  private async grabPhotoBlob(): Promise<Blob | null> {
    const track = this.camStream?.getVideoTracks()[0];
    const ImageCaptureCtor = (
      globalThis as unknown as {
        ImageCapture?: new (t: MediaStreamTrack) => { takePhoto(): Promise<Blob> };
      }
    ).ImageCapture;
    if (track && ImageCaptureCtor) {
      try {
        const still = await new ImageCaptureCtor(track).takePhoto();
        if (still && still.size > 0) {
          return still;
        }
      } catch {
        /* sem suporte real / negado → cai pro frame do vídeo */
      }
    }
    const video = this.camVideo()?.nativeElement;
    if (!video || !video.videoWidth) {
      return null;
    }
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d')?.drawImage(video, 0, 0, canvas.width, canvas.height);
    return new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.92));
  }

  protected cancelCamera(): void {
    this.closeCamera();
  }

  private closeCamera(): void {
    this.stopCamStream();
    this.camOpen.set(false);
    this.camReady.set(false);
  }

  private stopCamStream(): void {
    this.camStream?.getTracks().forEach((t) => t.stop());
    this.camStream = null;
  }
}
