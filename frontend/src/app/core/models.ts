/** The seven possible lifecycle states of a session (alinhado ao Worker/mockup). */
export type SessionStatus =
  | 'running'
  | 'waiting_input'
  | 'waiting_external'
  | 'completed'
  | 'error'
  | 'stopped'
  | 'detached';

/** Estados de tarefa (mockup). */
export type TaskState = 'todo' | 'doing' | 'blocked' | 'done' | 'attention';

/** Categorias de notificação/evento (cores do mockup). */
export type EventKind = 'attention' | 'info' | 'warning' | 'success';

/** Supported agent backends. */
export type AgentType =
  | 'claude'
  | 'codex'
  | 'gemini'
  | 'opencode'
  | 'desconhecido';

/**
 * Métricas reais da sessão, enriquecidas pelo backend (atualmente só p/
 * sessões claude). Limites diário/semanal NÃO vêm — não há fonte.
 */
export interface SessionMetrics {
  model: string | null;
  context_used: number;
  context_max: number;
  context_pct: number;
  tokens_in: number;
  tokens_out: number;
  source: string;
  activity?: {
    today_messages: number;
    today_tools: number;
    today_date: string | null;
    week_messages: number;
    week_tools: number;
  } | null;
  /** % real do limite de uso (sessão 5h + semanal). Só p/ sessões claude com dado. */
  limits?: {
    session_pct: number;
    session_reset: string;
    week_pct: number;
    week_reset: string;
  } | null;
}

export interface Session {
  id: string;
  tmux_name: string;
  display_name: string;
  agent_type: AgentType;
  model: string | null;
  effort: string | null;
  work_dir: string;
  status: SessionStatus;
  /** Rótulo fino do que o agente está fazendo (worker, só p/ sessões running). */
  activity?: string;
  origin: string;
  favorite?: boolean;
  /** JARVIS: resumo falado da sessão (voz no celular) ligado p/ esta sessão. */
  jarvis?: boolean;
  metrics?: SessionMetrics | null;
  [key: string]: unknown;
}

export interface EventItem {
  id: string;
  session_id: string | null;
  type: string;
  kind: EventKind;
  title: string;
  desc: string;
  at: string;
}

/** A notification has the same shape as an event item. */
export type Notification = EventItem;

export interface Directory {
  path: string;
  parent: string;
  name: string;
  root: string;
}

export interface Task {
  id: string;
  session_id: string;
  title: string;
  state: TaskState;
}

export interface OutputLine {
  id: string;
  seq: number;
  text: string;
  line_type: string;
  at: string;
}

/** A single model option for an agent (vindo de `GET /models`). */
export interface AgentModel {
  id: string;
  label: string;
  description?: string;
  is_default?: boolean;
}

/** Modelos disponíveis para um agente (envelope item de `GET /models`). */
export interface AgentModels {
  agent: AgentType;
  source: string;
  models: AgentModel[];
}

/** Payload required to create a new session. */
export interface CreateSessionPayload {
  name: string;
  agent_type: AgentType;
  work_dir: string;
  model: string | null;
  effort: string | null;
}

/** Config geral do app — `GET/PUT /settings`. */
export interface AppSettings {
  /** Instruir as sessões a trabalhar em tarefas/marcos automaticamente. */
  milestones_auto: boolean;
  /** JARVIS (voz) ligado para TODAS as sessões (atalho global). */
  jarvis_all: boolean;
}

/** Status do Worker (host) — `GET /worker`. */
export interface WorkerStatus {
  online: boolean;
  hostname: string | null;
  uptime_seconds: number | null;
  started_at: string | null;
  updated_at: string | null;
}

/** Limites reais do Claude (scrape do /usage). */
export interface ClaudeLimits {
  session_pct: number | null;
  session_reset: string | null;
  week_pct: number | null;
  week_reset: string | null;
}

/** Limites de uso por provider — `GET /usage` (hoje só Claude). */
export interface UsageInfo {
  claude: ClaudeLimits | null;
}

/** Teclas especiais navegáveis em prompts TUI (espelha o allowlist do backend). */
export type TerminalKey =
  | 'up'
  | 'down'
  | 'left'
  | 'right'
  | 'enter'
  | 'space'
  | 'escape'
  | 'tab'
  | 'backspace'
  | 'ctrl-c';
