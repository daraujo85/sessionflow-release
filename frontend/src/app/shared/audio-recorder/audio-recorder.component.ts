import {
  Component,
  EventEmitter,
  Input,
  Output,
  inject,
  signal,
} from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { ApiService } from '../../core/api.service';
import { JarvisAudioService } from '../../core/jarvis-audio.service';
import { AudioRecorderService } from './audio-recorder.service';

/**
 * Round microphone button (mockup style) that records audio and uploads it to
 * the given session. Pulses red while recording; tap again to stop.
 *
 * Emits `transcribing` (true on upload start, false when finished) and
 * `uploaded` once the audio reaches the server.
 */
@Component({
  selector: 'sf-audio-recorder',
  standalone: true,
  template: `
    <div class="sf-rec" [class.is-rec]="recorder.recording()">
      @if (recorder.recording()) {
        <!-- Cancelar (descarta) -->
        <button
          type="button"
          class="sf-rec-cancel"
          aria-label="Cancelar gravação"
          (click)="cancel()"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"
               stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M18 6 6 18M6 6l12 12" />
          </svg>
        </button>

        <!-- Onda animada (indica que está gravando) -->
        <span class="sf-wave" aria-label="Gravando…">
          @for (b of waveBars; track b) {
            <i [style.animation-delay.ms]="b"></i>
          }
        </span>

        <!-- Enviar (verde, seta) -->
        <button
          type="button"
          class="sf-rec-send"
          [disabled]="busy()"
          aria-label="Enviar gravação"
          (click)="stop()"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
               stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M22 2 11 13" />
            <path d="M22 2 15 22l-4-9-9-4 20-7z" />
          </svg>
        </button>
      } @else {
        <!-- Iniciar gravação (mic) -->
        <button
          type="button"
          class="sf-rec-btn"
          [disabled]="busy()"
          aria-label="Gravar áudio"
          (click)="start()"
        >
          <svg class="sf-rec-mic-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="9" y="2" width="6" height="12" rx="3" />
            <path d="M5 10a7 7 0 0 0 14 0" />
            <path d="M12 17v4" />
          </svg>
        </button>
      }

      @if (recorder.error()) {
        <p class="sf-rec-error" role="alert">{{ recorder.error() }}</p>
      }
    </div>
  `,
  styles: [
    `
      .sf-rec {
        display: flex;
        flex-direction: row;
        align-items: center;
        gap: 8px;
      }
      .sf-rec.is-rec {
        padding: 0 4px;
        border-radius: 22px;
        background: #14191a;
        border: 1px solid #283230;
      }
      /* Mic (idle) — botão redondo verde */
      .sf-rec-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 44px;
        height: 44px;
        border: none;
        border-radius: var(--radius-full);
        background: var(--prata-green-600, #00a482);
        color: var(--text-on-accent, #fff);
        cursor: pointer;
        -webkit-tap-highlight-color: transparent;
      }
      .sf-rec-btn:disabled {
        opacity: 0.6;
        cursor: progress;
      }
      .sf-rec-mic-svg {
        display: block;
        width: 20px;
        height: 20px;
      }
      /* Cancelar — círculo discreto com X vermelho */
      .sf-rec-cancel {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 38px;
        height: 38px;
        flex: none;
        border: none;
        border-radius: var(--radius-full);
        background: #2a1c1c;
        color: #f87171;
        cursor: pointer;
        -webkit-tap-highlight-color: transparent;
      }
      .sf-rec-cancel svg {
        width: 16px;
        height: 16px;
      }
      /* Onda animada (gravando) */
      .sf-wave {
        display: inline-flex;
        align-items: center;
        gap: 3px;
        height: 38px;
        padding: 0 4px;
      }
      .sf-wave i {
        width: 3px;
        height: 6px;
        border-radius: 2px;
        background: var(--color-accent, #00e4b4);
        animation: sf-wave 0.9s ease-in-out infinite;
      }
      @keyframes sf-wave {
        0%,
        100% {
          transform: scaleY(0.5);
          opacity: 0.6;
        }
        50% {
          transform: scaleY(2.6);
          opacity: 1;
        }
      }
      /* Enviar — botão redondo verde com seta (claro que ENVIA) */
      .sf-rec-send {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 44px;
        height: 44px;
        flex: none;
        border: none;
        border-radius: var(--radius-full);
        background: var(--color-accent, #00e4b4);
        color: #04140f;
        cursor: pointer;
        -webkit-tap-highlight-color: transparent;
      }
      .sf-rec-send:disabled {
        opacity: 0.6;
        cursor: progress;
      }
      .sf-rec-send svg {
        width: 19px;
        height: 19px;
      }
      @media (prefers-reduced-motion: reduce) {
        .sf-wave i {
          animation: none;
        }
      }
      .sf-rec-error {
        margin: 0;
        font-size: var(--text-sm);
        color: var(--danger);
        text-align: center;
      }
    `,
  ],
})
export class AudioRecorderComponent {
  /** Target session that will receive the uploaded audio. */
  @Input({ required: true }) sessionId!: string;

  /** Emits true while uploading/transcribing, false when finished. */
  @Output() transcribing = new EventEmitter<boolean>();
  /** Emits once the audio has been successfully uploaded. */
  @Output() uploaded = new EventEmitter<void>();

  protected readonly recorder = inject(AudioRecorderService);
  private readonly api = inject(ApiService);
  private readonly jarvisAudio = inject(JarvisAudioService);

  /** Disables the button while an upload is in flight. */
  protected readonly busy = signal(false);

  /** Atrasos (ms) das barras da onda — staggered p/ efeito de equalizador. */
  protected readonly waveBars = [0, 120, 240, 360, 240, 120, 0, 180, 300];

  /** Inicia a gravação. */
  async start(): Promise<void> {
    // Suprime o áudio do JARVIS para não vazar no microfone durante a gravação.
    this.jarvisAudio.setRecording(true);
    try {
      await this.recorder.start();
    } catch (err) {
      // Se a gravação não começou, libera o JARVIS imediatamente.
      this.jarvisAudio.setRecording(false);
      throw err;
    }
  }

  /** Para a gravação e ENVIA (transcreve no servidor). */
  async stop(): Promise<void> {
    await this.finish();
  }

  /** Cancela a gravação em curso e descarta o áudio (não envia). */
  cancel(): void {
    try {
      this.recorder.cancel();
    } finally {
      // Gravação encerrada: retoma o áudio do JARVIS.
      this.jarvisAudio.setRecording(false);
    }
  }

  private async finish(): Promise<void> {
    let blob: Blob;
    try {
      blob = await this.recorder.stop();
    } catch {
      return;
    } finally {
      // Gravação encerrada (com sucesso ou falha): retoma o áudio do JARVIS.
      this.jarvisAudio.setRecording(false);
    }

    this.busy.set(true);
    this.transcribing.emit(true);
    const file = new File([blob], 'gravacao.webm', { type: 'audio/webm' });
    try {
      await firstValueFrom(this.api.uploadAudio(this.sessionId, file));
      this.uploaded.emit();
    } catch {
      this.recorder.error.set('Falha ao enviar o áudio.');
    } finally {
      this.transcribing.emit(false);
      this.busy.set(false);
    }
  }
}
