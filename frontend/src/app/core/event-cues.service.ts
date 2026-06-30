import { Injectable, effect, inject, signal } from '@angular/core';
import { EventItem } from './models';
import { SseService, isOutputLine } from './sse.service';

/** Modo dos avisos de evento: silencioso, chime sutil ou frase falada (PT-BR). */
export type CueMode = 'off' | 'chime' | 'voice';

/** localStorage key para persistir o modo escolhido pelo usuário. */
const STORAGE_KEY = 'sf.cues.mode';

/** Categoria de cue derivada do evento de ciclo de vida da sessão. */
type CueKind = 'created' | 'completed' | 'attention' | 'stopped' | 'error';

/** Frases curtas (PT-BR) faladas no modo "voice", por categoria de cue. */
const PHRASES: Record<CueKind, string> = {
  created: 'Sessão iniciada',
  completed: 'Sessão concluída',
  attention: 'Aguardando sua resposta',
  stopped: 'Sessão encerrada',
  error: 'Erro na sessão',
};

/**
 * Avisos de evento DISCRETOS para o ciclo de vida das sessões.
 *
 * Separado do {@link JarvisAudioService} (resumos ricos falados): aqui só damos
 * um sinal SUTIL e moderno — um chime suave (default) ou uma frase curta falada.
 * O usuário liga/desliga globalmente no Perfil ({@link mode}).
 *
 * - Os chimes são sintetizados via Web Audio API (sem arquivos), curtos
 *   (≤350ms), volume baixo e com attack/release suaves (sem cliques).
 * - Um único {@link AudioContext} é criado preguiçosamente e "destravado" no
 *   primeiro gesto do usuário (política de autoplay), igual ao JarvisAudioService.
 * - No modo "voice" usamos `speechSynthesis` (voz pt-BR se houver).
 *
 * Instanciado uma vez (root) e ligado via {@link init} no bootstrap do app.
 */
@Injectable({ providedIn: 'root' })
export class EventCuesService {
  private readonly sse = inject(SseService);

  /** Modo atual, persistido em localStorage (default 'chime'). */
  readonly mode = signal<CueMode>(readMode());

  /** Ids de eventos já tocados (evita repetir em reconexões do SSE). */
  private readonly seen = new Set<string>();

  private ctx: AudioContext | null = null;
  private unlocked = false;
  private started = false;

  /** Liga o efeito no SSE + o destravamento de autoplay por gesto. */
  init(): void {
    if (this.started || typeof window === 'undefined') {
      return;
    }
    this.started = true;

    const unlock = () => {
      this.ensureContext();
      void this.ctx?.resume().then(() => {
        this.unlocked = true;
      });
    };
    window.addEventListener('pointerdown', unlock, { once: true });
    window.addEventListener('keydown', unlock, { once: true });
    window.addEventListener('touchstart', unlock, { once: true });

    effect(() => {
      const frame = this.sse.lastEvent();
      if (!frame || isOutputLine(frame)) {
        return;
      }
      if (this.mode() === 'off') {
        return;
      }
      // Dedup por id (o mesmo evento não deve tocar 2x em reconexão).
      const id = frame.id;
      if (id) {
        if (this.seen.has(id)) {
          return;
        }
        this.seen.add(id);
      }
      // Sessão com o alto-falante OFF (jarvis=false no evento) não toca chime —
      // alinha com o botão de alto-falante por sessão. (undefined = não informado
      // → mantém o comportamento padrão de tocar.)
      if (frame.jarvis === false) {
        return;
      }
      const cue = classify(frame);
      if (!cue) {
        return;
      }
      this.play(cue);
    });
  }

  /** Define o modo e persiste. */
  setMode(m: CueMode): void {
    this.mode.set(m);
    try {
      localStorage.setItem(STORAGE_KEY, m);
    } catch {
      /* storage indisponível — silencioso */
    }
  }

  private play(cue: CueKind): void {
    if (this.mode() === 'voice') {
      this.speak(PHRASES[cue]);
      return;
    }
    this.chime(cue);
  }

  // ── Voz (PT-BR) ──────────────────────────────────────────────────────────

  private speak(text: string): void {
    if (typeof speechSynthesis === 'undefined' || typeof SpeechSynthesisUtterance === 'undefined') {
      return;
    }
    try {
      // Cancela qualquer fala em andamento antes da nova (evita acúmulo).
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.lang = 'pt-BR';
      u.rate = 1.05;
      const voice = speechSynthesis
        .getVoices()
        .find((v) => /pt[-_]?BR/i.test(v.lang)) ??
        speechSynthesis.getVoices().find((v) => /^pt/i.test(v.lang));
      if (voice) {
        u.voice = voice;
      }
      speechSynthesis.speak(u);
    } catch {
      /* TTS indisponível — silencioso */
    }
  }

  // ── Chimes sintetizados (Web Audio) ──────────────────────────────────────

  private ensureContext(): void {
    if (this.ctx) {
      return;
    }
    const Ctor =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (Ctor) {
      this.ctx = new Ctor();
    }
  }

  private chime(cue: CueKind): void {
    this.ensureContext();
    const ctx = this.ctx;
    if (!ctx) {
      return;
    }
    // Se ainda suspenso (gesto não destravou), tenta retomar best-effort.
    if (ctx.state === 'suspended') {
      void ctx.resume();
    }
    const now = ctx.currentTime;

    switch (cue) {
      case 'completed':
        // 2 notas ASCENDENTES — agradável (concluído).
        this.tone(659.25, now, 0.16, 'sine'); // E5
        this.tone(987.77, now + 0.13, 0.18, 'sine'); // B5
        break;
      case 'attention':
        // "ping" duplo suave — precisa de você.
        this.tone(880, now, 0.1, 'triangle'); // A5
        this.tone(880, now + 0.16, 0.12, 'triangle');
        break;
      case 'stopped':
        // Tom grave/descendente suave — encerrado.
        this.tone(440, now, 0.16, 'sine'); // A4
        this.tone(329.63, now + 0.12, 0.2, 'sine'); // E4
        break;
      case 'error':
        // Descendente um pouco mais grave — erro.
        this.tone(392, now, 0.16, 'sine'); // G4
        this.tone(261.63, now + 0.12, 0.2, 'sine'); // C4
        break;
      case 'created':
        // Nota única bem sutil — iniciada.
        this.tone(587.33, now, 0.14, 'sine'); // D5
        break;
    }
  }

  /**
   * Toca uma nota curta com envelope suave (attack/release) p/ evitar cliques.
   * Volume baixo (premium/discreto). `dur` ≤ ~0.2s mantém o cue ≤350ms.
   */
  private tone(
    freq: number,
    start: number,
    dur: number,
    type: OscillatorType,
  ): void {
    const ctx = this.ctx;
    if (!ctx) {
      return;
    }
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = type;
    osc.frequency.value = freq;

    const peak = 0.09; // gain baixo (~0.06–0.12) — discreto
    const attack = 0.012;
    const release = Math.max(0.04, dur - attack);
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(peak, start + attack);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + attack + release);

    osc.connect(gain).connect(ctx.destination);
    osc.start(start);
    osc.stop(start + attack + release + 0.02);
  }
}

/** Lê o modo persistido; default 'chime'. */
function readMode(): CueMode {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === 'off' || v === 'chime' || v === 'voice') {
      return v;
    }
  } catch {
    /* ignore */
  }
  return 'chime';
}

/** Mapeia um evento estruturado para a categoria de cue, ou null se irrelevante. */
function classify(e: EventItem): CueKind | null {
  const type = (e.type || '').toLowerCase();
  const kind = (e.kind || '').toLowerCase();

  if (type === 'completed' || kind === 'success') {
    return 'completed';
  }
  if (type === 'attention' || type === 'waiting' || kind === 'attention') {
    return 'attention';
  }
  if (type === 'error') {
    return 'error';
  }
  if (type === 'stopped') {
    return 'stopped';
  }
  if (type === 'created') {
    return 'created';
  }
  return null;
}
