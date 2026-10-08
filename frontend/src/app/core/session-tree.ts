import { Session, SessionStatus } from './models';

/** Uma sessão posicionada na árvore (pré-ordem), com o nível de recuo. */
export interface SessionTreeNode {
  session: Session;
  /** 0 = raiz do grupo; 1 = filho; 2 = neto... */
  depth: number;
}

/** Contadores do cabeçalho do grupo (sobre os DESCENDENTES, sem a raiz). */
export interface SessionGroupCounts {
  /** Descendentes totais (todos os níveis). */
  workers: number;
  running: number;
  /** waiting_input + waiting_external. */
  waiting: number;
  /** completed + stopped (status final). */
  done: number;
}

export interface SessionGroup {
  /** tmux_name da raiz, ou ORPHAN_GROUP_KEY p/ "Delegadas sem principal". */
  key: string;
  /** Sessão raiz; null no grupo de órfãs. */
  root: Session | null;
  /** Nós em pré-ordem (raiz primeiro, depth 0). */
  nodes: SessionTreeNode[];
  counts: SessionGroupCounts;
  /** Raiz ou algum descendente rodando/aguardando → grupo abre sozinho. */
  active: boolean;
}

export const ORPHAN_GROUP_KEY = '__orphans__';

const RUNNING: readonly SessionStatus[] = ['running'];
const WAITING: readonly SessionStatus[] = ['waiting_input', 'waiting_external'];
const DONE: readonly SessionStatus[] = ['completed', 'stopped'];

export function isActiveStatus(st: SessionStatus): boolean {
  return RUNNING.includes(st) || WAITING.includes(st);
}

function keyOf(s: Session): string {
  return s.tmux_name || s.id;
}

function countNodes(nodes: SessionTreeNode[], skipDepth0: boolean): SessionGroupCounts {
  const c: SessionGroupCounts = { workers: 0, running: 0, waiting: 0, done: 0 };
  for (const n of nodes) {
    if (skipDepth0 && n.depth === 0) {
      continue;
    }
    c.workers++;
    if (RUNNING.includes(n.session.status)) {
      c.running++;
    } else if (WAITING.includes(n.session.status)) {
      c.waiting++;
    } else if (DONE.includes(n.session.status)) {
      c.done++;
    }
  }
  return c;
}

/**
 * Agrupa sessões pela sessão PRINCIPAL (raiz da cadeia de `parent`).
 *
 * - Raiz = sessão sem `parent`. Cada raiz vira um grupo com toda a sua
 *   descendência (N níveis) em pré-ordem.
 * - Sessões cujo `parent` não está na lista (pai sumiu) vão, com a própria
 *   subárvore, pro grupo "Delegadas sem principal" (último da lista).
 * - Ciclos de `parent` (a→b→a) não travam: cada sessão é visitada uma vez;
 *   membros de ciclo inalcançáveis por uma raiz também caem nas órfãs.
 *
 * Ordem: grupos ativos primeiro; dentro disso, ordem de entrada (estável).
 */
export function buildSessionGroups(sessions: readonly Session[]): SessionGroup[] {
  const byKey = new Map<string, Session>();
  for (const s of sessions) {
    byKey.set(keyOf(s), s);
  }
  const children = new Map<string, Session[]>();
  for (const s of sessions) {
    const p = s.parent;
    if (p && byKey.has(p) && p !== keyOf(s)) {
      const list = children.get(p) ?? [];
      list.push(s);
      children.set(p, list);
    }
  }

  const visited = new Set<string>();
  const walk = (s: Session, depth: number, out: SessionTreeNode[]): void => {
    const k = keyOf(s);
    if (visited.has(k)) {
      return;
    }
    visited.add(k);
    out.push({ session: s, depth });
    for (const c of children.get(k) ?? []) {
      walk(c, depth + 1, out);
    }
  };

  const groups: SessionGroup[] = [];
  for (const s of sessions) {
    if (s.parent) {
      continue;
    }
    const nodes: SessionTreeNode[] = [];
    walk(s, 0, nodes);
    if (!nodes.length) {
      continue;
    }
    groups.push({
      key: keyOf(s),
      root: s,
      nodes,
      counts: countNodes(nodes, true),
      active: nodes.some((n) => isActiveStatus(n.session.status)),
    });
  }

  // Órfãs: pai ausente primeiro; depois o que sobrou (ciclos sem raiz).
  const orphanNodes: SessionTreeNode[] = [];
  for (const s of sessions) {
    if (s.parent && !byKey.has(s.parent)) {
      walk(s, 0, orphanNodes);
    }
  }
  for (const s of sessions) {
    walk(s, 0, orphanNodes);
  }

  const sorted = [
    ...groups.filter((g) => g.active),
    ...groups.filter((g) => !g.active),
  ];
  if (orphanNodes.length) {
    sorted.push({
      key: ORPHAN_GROUP_KEY,
      root: null,
      nodes: orphanNodes,
      counts: countNodes(orphanNodes, false),
      active: orphanNodes.some((n) => isActiveStatus(n.session.status)),
    });
  }
  return sorted;
}
