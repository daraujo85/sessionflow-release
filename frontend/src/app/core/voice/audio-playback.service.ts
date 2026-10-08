// frontend/src/app/core/voice/audio-playback.service.ts
import { Injectable } from '@angular/core';

@Injectable({ providedIn: 'root' })
export class AudioPlaybackService {
  private ctx: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private ready: Promise<void> | null = null;
  private ctxSampleRate: number | null = null;

  private async init(sampleRate: number): Promise<void> {
    this.ctx = new AudioContext({ sampleRate });
    await this.ctx.audioWorklet.addModule('core/voice/worklets/playback.worklet.js');
    this.node = new AudioWorkletNode(this.ctx, 'playback-processor', { outputChannelCount: [1] });
    this.node.connect(this.ctx.destination);
    this.ctxSampleRate = sampleRate;
  }

  /**
   * Enqueues a PCM16 chunk for playback. Lazily creates the AudioContext at
   * `sampleRate` on first call, or re-creates it if the server switches rate
   * mid-session (rare, but the worklet must run at the stream's native rate).
   */
  enqueue(pcm16: Int16Array, sampleRate: number): void {
    if (!this.ready || this.ctxSampleRate !== sampleRate) {
      this.close();
      this.ready = this.init(sampleRate);
    }
    // Copy out of the caller's buffer before transferring ownership to the worklet.
    const buffer = pcm16.buffer.slice(pcm16.byteOffset, pcm16.byteOffset + pcm16.byteLength);
    this.ready.then(() => this.node?.port.postMessage(buffer, [buffer]));
  }

  /** spec §13/§19: cancellation must clear pending audio immediately. */
  clear(): void {
    this.node?.port.postMessage('clear');
  }

  close(): void {
    this.ctx?.close();
    this.ctx = null;
    this.node = null;
    this.ready = null;
    this.ctxSampleRate = null;
  }
}
