import { Injectable, effect, inject, signal } from '@angular/core';
import { JarvisAudioFrame, SseService } from './sse.service';

/**
 * Toca os áudios do JARVIS (resumo falado) que chegam via SSE.
 *
 * O worker publica um frame `jarvis_audio` (base64 ogg/opus) quando uma sessão
 * conclui/aguarda e o recurso está ligado. Este serviço:
 *
 * - reage ao signal {@link SseService.jarvisAudio};
 * - **enfileira** os áudios para não sobreporem (várias sessões ao mesmo tempo);
 * - lida com o **bloqueio de autoplay** do navegador. Áudio só toca depois de um
 *   gesto do usuário. No 1º toque/clique/tecla nós "abençoamos" UM elemento
 *   `<audio>` tocando um clipe silencioso dentro do gesto — a partir daí o mesmo
 *   elemento pode tocar clipes futuros SEM precisar de gesto recente (que é o
 *   caso real: o áudio chega minutos depois, com o celular parado). Antes do
 *   primeiro gesto, guardamos o último frame como "pendente" e tocamos assim que
 *   o app receber qualquer toque.
 *
 * É instanciado uma vez (root) e ativado via {@link init} no bootstrap do app.
 */
@Injectable({ providedIn: 'root' })
export class JarvisAudioService {
  private readonly sse = inject(SseService);

  /** Liga/desliga a reprodução no cliente (preferência local do aparelho). */
  readonly enabled = signal(readEnabled());
  /** Volume da reprodução (0–100), local por aparelho — Perfil > Áudio. */
  readonly volume = signal(readVolume());
  /** Áudio tocando agora (para um indicador visual, se quiser). */
  readonly speaking = signal(false);
  /** tmux_name da sessão cujo áudio está tocando agora (ou null) — p/ marcar o
   * card que está "falando" nas listas. */
  readonly speakingSessionId = signal<string | null>(null);

  private readonly queue: JarvisAudioFrame[] = [];
  /** Um único elemento, "abençoado" no 1º gesto e reusado p/ todos os clipes. */
  private el: HTMLAudioElement | null = null;
  private playing = false;
  private unlocked = false;
  /** Enquanto o usuário grava áudio, segura a reprodução (evita vazar no mic). */
  private suppressed = false;
  private pending: JarvisAudioFrame | null = null;
  /** Frame tocando agora, p/ saber se o próximo `onClipDone` deve repetir
   * (1ª vez) ou seguir pro próximo da fila (repetição já feita). */
  private currentFrame: JarvisAudioFrame | null = null;
  private repeating = false;
  private repeatTimer: ReturnType<typeof setTimeout> | null = null;
  private lastAt: string | null = null;
  /** Reforço anti-repetição: último TEXTO falado + quando (ms). Pega casos de
   * reconexão do SSE reentregando um frame com `at` diferente mas mesmo conteúdo. */
  private lastText: string | null = null;
  private lastTextAt = 0;
  private started = false;

  /** Liga o pipeline: efeito no signal de SSE + desbloqueio por gesto. */
  init(): void {
    if (this.started || typeof window === 'undefined') {
      return;
    }
    this.started = true;
    this.el = new Audio();
    this.el.volume = this.volume() / 100;
    this.el.addEventListener('ended', () => this.onClipDone());
    this.el.addEventListener('error', () => this.onClipDone());

    const unlock = () => {
      if (this.unlocked) {
        return;
      }
      // "Abençoa" o elemento tocando um clipe silencioso DENTRO do gesto.
      const el = this.el;
      if (el) {
        el.src = SILENT_WAV;
        el.muted = true;
        void el
          .play()
          .then(() => {
            el.pause();
            el.muted = false;
            this.unlocked = true;
            if (this.pending) {
              this.enqueue(this.pending);
              this.pending = null;
            }
          })
          .catch(() => {
            el.muted = false;
          });
      }
    };
    window.addEventListener('pointerdown', unlock);
    window.addEventListener('keydown', unlock);
    window.addEventListener('touchstart', unlock);

    effect(() => {
      const frame = this.sse.jarvisAudio();
      if (!frame || !frame.audio_b64) {
        return;
      }
      // Dedup por timestamp (o mesmo frame não deve tocar 2x).
      if (frame.at && frame.at === this.lastAt) {
        return;
      }
      this.lastAt = frame.at ?? null;
      // Dedup por CONTEÚDO+tempo: se o mesmo texto foi falado há < 150s (ex.:
      // reconexão do SSE ou re-detecção de "waiting"), não repete.
      const text = (frame.text || frame.title || '').trim();
      const now = Date.now();
      if (text && text === this.lastText && now - this.lastTextAt < 150_000) {
        return;
      }
      if (text) {
        this.lastText = text;
        this.lastTextAt = now;
      }
      if (!this.enabled()) {
        return;
      }
      if (!this.unlocked) {
        this.pending = frame; // toca no 1º gesto
        return;
      }
      this.enqueue(frame);
    });
  }

  /** Ajusta o volume (0–100), persiste em localStorage e aplica na hora, mesmo
   * com um clipe já tocando (não precisa esperar o próximo). */
  setVolume(v: number): void {
    const clamped = Math.min(100, Math.max(0, Math.round(v)));
    this.volume.set(clamped);
    try {
      localStorage.setItem(VOLUME_KEY, String(clamped));
    } catch {
      /* storage indisponível — silencioso */
    }
    if (this.el) {
      this.el.volume = clamped / 100;
    }
  }

  /** Liga/desliga a reprodução neste aparelho (persiste em localStorage). */
  setEnabled(on: boolean): void {
    this.enabled.set(on);
    try {
      localStorage.setItem(STORAGE_KEY, on ? '1' : '0');
    } catch {
      /* storage indisponível — silencioso */
    }
    if (!on) {
      this.stop();
    }
  }

  /**
   * Liga/desliga a supressão da reprodução enquanto o usuário grava áudio.
   *
   * Com `on=true`: pausa o clipe atual e segura a fila (sem descartar frames).
   * Com `on=false`: libera e retoma a reprodução se houver itens enfileirados.
   */
  setRecording(on: boolean): void {
    this.suppressed = on;
    if (on) {
      this.clearRepeatTimer();
      if (this.el) {
        this.el.pause();
      }
      this.playing = false;
      this.speaking.set(false);
      this.speakingSessionId.set(null);
    } else if (this.queue.length > 0 && !this.playing) {
      this.playNext();
    }
  }

  /** Interrompe o áudio atual e limpa a fila. */
  stop(): void {
    this.clearRepeatTimer();
    this.queue.length = 0;
    if (this.el) {
      this.el.pause();
    }
    this.playing = false;
    this.speaking.set(false);
    this.speakingSessionId.set(null);
  }

  private clearRepeatTimer(): void {
    if (this.repeatTimer !== null) {
      clearTimeout(this.repeatTimer);
      this.repeatTimer = null;
    }
    this.repeating = false;
  }

  private enqueue(frame: JarvisAudioFrame): void {
    this.queue.push(frame);
    if (!this.playing && !this.suppressed) {
      this.playNext();
    }
  }

  private onClipDone(): void {
    if (!this.repeating && this.currentFrame && !this.suppressed) {
      // 1ª vez que este clipe termina: repete uma vez após uma pausa (ajuda a
      // pegar quem não escutou/percebeu de primeira).
      this.repeating = true;
      const frame = this.currentFrame;
      this.repeatTimer = setTimeout(() => {
        this.repeatTimer = null;
        this.playFrame(frame);
      }, REPEAT_DELAY_MS);
      return;
    }
    this.playNext();
  }

  private playNext(): void {
    this.currentFrame = null;
    this.repeating = false;
    if (this.suppressed) {
      this.playing = false;
      this.speaking.set(false);
      this.speakingSessionId.set(null);
      return;
    }
    const frame = this.queue.shift();
    if (!frame) {
      this.playing = false;
      this.speaking.set(false);
      this.speakingSessionId.set(null);
      return;
    }
    this.playFrame(frame);
  }

  private playFrame(frame: JarvisAudioFrame): void {
    const el = this.el;
    if (!el || this.suppressed) {
      this.playing = false;
      this.speaking.set(false);
      this.speakingSessionId.set(null);
      return;
    }
    this.currentFrame = frame;
    this.playing = true;
    this.speaking.set(true);
    this.speakingSessionId.set(frame.session_id ?? null);
    el.volume = this.volume() / 100;
    el.src = `data:${frame.mime || 'audio/ogg'};base64,${frame.audio_b64}`;
    void el.play().catch(() => {
      // Bloqueado mesmo abençoado (raro): exige novo gesto.
      this.unlocked = false;
      this.playing = false;
      this.speaking.set(false);
      this.speakingSessionId.set(null);
    });
  }
}

const STORAGE_KEY = 'sf.jarvis.audio';
const VOLUME_KEY = 'sf.jarvis.volume';
/** Pausa antes de repetir o clipe uma 2ª vez (dá tempo de perceber e ainda
 * reforça caso a pessoa não tenha ouvido de primeira). */
const REPEAT_DELAY_MS = 5000;

function readEnabled(): boolean {
  try {
    // Default LIGADO: se o usuário ativou JARVIS numa sessão, ele quer ouvir.
    return localStorage.getItem(STORAGE_KEY) !== '0';
  } catch {
    return true;
  }
}

/** Volume salvo (0–100), com fallback 100 (sem redução) e clamp. */
function readVolume(): number {
  try {
    const raw = localStorage.getItem(VOLUME_KEY);
    if (raw === null) {
      return 100; // nunca configurado — `Number(null)` daria 0, não confundir
    }
    const v = Number(raw);
    return v >= 0 && v <= 100 ? v : 100;
  } catch {
    return 100;
  }
}

/** WAV silencioso curto (gerado em runtime) p/ "abençoar" o autoplay. */
function buildSilentWav(): string {
  const rate = 8000;
  const n = 400; // ~0.05s
  const buf = new ArrayBuffer(44 + n * 2);
  const dv = new DataView(buf);
  const w = (o: number, s: string) => {
    for (let i = 0; i < s.length; i++) {
      dv.setUint8(o + i, s.charCodeAt(i));
    }
  };
  w(0, 'RIFF');
  dv.setUint32(4, 36 + n * 2, true);
  w(8, 'WAVE');
  w(12, 'fmt ');
  dv.setUint32(16, 16, true);
  dv.setUint16(20, 1, true);
  dv.setUint16(22, 1, true);
  dv.setUint32(24, rate, true);
  dv.setUint32(28, rate * 2, true);
  dv.setUint16(32, 2, true);
  dv.setUint16(34, 16, true);
  w(36, 'data');
  dv.setUint32(40, n * 2, true);
  let bin = '';
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < bytes.length; i++) {
    bin += String.fromCharCode(bytes[i]);
  }
  return 'data:audio/wav;base64,' + btoa(bin);
}

const SILENT_WAV =
  typeof window === 'undefined' ? '' : buildSilentWav();
