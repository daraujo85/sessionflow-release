// frontend/src/app/core/voice/voice.service.ts
import { Injectable, inject, signal } from '@angular/core';
import { VoiceSocketService } from './voice-socket.service';
import { MicrophoneService } from './microphone.service';
import { AudioPlaybackService } from './audio-playback.service';
import { VoiceState, VoiceServerMessage } from './voice-state';
import { ApiService, API_BASE_URL } from '../api.service';
import { EventCuesService } from '../event-cues.service';
import { JarvisAudioService } from '../jarvis-audio.service';

const COOLDOWN_MS = 350;

@Injectable({ providedIn: 'root' })
export class VoiceService {
  private readonly api = inject(ApiService);
  private readonly apiBaseUrl = inject(API_BASE_URL);
  private readonly eventCues = inject(EventCuesService);
  private readonly jarvisAudio = inject(JarvisAudioService);

  readonly state = signal<VoiceState>('idle');
  readonly transcript = signal('');
  readonly reply = signal('');
  private turnId: string | null = null;
  // Important #6: server always sends 22050/pcm_s16le today (turn_manager.py);
  // overwritten by the real value from `assistant.speech.start` when present.
  private sampleRate = 22050;

  constructor(
    private readonly socket: VoiceSocketService,
    private readonly mic: MicrophoneService,
    private readonly playback: AudioPlaybackService,
  ) {
    this.socket.messages$.subscribe((msg) => this.onMessage(msg));
    this.socket.binary$.subscribe((buf) => this.onAudioChunk(buf));
    this.socket.closed$.subscribe(() => this.onDisconnected());
  }

  /** Botão mínimo de teste (tela de detalhe): pede ticket, conecta o WS,
   * liga a captura de mic e vincula a conversa a esta sessão. */
  async start(sessionId: string): Promise<void> {
    if (this.state() !== 'idle' && this.state() !== 'disconnected') return;
    this.state.set('connecting');
    // Suprime chimes/voz de eventos ANTES de discar — do contrário um evento
    // SSE de outra sessão pode tocar por cima do áudio da chamada assim que
    // ela conectar. `catch` abaixo desfaz se a ligação nem chegar a subir
    // (nesse caso `onDisconnected()`, via `closed$`, nunca dispara).
    this.eventCues.suppress();
    // Resumos falados do JARVIS (áudio via SSE, sessões diferentes desta
    // ligação) usam fila/elemento próprios — `EventCuesService.suppress()`
    // não os alcança. Mesmo mecanismo já usado pelo gravador de áudio.
    this.jarvisAudio.setRecording(true, 'voice-call');
    try {
      const { ticket } = await new Promise<{ ticket: string }>((resolve, reject) =>
        this.api.getVoiceTicket().subscribe({ next: resolve, error: reject }),
      );
      await this.socket.connect(ticket, this.apiBaseUrl);
      this.socket.sendJson({ type: 'voice.bind_session', sessionId });
      await this.mic.start((pcm16) =>
        this.socket.sendPcmFrame(
          pcm16.buffer.slice(pcm16.byteOffset, pcm16.byteOffset + pcm16.byteLength) as ArrayBuffer,
        ),
      );
      this.state.set('listening');
    } catch {
      this.socket.disconnect();
      this.eventCues.unsuppress();
      this.jarvisAudio.setRecording(false, 'voice-call');
      this.state.set('error');
    }
  }

  /** Encerra a conversa: fecha o socket, o que já dispara `onDisconnected()`
   * e limpa mic/playback/estado. */
  stop(): void {
    this.socket.disconnect();
    this.onDisconnected();
  }

  // Important #6: `AudioPlaybackService` was injected but `socket.binary$`
  // (where PCM chunks arrive) was never subscribed — audio bytes reached the
  // browser and were dropped, so the assistant was never actually heard.
  private onAudioChunk(buf: ArrayBuffer): void {
    this.playback.enqueue(new Int16Array(buf), this.sampleRate);
  }

  // spec §21: on disconnect, stop capture/playback and cancel the current
  // turn's client-side state — no old turn's audio may survive reconnection.
  // TODO Task 9 completo: reconexão automática via start() aqui.
  private onDisconnected(): void {
    this.mic.stop();
    this.playback.close();
    this.eventCues.unsuppress();
    this.jarvisAudio.setRecording(false, 'voice-call');
    this.state.set('disconnected');
    this.turnId = null;
    this.transcript.set('');
    this.reply.set('');
  }

  // TODO Task 9 completo: start()/stop()/bindSession() exigem decisão de
  // escopo maior (endpoint de ticket, componente que monta o botão) — fora
  // desta fatia mínima (só máquina de estados + mudo half-duplex).

  private onMessage(msg: VoiceServerMessage): void {
    switch (msg.type) {
      case 'transcript.final':
        this.turnId = msg.turnId;
        this.transcript.set(msg.text ?? '');
        this.state.set('waiting_agent');
        break;
      case 'assistant.text.delta':
        this.reply.set((this.reply() + (msg.text ?? '')).trim());
        break;
      case 'assistant.speech.start':
        this.sampleRate = msg.sampleRate ?? this.sampleRate;
        this.onSpeechStart();
        break;
      case 'assistant.speech.end':
        this.onSpeechEnd();
        break;
    }
  }

  private onSpeechStart(): void {
    this.state.set('speaking');
    this.mic.setMuted(true);
  }

  private onSpeechEnd(): void {
    this.state.set('cooldown');
    setTimeout(() => {
      this.mic.setMuted(false);
      this.state.set('listening');
      this.reply.set('');
    }, COOLDOWN_MS);
  }
}
