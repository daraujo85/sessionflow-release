/**
 * Mapeia KeyboardEvent para TerminalKey (allowlist espelhada no backend).
 *
 * Stub mínimo — a feature de Meeting Runtime (que tinha o mapeamento mais
 * completo) vive agora em repositório separado (sessionflow-meeting-runtime).
 * Aqui mantemos só o suficiente para os atalhos de teclado do detalhe/session-
 * panel que ainda dependem do recurso.
 *
 * Adicione entradas aqui se algum atalho novo for necessário no app principal.
 */
import type { TerminalKey } from './models';

export function keyToTerminalKey(ev: KeyboardEvent): TerminalKey | null {
  if (ev.key === 'ArrowUp') return 'up';
  if (ev.key === 'ArrowDown') return 'down';
  if (ev.key === 'ArrowLeft') return 'left';
  if (ev.key === 'ArrowRight') return 'right';
  if (ev.key === 'Enter') return 'enter';
  if (ev.key === ' ') return 'space';
  if (ev.key === 'Escape') return 'escape';
  if (ev.key === 'Tab') return ev.shiftKey ? 'shift-tab' : 'tab';
  if (ev.key === 'Backspace') return 'backspace';
  if (ev.ctrlKey && (ev.key === 'c' || ev.key === 'C')) return 'ctrl-c';
  if (ev.key === 'PageUp') return 'scroll-up';
  if (ev.key === 'PageDown') return 'scroll-down';
  if (ev.key === 'End' && (ev.ctrlKey || ev.metaKey)) return 'scroll-bottom';
  return null;
}