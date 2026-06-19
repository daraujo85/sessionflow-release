/**
 * Tempo relativo curto em pt-BR para "última atividade há X".
 *
 * Entrada: timestamp ISO (ou null). Saída compacta: "agora", "há 5 min",
 * "há 2 h", "há 3 d", "há 2 sem". Robusto a relógio adiantado (futuro -> "agora").
 */
export function timeAgo(iso: string | null | undefined, nowMs?: number): string {
  if (!iso) {
    return '';
  }
  const then = Date.parse(iso);
  if (Number.isNaN(then)) {
    return '';
  }
  const now = nowMs ?? Date.now();
  const sec = Math.floor((now - then) / 1000);
  if (sec < 45) {
    return 'agora';
  }
  const min = Math.floor(sec / 60);
  if (min < 60) {
    return `há ${min} min`;
  }
  const hr = Math.floor(min / 60);
  if (hr < 24) {
    return `há ${hr} h`;
  }
  const day = Math.floor(hr / 24);
  if (day < 7) {
    return `há ${day} d`;
  }
  const wk = Math.floor(day / 7);
  if (wk < 5) {
    return `há ${wk} sem`;
  }
  const mo = Math.floor(day / 30);
  if (mo < 12) {
    return `há ${mo} ${mo === 1 ? 'mês' : 'meses'}`;
  }
  return `há ${Math.floor(day / 365)} a`;
}
