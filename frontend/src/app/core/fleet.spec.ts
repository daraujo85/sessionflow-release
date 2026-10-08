import { describe, expect, it } from 'vitest';
import {
  FLEET_BUSY_UPTO,
  FLEET_FREE_BELOW,
  activeSessionsByHost,
  countBands,
  cpuPct,
  fleetView,
  loadBand,
  memUsedPct,
  sortFleet,
} from './fleet';
import { WorkerStatus } from './models';

function host(id: string, cpu: number | null, online = true, extra: Partial<WorkerStatus> = {}): WorkerStatus {
  return {
    host_id: id,
    hostname: id,
    online,
    uptime_seconds: null,
    started_at: null,
    updated_at: null,
    metrics:
      cpu === null ? null : { cpu_pct: cpu, mem_pct: null, mem_total_gb: null, mem_avail_gb: null },
    ...extra,
  };
}

describe('fleet', () => {
  it('limiares nomeados', () => {
    expect(FLEET_FREE_BELOW).toBe(40);
    expect(FLEET_BUSY_UPTO).toBe(80);
  });

  it('faixas nos limites 40 e 80', () => {
    expect(loadBand(host('a', 0))).toBe('free');
    expect(loadBand(host('a', 39))).toBe('free');
    expect(loadBand(host('a', 40))).toBe('busy');
    expect(loadBand(host('a', 80))).toBe('busy');
    expect(loadBand(host('a', 81))).toBe('overloaded');
    expect(loadBand(host('a', 100))).toBe('overloaded');
  });

  it('faixa usa a CPU arredondada (a mesma exibida)', () => {
    expect(cpuPct(host('a', 39.6))).toBe(40);
    expect(loadBand(host('a', 39.6))).toBe('busy');
    expect(loadBand(host('a', 39.4))).toBe('free');
    expect(cpuPct(host('a', 79.6))).toBe(80);
    expect(loadBand(host('a', 79.6))).toBe('busy');
    expect(cpuPct(host('a', 80.4))).toBe(80);
    expect(loadBand(host('a', 80.4))).toBe('busy');
    expect(loadBand(host('a', 80.5))).toBe('overloaded');
    expect(cpuPct(host('a', 104))).toBe(100);
    expect(cpuPct(host('a', null))).toBeNull();
  });

  it('host sem metrics online = unknown; offline vence metrics', () => {
    expect(loadBand(host('a', null))).toBe('unknown');
    expect(loadBand(host('a', 95, false))).toBe('offline');
    expect(loadBand(host('a', null, false))).toBe('offline');
  });

  it('ordena por CPU desc, sem metrics depois, offline no fim', () => {
    const list = [
      host('off', 99, false),
      host('low', 10),
      host('nometrics', null),
      host('high', 90),
      host('mid', 50),
    ];
    expect(sortFleet(list).map((w) => w.host_id)).toEqual(['high', 'mid', 'low', 'nometrics', 'off']);
    expect(list[0].host_id).toBe('off'); // não muta
  });

  it('empate de CPU desempata por nome', () => {
    expect(sortFleet([host('b', 20), host('a', 20)]).map((w) => w.host_id)).toEqual(['a', 'b']);
  });

  it('conta faixas e filtra', () => {
    const list = [host('a', 10), host('b', 40), host('c', 85), host('d', 5, false), host('e', null)];
    expect(countBands(list)).toEqual({ free: 1, busy: 1, overloaded: 1, offline: 1 });
    expect(fleetView(list, 'overloaded').map((w) => w.host_id)).toEqual(['c']);
    expect(fleetView(list, null)).toHaveLength(5);
  });

  it('RAM usada: mem_pct ou derivada de total/livre', () => {
    expect(memUsedPct(host('a', null))).toBeNull();
    const m = { cpu_pct: 1, mem_pct: null, mem_total_gb: 16, mem_avail_gb: 4 };
    expect(memUsedPct({ metrics: m })).toBe(75);
    expect(memUsedPct({ metrics: { ...m, mem_pct: 33 } })).toBe(33);
  });

  it('agrupa sessões ativas por host', () => {
    const map = activeSessionsByHost([
      { host_id: 'h1', status: 'running', display_name: 'api', tmux_name: 't1' },
      { host_id: 'h1', status: 'waiting_input', display_name: '', tmux_name: 't2' },
      { host_id: 'h1', status: 'completed', display_name: 'velha', tmux_name: 't3' },
      { host_id: null, status: 'running', display_name: 'sem host', tmux_name: 't4' },
    ]);
    expect(map.get('h1')).toEqual(['api', 't2']);
    expect(map.size).toBe(1);
  });
});
