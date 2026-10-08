import { Injectable, signal } from '@angular/core';

/**
 * Captures microphone audio using the MediaRecorder API and exposes a simple
 * start/stop interface plus reactive signals for UI binding.
 *
 * Supports DASH-14: client-side voice capture that is later uploaded to the
 * session for transcription.
 */
@Injectable({ providedIn: 'root' })
export class AudioRecorderService {
  /** Whether a recording is currently in progress. */
  readonly recording = signal(false);
  /** Last error message (e.g. permission denied), or null when healthy. */
  readonly error = signal<string | null>(null);

  private recorder: MediaRecorder | null = null;
  private stream: MediaStream | null = null;
  private chunks: Blob[] = [];

  /**
   * Requests microphone permission and starts recording. On failure (e.g. the
   * user denies the permission) the `error` signal is populated and the method
   * resolves without throwing.
   */
  async start(): Promise<void> {
    this.error.set(null);
    if (this.recording()) {
      return;
    }
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      this.error.set(this.describe(err));
      return;
    }

    this.chunks = [];
    this.recorder = new MediaRecorder(this.stream);
    this.recorder.addEventListener('dataavailable', (ev) => {
      const data = (ev as BlobEvent).data;
      if (data && data.size > 0) {
        this.chunks.push(data);
      }
    });
    this.recorder.start();
    this.recording.set(true);
  }

  /**
   * Stops recording and resolves with the assembled audio Blob (audio/webm).
   * Releases the underlying media stream tracks. Rejects if not recording.
   */
  stop(): Promise<Blob> {
    const recorder = this.recorder;
    if (!recorder || !this.recording()) {
      return Promise.reject(new Error('Not recording'));
    }

    return new Promise<Blob>((resolve) => {
      recorder.addEventListener(
        'stop',
        () => {
          const blob = new Blob(this.chunks, { type: 'audio/webm' });
          this.cleanup();
          this.recording.set(false);
          resolve(blob);
        },
        { once: true },
      );
      recorder.stop();
    });
  }

  /**
   * Cancela a gravação em curso e DESCARTA o áudio (sem produzir Blob nem
   * enviar). Libera o microfone. Seguro chamar mesmo se não estiver gravando.
   */
  cancel(): void {
    const recorder = this.recorder;
    if (recorder && this.recording()) {
      try {
        recorder.stop();
      } catch {
        /* já parado — ignora */
      }
    }
    this.chunks = [];
    this.cleanup();
    this.recording.set(false);
    this.error.set(null);
  }

  private cleanup(): void {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.recorder = null;
  }

  private describe(err: unknown): string {
    if (
      err instanceof Error &&
      (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError')
    ) {
      return 'Permissão de microfone negada.';
    }
    return 'Não foi possível acessar o microfone.';
  }
}
