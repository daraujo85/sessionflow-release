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
    <div class="sf-rec">
      @if (recorder.recording()) {
        <button
          type="button"
          class="sf-rec-cancel"
          aria-label="Cancelar gravação"
          (click)="cancel()"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2.4"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path d="M18 6 6 18M6 6l12 12" />
          </svg>
        </button>
      }
      <button
        type="button"
        class="sf-rec-btn"
        [class.is-recording]="recorder.recording()"
        [disabled]="busy()"
        [attr.aria-label]="
          recorder.recording() ? 'Parar e enviar gravação' : 'Iniciar gravação'
        "
        (click)="toggle()"
      >
        <span class="sf-rec-icon" aria-hidden="true">
          <svg
            class="sf-rec-mic-svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <rect x="9" y="2" width="6" height="12" rx="3" />
            <path d="M5 10a7 7 0 0 0 14 0" />
            <path d="M12 17v4" />
          </svg>
        </span>
      </button>

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
        flex-wrap: wrap;
        gap: var(--space-2);
      }
      .sf-rec-cancel {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 44px;
        height: 44px;
        flex: none;
        border: 1px solid #3a2326;
        border-radius: var(--radius-full);
        background: #181c1b;
        color: #f87171;
        cursor: pointer;
      }
      .sf-rec-cancel svg {
        width: 18px;
        height: 18px;
      }
      .sf-rec-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 64px;
        height: 64px;
        border: none;
        border-radius: var(--radius-full);
        background: var(--prata-green-600);
        color: var(--text-on-accent, #fff);
        cursor: pointer;
        transition:
          background var(--dur-fast) var(--ease-standard),
          transform var(--dur-fast) var(--ease-standard);
      }
      .sf-rec-btn:disabled {
        opacity: 0.6;
        cursor: progress;
      }
      .sf-rec-btn.is-recording {
        background: var(--danger);
        animation: sf-rec-pulse 1.2s ease-in-out infinite;
      }
      .sf-rec-icon {
        display: inline-flex;
        align-items: center;
        justify-content: center;
      }
      .sf-rec-mic-svg {
        display: block;
        width: 22px;
        height: 22px;
      }
      .sf-rec-btn.is-recording .sf-rec-mic-svg {
        animation: sf-rec 1s infinite;
      }
      @keyframes sf-rec {
        0% {
          transform: scale(1);
          opacity: 1;
        }
        50% {
          transform: scale(1.22);
          opacity: 0.55;
        }
      }
      .sf-rec-error {
        margin: 0;
        font-size: var(--text-sm);
        color: var(--danger);
        text-align: center;
      }
      @keyframes sf-rec-pulse {
        0% {
          box-shadow: 0 0 0 0 rgba(248, 113, 113, 0.5);
        }
        70% {
          box-shadow: 0 0 0 12px rgba(248, 113, 113, 0);
        }
        100% {
          box-shadow: 0 0 0 0 rgba(248, 113, 113, 0);
        }
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

  /** Disables the button while an upload is in flight. */
  protected readonly busy = signal(false);

  async toggle(): Promise<void> {
    if (this.recorder.recording()) {
      await this.finish();
    } else {
      await this.recorder.start();
    }
  }

  /** Cancela a gravação em curso e descarta o áudio (não envia). */
  cancel(): void {
    this.recorder.cancel();
  }

  private async finish(): Promise<void> {
    let blob: Blob;
    try {
      blob = await this.recorder.stop();
    } catch {
      return;
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
