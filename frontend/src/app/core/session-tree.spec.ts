import { describe, expect, it } from 'vitest';
import { Session, SessionStatus } from './models';
import { ORPHAN_GROUP_KEY, buildSessionGroups } from './session-tree';

function sess(name: string, parent: string | null = null, status: SessionStatus = 'stopped'): Session {
  return {
    id: 'id-' + name,
    tmux_name: name,
    display_name: name,
    agent_type: 'claude',
    model: null,
    effort: null,
    work_dir: '/tmp',
    status,
    origin: 'test',
    parent,
  } as Session;
}

const names = (nodes: { session: Session; depth: number }[]) =>
  nodes.map((n) => `${n.session.tmux_name}@${n.depth}`);

describe('buildSessionGroups', () => {
  it('raiz simples sem filhos vira um grupo só com ela', () => {
    const groups = buildSessionGroups([sess('a')]);
    expect(groups.length).toBe(1);
    expect(groups[0].key).toBe('a');
    expect(names(groups[0].nodes)).toEqual(['a@0']);
    expect(groups[0].counts).toEqual({ workers: 0, running: 0, waiting: 0, done: 0 });
    expect(groups[0].active).toBe(false);
  });

  it('monta 3 níveis em pré-ordem com recuo', () => {
    const groups = buildSessionGroups([
      sess('neto', 'filho'),
      sess('raiz'),
      sess('filho', 'raiz'),
      sess('filho2', 'raiz'),
    ]);
    expect(groups.length).toBe(1);
    expect(names(groups[0].nodes)).toEqual(['raiz@0', 'filho@1', 'neto@2', 'filho2@1']);
    expect(groups[0].counts.workers).toBe(3);
  });

  it('sessão com pai ausente vai pras "Delegadas sem principal" com a subárvore', () => {
    const groups = buildSessionGroups([
      sess('raiz'),
      sess('orfa', 'sumiu'),
      sess('filho-orfa', 'orfa', 'running'),
    ]);
    expect(groups.map((g) => g.key)).toEqual(['raiz', ORPHAN_GROUP_KEY]);
    const orphans = groups[1];
    expect(orphans.root).toBeNull();
    expect(names(orphans.nodes)).toEqual(['orfa@0', 'filho-orfa@1']);
    expect(orphans.counts.workers).toBe(2);
    expect(orphans.active).toBe(true);
  });

  it('ciclo de parent não trava e não duplica', () => {
    const groups = buildSessionGroups([
      sess('a', 'b'),
      sess('b', 'a'),
      sess('self', 'self'),
      sess('raiz'),
    ]);
    const all = groups.flatMap((g) => g.nodes.map((n) => n.session.tmux_name));
    expect(all.sort()).toEqual(['a', 'b', 'raiz', 'self']);
    expect(groups[groups.length - 1].key).toBe(ORPHAN_GROUP_KEY);
  });

  it('contadores por status e grupo ativo primeiro', () => {
    const groups = buildSessionGroups([
      sess('quieta'),
      sess('chefe', null, 'stopped'),
      sess('w1', 'chefe', 'running'),
      sess('w2', 'chefe', 'waiting_input'),
      sess('w3', 'w2', 'waiting_external'),
      sess('w4', 'chefe', 'completed'),
      sess('w5', 'w1', 'stopped'),
      sess('w6', 'w1', 'error'),
    ]);
    expect(groups.map((g) => g.key)).toEqual(['chefe', 'quieta']);
    expect(groups[0].counts).toEqual({ workers: 6, running: 1, waiting: 2, done: 2 });
    expect(groups[0].active).toBe(true);
    expect(groups[1].active).toBe(false);
  });
});
