/**
 * Conversor ANSI (SGR) → HTML para o espelho do terminal. O Worker captura a
 * tela com `tmux capture-pane -e`, preservando os códigos de cor/atributo; aqui
 * traduzimos esses códigos para `<span style>` reproduzindo as cores do
 * terminal real no navegador.
 *
 * O texto é SEMPRE escapado antes de virar HTML (a saída do agente é não
 * confiável) — só os spans/estilos que NÓS geramos são adicionados.
 */

/** Paleta dos 16 ANSI (0–7 normal, 8–15 bright), afinada ao tema dark. */
const PALETTE_16: readonly string[] = [
  '#3b3f45', // 0 black
  '#f87171', // 1 red
  '#34d399', // 2 green
  '#fbbf24', // 3 yellow
  '#60a5fa', // 4 blue
  '#c084fc', // 5 magenta
  '#22d3ee', // 6 cyan
  '#d4d4d4', // 7 white
  '#6b7280', // 8 bright black (gray)
  '#fca5a5', // 9 bright red
  '#6ee7b7', // 10 bright green
  '#fde047', // 11 bright yellow
  '#93c5fd', // 12 bright blue
  '#d8b4fe', // 13 bright magenta
  '#67e8f9', // 14 bright cyan
  '#f4f5f7', // 15 bright white
];

interface SgrState {
  fg: string | null;
  bg: string | null;
  bold: boolean;
  dim: boolean;
  italic: boolean;
  underline: boolean;
  inverse: boolean;
}

function emptyState(): SgrState {
  return {
    fg: null,
    bg: null,
    bold: false,
    dim: false,
    italic: false,
    underline: false,
    inverse: false,
  };
}

/** Cor da paleta 256 (xterm) → hex. */
function color256(n: number): string {
  if (n < 16) {
    return PALETTE_16[n];
  }
  if (n >= 232) {
    // Escala de cinza 232–255.
    const v = 8 + (n - 232) * 10;
    return rgb(v, v, v);
  }
  // Cubo 6x6x6 (16–231).
  const i = n - 16;
  const r = Math.floor(i / 36);
  const g = Math.floor((i % 36) / 6);
  const b = i % 6;
  const ch = (c: number) => (c === 0 ? 0 : 55 + c * 40);
  return rgb(ch(r), ch(g), ch(b));
}

function rgb(r: number, g: number, b: number): string {
  return `rgb(${r},${g},${b})`;
}

/** Aplica uma sequência de parâmetros SGR ao estado corrente. */
function applySgr(params: number[], st: SgrState): void {
  for (let i = 0; i < params.length; i++) {
    const p = params[i];
    switch (true) {
      case p === 0:
        Object.assign(st, emptyState());
        break;
      case p === 1:
        st.bold = true;
        break;
      case p === 2:
        st.dim = true;
        break;
      case p === 3:
        st.italic = true;
        break;
      case p === 4:
        st.underline = true;
        break;
      case p === 7:
        st.inverse = true;
        break;
      case p === 22:
        st.bold = false;
        st.dim = false;
        break;
      case p === 23:
        st.italic = false;
        break;
      case p === 24:
        st.underline = false;
        break;
      case p === 27:
        st.inverse = false;
        break;
      case p >= 30 && p <= 37:
        st.fg = PALETTE_16[p - 30];
        break;
      case p === 39:
        st.fg = null;
        break;
      case p >= 40 && p <= 47:
        st.bg = PALETTE_16[p - 40];
        break;
      case p === 49:
        st.bg = null;
        break;
      case p >= 90 && p <= 97:
        st.fg = PALETTE_16[p - 90 + 8];
        break;
      case p >= 100 && p <= 107:
        st.bg = PALETTE_16[p - 100 + 8];
        break;
      case p === 38 || p === 48: {
        // Cor estendida: 38;5;n (256) ou 38;2;r;g;b (truecolor).
        const mode = params[i + 1];
        if (mode === 5) {
          const c = color256(params[i + 2] ?? 0);
          if (p === 38) st.fg = c;
          else st.bg = c;
          i += 2;
        } else if (mode === 2) {
          const c = rgb(params[i + 2] ?? 0, params[i + 3] ?? 0, params[i + 4] ?? 0);
          if (p === 38) st.fg = c;
          else st.bg = c;
          i += 4;
        }
        break;
      }
      default:
        break;
    }
  }
}

/** Estilo CSS inline a partir do estado SGR (ou '' se nada a aplicar). */
function styleFor(st: SgrState): string {
  const parts: string[] = [];
  let fg = st.fg;
  let bg = st.bg;
  if (st.inverse) {
    // Inverte fg/bg (padrões do tema quando null).
    fg = st.bg ?? '#0e1113';
    bg = st.fg ?? '#d4d4d4';
  }
  if (fg) parts.push(`color:${fg}`);
  if (bg) parts.push(`background:${bg}`);
  if (st.bold) parts.push('font-weight:600');
  if (st.dim) parts.push('opacity:.6');
  if (st.italic) parts.push('font-style:italic');
  if (st.underline) parts.push('text-decoration:underline');
  return parts.join(';');
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

// CSI SGR: ESC [ <params> m
const SGR_RE = /\x1b\[([0-9;]*)m/g;

/**
 * Converte uma string com escapes SGR em HTML seguro (texto escapado +
 * `<span style>` para cor/atributo). Sequências não-SGR já vêm removidas pelo
 * Worker; qualquer ESC residual é escapado como texto.
 */
export function ansiToHtml(input: string): string {
  if (!input) {
    return '';
  }
  const st = emptyState();
  let out = '';
  let last = 0;
  SGR_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  const flush = (text: string) => {
    if (!text) return;
    const style = styleFor(st);
    const safe = escapeHtml(text);
    out += style ? `<span style="${style}">${safe}</span>` : safe;
  };
  while ((m = SGR_RE.exec(input)) !== null) {
    flush(input.slice(last, m.index));
    const params = m[1] === '' ? [0] : m[1].split(';').map((x) => parseInt(x, 10) || 0);
    applySgr(params, st);
    last = SGR_RE.lastIndex;
  }
  flush(input.slice(last));
  return out;
}
