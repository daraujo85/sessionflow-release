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
  /** Áudio tocando agora (para um indicador visual, se quiser). */
  readonly speaking = signal(false);

  private readonly queue: JarvisAudioFrame[] = [];
  /** Um único elemento, "abençoado" no 1º gesto e reusado p/ todos os clipes. */
  private el: HTMLAudioElement | null = null;
  private playing = false;
  private unlocked = false;
  /** Enquanto o usuário grava áudio, segura a reprodução (evita vazar no mic). */
  private suppressed = false;
  private pending: JarvisAudioFrame | null = null;
  private lastAt: string | null = null;
  private started = false;

  /** Liga o pipeline: efeito no signal de SSE + desbloqueio por gesto. */
  init(): void {
    if (this.started || typeof window === 'undefined') {
      return;
    }
    this.started = true;
    this.el = new Audio();
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
      if (this.el) {
        this.el.pause();
      }
      this.playing = false;
      this.speaking.set(false);
    } else if (this.queue.length > 0 && !this.playing) {
      this.playNext();
    }
  }

  /** Interrompe o áudio atual e limpa a fila. */
  stop(): void {
    this.queue.length = 0;
    if (this.el) {
      this.el.pause();
    }
    this.playing = false;
    this.speaking.set(false);
  }

  private enqueue(frame: JarvisAudioFrame): void {
    this.queue.push(frame);
    if (!this.playing && !this.suppressed) {
      this.playNext();
    }
  }

  private onClipDone(): void {
    this.playNext();
  }

  private playNext(): void {
    if (this.suppressed) {
      this.playing = false;
      this.speaking.set(false);
      return;
    }
    const frame = this.queue.shift();
    const el = this.el;
    if (!frame || !el) {
      this.playing = false;
      this.speaking.set(false);
      return;
    }
    this.playing = true;
    this.speaking.set(true);
    el.src = `data:${frame.mime || 'audio/ogg'};base64,${frame.audio_b64}`;
    void el.play().catch(() => {
      // Bloqueado mesmo abençoado (raro): exige novo gesto.
      this.unlocked = false;
      this.playing = false;
      this.speaking.set(false);
    });
  }
}

const STORAGE_KEY = 'sf.jarvis.audio';

function readEnabled(): boolean {
  try {
    // Default LIGADO: se o usuário ativou JARVIS numa sessão, ele quer ouvir.
    return localStorage.getItem(STORAGE_KEY) !== '0';
  } catch {
    return true;
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
