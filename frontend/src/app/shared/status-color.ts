import { SessionStatus, AgentType } from '../core/models';

/** UI metadata for a session lifecycle state (label + colors from the mockup). */
export interface StatusMeta {
  /** PT-BR label shown to the user. */
  label: string;
  /** Foreground/accent color for the state. */
  color: string;
  /** Status dot color. */
  dot: string;
}

/** Maps each session status to its display metadata. */
export const STATUS_META: Record<SessionStatus, StatusMeta> = {
  running: { label: 'Executando', color: '#34D399', dot: '#34D399' },
  waiting_input: { label: 'Aguardando sua decisão', color: '#FBBF24', dot: '#FBBF24' },
  waiting_external: { label: 'Aguardando externo', color: '#FB923C', dot: '#FB923C' },
  completed: { label: 'Concluído', color: '#34D399', dot: '#34D399' },
  error: { label: 'Erro', color: '#F87171', dot: '#F87171' },
  stopped: { label: 'Encerrada', color: '#6B7280', dot: '#6B7280' },
  detached: { label: 'Detached', color: '#9AA0AE', dot: '#9AA0AE' },
};

/** Returns the color for a given session status (falls back to detached). */
export function statusColor(status: SessionStatus): string {
  return (STATUS_META[status] ?? STATUS_META.detached).color;
}

/** UI metadata for an agent backend. */
export interface AgentMeta {
  /** Full human-readable label. */
  label: string;
  /** Short badge text (e.g. "CC"). */
  short: string;
  /** Brand color. */
  color: string;
  /** CLI command name. */
  cmd: string;
}

/** Maps each agent type to its display metadata (colors from the mockup). */
export const AGENT_META: Record<AgentType, AgentMeta> = {
  claude: { label: 'Claude Code', short: 'CC', color: '#D97757', cmd: 'claude' },
  codex: { label: 'Codex', short: 'Cx', color: '#10A37F', cmd: 'codex' },
  gemini: { label: 'Gemini CLI', short: 'G', color: '#4796E3', cmd: 'gemini' },
  opencode: { label: 'OpenCode', short: 'OC', color: '#06B6D4', cmd: 'opencode' },
  desconhecido: { label: 'Desconhecido', short: '?', color: '#8A90A0', cmd: '' },
};

/** Returns metadata for an agent type (falls back to "desconhecido"). */
export function agentMeta(agent: AgentType): AgentMeta {
  return AGENT_META[agent] ?? AGENT_META.desconhecido;
}

/**
 * Heurística: a sessão é um worker / sub-agente, pela convenção de nome
 * (``worker-*`` / ``sub-*``), usada por automações que spawnam outros agentes.
 */
export function isWorkerSession(name: string | null | undefined): boolean {
  return !!name && /^(worker|sub)[-_]/i.test(name);
}
