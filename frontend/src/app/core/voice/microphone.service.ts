// frontend/src/app/core/voice/microphone.service.ts
import { Injectable } from '@angular/core';

@Injectable({ providedIn: 'root' })
export class MicrophoneService {
  private ctx: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private silentSink: GainNode | null = null;
  private stream: MediaStream | null = null;
  private muted = false;
  private framesSent = 0;

  async start(onFrame: (pcm16: Int16Array) => void): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    console.info('[voice] mic stream acquired', this.stream.getAudioTracks().map((t) => t.label));
    this.ctx = new AudioContext();
    await this.ctx.resume();
    console.info('[voice] audio context ready', this.ctx.state, this.ctx.sampleRate);
    await this.ctx.audioWorklet.addModule('core/voice/worklets/capture.worklet.js');
    console.info('[voice] capture worklet loaded');
    const source = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, 'capture-processor');
    this.framesSent = 0;
    this.node.port.onmessage = (ev) => {
      if (this.muted) return;
      this.framesSent += 1;
      if (this.framesSent === 1 || this.framesSent % 100 === 0) {
        console.info('[voice] mic frame emitted', this.framesSent, (ev.data as ArrayBuffer).byteLength);
      }
      onFrame(new Int16Array(ev.data as ArrayBuffer));
    };
    // Worklets sem caminho para destination podem não ser processados pelo
    // navegador. Gain zero mantém o grafo vivo sem reproduzir microfone/eco.
    this.silentSink = this.ctx.createGain();
    this.silentSink.gain.value = 0;
    source.connect(this.node).connect(this.silentSink).connect(this.ctx.destination);
  }

  /** spec §14: ignore mic frames while SPEAKING, without tearing down the graph. */
  setMuted(muted: boolean): void {
    this.muted = muted;
  }

  stop(): void {
    this.node?.port.close();
    this.node?.disconnect();
    this.silentSink?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.ctx?.close();
    this.node = null;
    this.silentSink = null;
    this.stream = null;
    this.ctx = null;
  }
}
