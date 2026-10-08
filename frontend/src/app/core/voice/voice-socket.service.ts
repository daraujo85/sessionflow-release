// frontend/src/app/core/voice/voice-socket.service.ts
import { Injectable } from '@angular/core';
import { Subject } from 'rxjs';
import { VoiceServerMessage } from './voice-state';

@Injectable({ providedIn: 'root' })
export class VoiceSocketService {
  private ws: WebSocket | null = null;
  private pcmFramesSent = 0;
  readonly messages$ = new Subject<VoiceServerMessage>();
  readonly binary$ = new Subject<ArrayBuffer>();
  readonly closed$ = new Subject<void>();

  // Bug: sendJson() logo após connect() batia num socket ainda CONNECTING
  // (ws.send() síncrono lança InvalidStateError) — daí o botão "piscar e não
  // fazer nada". connect() agora só resolve depois do handshake completar
  // (onopen), pra quem chama poder aguardar antes de mandar qualquer coisa.
  connect(ticket: string, origin = location.origin): Promise<void> {
    const wsUrl = origin.replace(/^http/, 'ws') + `/ws/voice?ticket=${ticket}`;
    const ws = new WebSocket(wsUrl);
    this.ws = ws;
    this.pcmFramesSent = 0;
    ws.binaryType = 'arraybuffer';
    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') {
        this.messages$.next(JSON.parse(ev.data));
      } else {
        this.binary$.next(ev.data as ArrayBuffer);
      }
    };
    const pingInterval = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' }));
    }, 20000);
    ws.onclose = () => {
      clearInterval(pingInterval);
      this.closed$.next();
    };
    return new Promise<void>((resolve, reject) => {
      ws.onopen = () => resolve();
      ws.onerror = () => reject(new Error('voice socket failed to open'));
    });
  }

  sendJson(msg: Record<string, unknown>): void {
    this.ws?.send(JSON.stringify(msg));
  }

  sendPcmFrame(frame: ArrayBuffer): void {
    if (this.ws?.readyState !== WebSocket.OPEN) {
      console.warn('[voice] dropped PCM; socket not open', this.ws?.readyState);
      return;
    }
    this.pcmFramesSent += 1;
    if (this.pcmFramesSent === 1 || this.pcmFramesSent % 100 === 0) {
      console.info('[voice] PCM sent to WebSocket', this.pcmFramesSent, frame.byteLength);
    }
    this.ws.send(frame);
  }

  disconnect(): void {
    this.ws?.close();
    this.ws = null;
  }
}
