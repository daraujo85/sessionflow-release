import { Session, SessionStatus, WorkerStatus } from './models';

/** CPU% abaixo disso = máquina "livre". */
export const FLEET_FREE_BELOW = 40;
/** CPU% até isso (inclusive) = "pegada"; acima = "sobrecarregada". */
export const FLEET_BUSY_UPTO = 80;

/** Status de sessão que contam como "ativa" numa máquina (mesmo critério do
 * "Ativas agora" do Perfil, pra soma dos chips bater com ele). */
export const FLEET_ACTIVE_STATUSES: readonly SessionStatus[] = ['running', 'waiting_input'];

/** Faixa de carga de um host. `unknown` = online mas sem métricas (worker antigo). */
export type LoadBand = 'free' | 'busy' | 'overloaded' | 'offline' | 'unknown';

/** Faixas que viram chip de filtro (unknown não tem chip). */
export const FLEET_FILTER_BANDS = ['free', 'busy', 'overloaded', 'offline'] as const;
export type FleetFilter = (typeof FLEET_FILTER_BANDS)[number];

/** Percentual inteiro em 0–100 (o mesmo número que a tela mostra). */
export function roundPct(v: number | null | undefined): number | null {
  return v == null ? null : Math.max(0, Math.min(100, Math.round(v)));
}

/** CPU% arredondada — fonte única pra faixa E pro número exibido, senão
 * 39.6 apareceria "40%" pintado de livre. */
export function cpuPct(w: Pick<WorkerStatus, 'metrics'>): number | null {
  return roundPct(w.metrics?.cpu_pct);
}

/** Classifica um host pela CPU% do heartbeat (offline vem do `online` da API,
 * que já aplica o corte de heartbeat > 90s). */
export function loadBand(w: Pick<WorkerStatus, 'online' | 'metrics'>): LoadBand {
  if (!w.online) return 'offline';
  const cpu = cpuPct(w);
  if (cpu == null) return 'unknown';
  if (cpu < FLEET_FREE_BELOW) return 'free';
  if (cpu <= FLEET_BUSY_UPTO) return 'busy';
  return 'overloaded';
}

/** RAM usada em % — `mem_pct` se vier, senão derivada de total/livre. */
export function memUsedPct(w: Pick<WorkerStatus, 'metrics'>): number | null {
  const m = w.metrics;
  if (!m) return null;
  if (m.mem_pct != null) return m.mem_pct;
  if (m.mem_total_gb && m.mem_avail_gb != null) {
    return ((m.mem_total_gb - m.mem_avail_gb) / m.mem_total_gb) * 100;
  }
  return null;
}

function hostLabel(w: WorkerStatus): string {
  return (w.display_name || w.hostname || w.host_id || '').toLowerCase();
}

/** Ordena por CPU% desc; online sem métricas depois dos com métricas;
 * offline sempre no fim. Empate → nome. Não muta a entrada. */
export function sortFleet(list: readonly WorkerStatus[]): WorkerStatus[] {
  const rank = (w: WorkerStatus) => (!w.online ? 2 : w.metrics?.cpu_pct == null ? 1 : 0);
  return [...list].sort((a, b) => {
    const r = rank(a) - rank(b);
    if (r) return r;
    const cpu = (cpuPct(b) ?? 0) - (cpuPct(a) ?? 0);
    if (cpu) return cpu;
    return hostLabel(a).localeCompare(hostLabel(b));
  });
}

/** Quantos hosts em cada faixa com chip. */
export function countBands(list: readonly WorkerStatus[]): Record<FleetFilter, number> {
  const out: Record<FleetFilter, number> = { free: 0, busy: 0, overloaded: 0, offline: 0 };
  for (const w of list) {
    const b = loadBand(w);
    if (b !== 'unknown') out[b]++;
  }
  return out;
}

/** Ordena e aplica o filtro de faixa (`null` = todos). */
export function fleetView(
  list: readonly WorkerStatus[],
  filter: FleetFilter | null,
): WorkerStatus[] {
  const sorted = sortFleet(list);
  return filter ? sorted.filter((w) => loadBand(w) === filter) : sorted;
}

/** Nomes das sessões ativas agrupados por host_id. */
export function activeSessionsByHost(
  sessions: readonly Pick<Session, 'host_id' | 'status' | 'display_name' | 'tmux_name'>[],
): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const s of sessions) {
    if (!s.host_id || !FLEET_ACTIVE_STATUSES.includes(s.status)) continue;
    const names = out.get(s.host_id) ?? [];
    names.push(s.display_name || s.tmux_name);
    out.set(s.host_id, names);
  }
  return out;
}
