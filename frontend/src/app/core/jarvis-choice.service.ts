import { Injectable, effect, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { JarvisChoiceFrame, SseService } from './sse.service';
import { JarvisAudioService } from './jarvis-audio.service';
import { ApiService } from './api.service';
import { NotifyService } from './notify.service';
import { AudioRecorderService } from '../shared/audio-recorder/audio-recorder.service';

/** Janela fixa de gravação da resposta (v1: sem detecção de silêncio — ver
 * plano, item "fora de escopo"). */
const LISTEN_WINDOW_MS = 6000;

/**
 * Fecha o loop do modo JARVIS COMPLETO: quando o worker detecta um picker de
 * escolha numerada numa sessão em modo `full`, publica um frame `jarvis_choice`
 * (pergunta já sintetizada em voz). Este serviço:
 *
 * - toca a pergunta (mesma fila do {@link JarvisAudioService}, sem sobrepor
 *   áudio de outras sessões);
 * - se a aba está em FOCO, abre o microfone sozinho e sobe a resposta;
 * - se não está em foco, dispara uma notificação nativa ("preciso que você
 *   responda") — ao clicar, o Service Worker foca a janela na sessão certa e,
 *   quando o foco voltar, o mic abre então;
 * - **só uma escolha ativa por vez**: se chegar `jarvis_choice` de outra
 *   sessão enquanto uma já está sendo respondida, ela entra numa fila e
 *   espera a atual resolver (upload enviado) — nunca duas gravações/avisos
 *   simultâneos (pedido explícito do Diego).
 *
 * A classificação da fala pra tecla (e a injeção no tmux) acontece no WORKER
 * (`_maybe_resolve_pending_choice`, ver `command_consumer.py`) depois que o
 * áudio sobe — este serviço só grava e envia, não decide a tecla.
 */
@Injectable({ providedIn: 'root' })
export class JarvisChoiceService {
  private readonly sse = inject(SseService);
  private readonly jarvisAudio = inject(JarvisAudioService);
  private readonly api = inject(ApiService);
  private readonly notify = inject(NotifyService);
  private readonly recorder = inject(AudioRecorderService);
  private readonly router = inject(Router);

  /** Frame ativo agora (UI usa pra mostrar as opções/estado de "ouvindo"). */
  readonly activeFrame = signal<JarvisChoiceFrame | null>(null);
  /** true enquanto o mic está gravando a resposta. */
  readonly listening = signal(false);

  private readonly queue: JarvisChoiceFrame[] = [];
  /** id (Mongo) da sessão do frame ativo — resolvido via listSessions() (o
   * frame só carrega o tmux_name, que é o que o worker conhece). */
  private activeSessionId: string | null = null;
  private lastAt: string | null = null;
  private stopTimer: ReturnType<typeof setTimeout> | null = null;
  private started = false;

  init(): void {
    if (this.started || typeof window === 'undefined') {
      return;
    }
    this.started = true;

    effect(() => {
      const frame = this.sse.jarvisChoice();
      if (!frame || frame.at === this.lastAt) {
        return;
      }
      this.lastAt = frame.at;
      this.queue.push(frame);
      this.pump();
    });

    // A pergunta terminou de tocar (fila do JarvisAudioService) → decide
    // mic-na-hora vs notificação, conforme o foco da janela.
    effect(() => {
      const done = this.jarvisAudio.choiceAudioDone();
      const active = this.activeFrame();
      if (!done || !active || done.at !== active.at) {
        return;
      }
      this.onQuestionSpoken();
    });

    window.addEventListener('focus', () => {
      if (this.activeFrame() && !this.listening()) {
        this.startListening();
      }
    });
  }

  /** Fallback manual: usuário tocou num botão de opção em vez de falar. */
  answerManually(key: string): void {
    const sessionId = this.activeSessionId;
    if (!sessionId) {
      return;
    }
    this.clearStopTimer();
    if (this.listening()) {
      this.jarvisAudio.setRecording(false);
      this.recorder.cancel();
      this.listening.set(false);
    }
    this.api.sendInput(sessionId, key, true).subscribe();
    this.finish();
  }

  /** Desiste da escolha ativa sem responder (ex.: usuário fechou o banner). */
  dismiss(): void {
    this.clearStopTimer();
    if (this.listening()) {
      this.jarvisAudio.setRecording(false);
      this.recorder.cancel();
      this.listening.set(false);
    }
    this.finish();
  }

  private pump(): void {
    if (this.activeFrame()) {
      return; // já tem uma ativa — a próxima espera (fila serializada)
    }
    const next = this.queue.shift();
    if (!next) {
      return;
    }
    this.activate(next);
  }

  private activate(frame: JarvisChoiceFrame): void {
    this.activeFrame.set(frame);
    this.activeSessionId = null;
    // Resolve tmux_name → id Mongo + confirma que ainda está em modo full
    // (pode ter mudado entre o worker detectar e este frame chegar).
    this.api.listSessions().subscribe({
      next: (list) => {
        const s = list.find((it) => it.tmux_name === frame.session_id);
        if (!s || (s.jarvis_mode ?? (s.jarvis ? 'speaker' : 'off')) !== 'full') {
          this.finish();
          return;
        }
        this.activeSessionId = s.id;
        this.jarvisAudio.enqueueChoice(frame);
      },
      error: () => this.finish(),
    });
  }

  private onQuestionSpoken(): void {
    if (!this.activeSessionId) {
      return;
    }
    if (typeof document !== 'undefined' && document.hasFocus()) {
      // Leva o usuário ATÉ a sessão que perguntou antes de abrir o mic —
      // senão o mic abria "invisível" enquanto ele olhava outra tela (o
      // banner de escolha só aparece na tela da sessão dona da pergunta).
      // Navegar pra mesma rota em que já está é um no-op inofensivo.
      void this.router.navigate(['/sessao', this.activeSessionId]);
      this.startListening();
      return;
    }
    const opts = this.activeFrame()?.options ?? [];
    const body =
      opts.map((o) => `${o.key}. ${o.label}`).join('  ·  ') ||
      'Toque para abrir a sessão e responder por voz';
    void this.notify.notify('Preciso que você responda', {
      body,
      url: `/sessao/${this.activeSessionId}`,
    });
  }

  private startListening(): void {
    if (this.listening() || !this.activeFrame()) {
      return;
    }
    this.listening.set(true);
    this.jarvisAudio.setRecording(true); // suprime eco do TTS no mic
    void this.recorder.start();
    this.stopTimer = setTimeout(() => this.stopListeningAndSend(), LISTEN_WINDOW_MS);
  }

  private stopListeningAndSend(): void {
    this.clearStopTimer();
    const sessionId = this.activeSessionId;
    this.listening.set(false);
    this.jarvisAudio.setRecording(false);
    this.recorder
      .stop()
      .then((blob) => {
        if (sessionId && blob && blob.size > 0) {
          const file = new File([blob], 'resposta-jarvis.webm', {
            type: blob.type || 'audio/webm',
          });
          this.api.uploadAudio(sessionId, file).subscribe();
        }
      })
      .catch(() => {
        /* sem gravação de verdade (permissão negada etc.) — nada a subir */
      })
      .finally(() => this.finish());
  }

  private clearStopTimer(): void {
    if (this.stopTimer !== null) {
      clearTimeout(this.stopTimer);
      this.stopTimer = null;
    }
  }

  private finish(): void {
    this.activeFrame.set(null);
    this.activeSessionId = null;
    this.pump();
  }
}
