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

/** Tokens/custo somados dentro de uma janela de período (ver {@link SessionMetrics.tokens_periods}). */
export interface TokenPeriodUsage {
  tokens_in: number;
  tokens_out: number;
  cost: {
    total_usd: number | null;
    total_brl?: number | null;
    by_model: {
      model: string;
      input: number;
      output: number;
      cache_read: number;
      cache_write: number;
      usd: number | null;
    }[];
  } | null;
}

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
  /** Custo estimado (USD, preço de API) por modelo. usd null = preço desconhecido. */
  cost?: {
    total_usd: number | null;
    /** Cotação USD→BRL do dia (cache ~6h no worker) e o total convertido. */
    brl_rate?: number | null;
    total_brl?: number | null;
    by_model: {
      model: string;
      input: number;
      output: number;
      cache_read: number;
      cache_write: number;
      usd: number | null;
    }[];
  } | null;
  /**
   * Tokens/custo dentro de janelas ROLANTES (últimas 24h/7d/30d) — alimenta o
   * filtro "hoje/semana/mês" do Top 3 da Home. "sempre" usa os campos
   * tokens_in/tokens_out/cost acima (não duplicado aqui).
   */
  tokens_periods?: {
    today?: TokenPeriodUsage;
    week?: TokenPeriodUsage;
    month?: TokenPeriodUsage;
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
  /** Repo(s) git do work_dir (badge(s) no card): o work_dir sendo ele mesmo
   * um repo (name=".") e/ou subpastas diretas que também são (pasta
   * "guarda-chuva" com vários projetos). Ausente = nenhum repo git ali. */
  git_repos?: { name: string; path: string; branch: string }[] | null;
  status: SessionStatus;
  /** Rótulo fino do que o agente está fazendo (worker, só p/ sessões running). */
  activity?: string;
  origin: string;
  favorite?: boolean;
  /** JARVIS: resumo falado da sessão (voz no celular) ligado p/ esta sessão. */
  jarvis?: boolean;
  /** Modo do JARVIS nesta sessão: "speaker" só toca áudio (hoje = `jarvis`
   * true); "full" também detecta picker de escolha e pede resposta por voz;
   * "off" desliga tudo. Default "speaker" quando ausente (compat). */
  jarvis_mode?: 'off' | 'speaker' | 'full';
  /** Descrição breve e "viva" do que se trata a sessão, escrita pela própria
   * IA da sessão (campo "description" do milestones, espelhado pelo worker). */
  ai_description?: string | null;
  /** Sub-agents rodando agora (heurística do worker sobre a tela). */
  subagents?: number;
  /** Nomes dos sub-agents rodando (quando o provedor expõe) — p/ tooltip. */
  subagent_names?: string[];
  /** tmux_name da sessão PAI que delegou esta (via `sf delegate`); null se raiz. */
  parent?: string | null;
  /** Último artifact (claude.ai) visto na tela desta sessão (worker persiste). */
  last_artifact_url?: string | null;
  /** Histórico de artifacts vistos (mais recente primeiro, máx 10). */
  artifact_urls?: string[];
  metrics?: SessionMetrics | null;
  /** Instante da última ATIVIDADE real (tela mudou / input do usuário). ISO. */
  last_activity_at?: string | null;
  /** Host (worker) dono desta sessão — multi-host (AD-011). Sessões antigas
   * (pré-migração) sempre têm o campo, já backfilled pelo worker no boot. */
  host_id?: string | null;
  /** Worktree isolada (`<repo>-clones/…`) — sessão nela não pode mudar de host. */
  worktree_path?: string | null;
  /** Transferência para outro host em andamento (ou que falhou). Ausente =
   * não está movendo. */
  moving?: SessionMoving | null;
  /** Instante (ISO) da última transferência de host concluída. */
  moved_at?: string | null;
  /** Histórico de transferências de host (mais antiga primeiro). */
  move_history?: SessionMoveRecord[] | null;
  [key: string]: unknown;
}

/** Fase de `Session.moving` — exportando no origem, importando no destino
 * ou falhou (o agente volta a rodar no host de origem). */
export type SessionMovePhase = 'exporting' | 'importing' | 'failed';

/** Estado de uma transferência de host em andamento (`Session.moving`). */
export interface SessionMoving {
  target_host_id: string;
  /** Caminho absoluto da pasta no host destino. */
  target_work_dir: string;
  phase: SessionMovePhase;
  /** Motivo da falha (pt-BR), só quando `phase === 'failed'`. */
  error: string | null;
  started_at: string;
  transfer_id?: string | null;
}

/** Uma transferência de host concluída (`Session.move_history`). */
export interface SessionMoveRecord {
  from: string;
  to: string;
  at: string;
}

export interface EventItem {
  id: string;
  session_id: string | null;
  type: string;
  kind: EventKind;
  title: string;
  desc: string;
  at: string;
  /** Alto-falante da sessão ligado? Quando `false`, o cliente não toca o chime. */
  jarvis?: boolean;
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
  /** ISO da última mudança do marco (para ordenar/filtrar "do dia"). */
  updated_at?: string | null;
}

/** Estado do link compartilhável de uma sessão (efêmero, escopado). */
export interface ShareLink {
  active: boolean;
  url?: string | null;
  expires_at?: string | null;
}

/** Arquivo que o AGENTE compartilhou de volta (ver `tools/sf share`). */
export interface SharedFile {
  id: string;
  session_id: string;
  filename: string;
  content_type: string;
  size: number;
  created_at: string | null;
}

/** Bookmark de uma sessão de OUTRA conta (link de convidado colado aqui) —
 * aparece na lista de Sessões destacada como "não é minha" (ver `sf send`/
 * `sf share`, painel "Compartilhar" de outra pessoa). Não tem NENHUMA
 * ligação de dados com a conta remota, é só o link + um rótulo. */
export interface RemoteSession {
  id: string;
  label: string;
  url: string;
  created_at: string | null;
}

/** Comando programado: instrução recorrente enviada ao terminal da sessão. */
export interface Schedule {
  id: string;
  session_id: string;
  text: string;
  interval_seconds: number;
  enabled: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  last_error: string | null;
  created_at: string | null;
}

/** Status de uma demanda na fila (fila de demandas / orquestrador). */
export type DemandStatus = 'pending' | 'in_progress' | 'completed' | 'error';

export interface Demand {
  id: string;
  base_session_id: string;
  text: string;
  status: DemandStatus;
  /** Sessão-filha criada pra essa demanda (preenchida quando entra em execução). */
  target_session_id: string | null;
  /** Branch git da sessão-filha. */
  branch: string | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  /** Títulos dos milestones concluídos, só quando `status === 'completed'`. */
  summary: string | null;
  /** Motivo do erro, só quando `status === 'error'`. */
  error_note: string | null;
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
  display_name?: string;
  agent_type: AgentType;
  work_dir?: string | null;
  model: string | null;
  effort: string | null;
  /** Host ONDE criar (multi-host, AD-011). Ausente = auto-resolve pro
   * worker mais recentemente ativo (comportamento de hoje, 1 host só). */
  host_id?: string | null;
  /** Sessão EFÊMERA (UI "Rápida"): worker gera `work_dir` sozinho (scratch
   * no host escolhido), marca `origin="ephemeral"`, e o scheduler apaga após
   * 24h sem atividade. Quando True, `work_dir` é opcional. */
  ephemeral?: boolean;
}

/** Config geral do app — `GET/PUT /settings`. */
export interface AppSettings {
  /** Instruir as sessões a trabalhar em tarefas/marcos automaticamente. */
  milestones_auto: boolean;
  /** JARVIS (voz) ligado para TODAS as sessões (atalho global). */
  jarvis_all: boolean;
  /** JARVIS COMPLETO (picker + resposta por voz) para TODAS as sessões —
   * atalho global separado do `jarvis_all` (ligar escuta de mic em toda
   * sessão é uma decisão maior, tem toggle próprio). */
  jarvis_full_all: boolean;
  /** Compressão pós-transcrição: remove redundâncias do áudio via SLM local
   * (Ollama) antes de injetar no terminal. Default false. */
  voice_dedup_enabled: boolean;
  /** Modelo Ollama para dedup. qwen3:0.6b roda em CPU; qwen3:1.7b se tiver GPU. */
  voice_dedup_model: string;
  /** Dedup automático do TEXTO digitado/Enviado (mesmo Ollama/SLM do voz).
   * Roda no send() antes de submeter; preserva comandos/URLs/emojis/line breaks. */
  text_dedup_enabled: boolean;
  /** Keep-alive do modelo Ollama: ping a cada 120s pra evitar que o daemon
   * descarregue o modelo após idle (~5min). Default false — só ligar quando o
   * dedup é usado mas a sessão fica parada por minutos. */
  keep_model_warm: boolean;
  /** Termos técnicos (vírgula/quebra de linha) usados como hint (initial_prompt)
   * do Whisper na transcrição de áudio — reduz erro em siglas/nomes próprios
   * recorrentes (ex. "QI Tech", "BMS"). Default vazio (sem hint). */
  transcription_glossary: string;
}

/** O que este host consegue fazer — multi-host (AD-011). Decide quais botões/
 * features aparecem no app pras sessões daquele host (TTS, "abrir no Mac",
 * upload de áudio p/ transcrição). */
export interface WorkerCapabilities {
  platform: string;
  tts: boolean;
  transcription: boolean;
  open_terminal: boolean;
}

/** Status de UM Worker (host) — `GET /worker` (o mais recente) e cada item
 * de `GET /workers` (todos os hosts conhecidos). */
export interface WorkerStatus {
  online: boolean;
  hostname: string | null;
  /** Nome de exibição editável (Perfil) — `null` usa o `hostname` técnico. */
  display_name?: string | null;
  /** Emoji editável (Perfil) — vira o identificador visual nos badges
   * (substitui o ícone genérico) quando definido. `null` = sem emoji. */
  emoji?: string | null;
  /** `null` em docs antigos (pré-migração multi-host, worker não reiniciou). */
  host_id?: string | null;
  platform?: string | null;
  capabilities?: WorkerCapabilities | null;
  uptime_seconds: number | null;
  started_at: string | null;
  updated_at: string | null;
  /** Config de áudio do JARVIS (Perfil > Áudio) — `null` = segue o default do
   * host (env var / efeito ligado). */
  tts_mode?: string | null;
  voice_effect?: boolean | null;
  /** Hardware/SO detalhado (Perfil > card do host, expandido). */
  hardware?: WorkerHardware | null;
  /** Carga ao vivo do heartbeat (worker novo); ausente/null em worker antigo. */
  metrics?: WorkerLoad | null;
  /** CLIs de agente instaladas no host (claude/codex/gemini/opencode/agy). */
  agents?: Record<string, boolean> | null;
  /** Disponibilidade de cada motor de TTS NESTE host — {motor: {installed,
   * installable}}. Usado pra só oferecer motores compatíveis com o SO e
   * mostrar "Instalar" (com progresso) quando dá pra baixar sozinho. */
  tts_engines?: Record<string, { installed: boolean; installable: boolean }> | null;
  /** Status do Ollama e modelo de dedup: {"ollama_running": bool, "model_installed": bool} */
  ollama_status?: { ollama_running: boolean; model_installed: boolean } | null;
  /** Modelos Ollama instalados NESTE host (nome/tamanho/carregado agora).
   * `null`/`[]` = Ollama offline ou sem modelos instalados. */
  ollama_models?: OllamaModel[] | null;
  /** ID do notebook Google Drive deste Colab (Perfil > Ligar Colab). `null` =
   * usar o default do backend. Setar no `worker_status` do Mongo quando o
   * Colab tem um notebook dedicado (ex.: Colab novo precisa do SEU). */
  colab_notebook_id?: string | null;
  /** Qual conta Google selecionar na URL do Colab quando há múltiplas
   * logadas ("0"/"1"/...). `null` = heurística do backend. */
  colab_authuser?: string | null;
}

/** Um modelo Ollama instalado num host (ver WorkerStatus.ollama_models). */
export interface OllamaModel {
  name: string;
  size_gb: number;
  modified_at: string | null;
  /** true = carregado na memória agora (rodando), via `/api/ps`. */
  loaded: boolean;
}

/** Snapshot de hardware/SO de um host — calculado 1x no boot do worker. */
export interface WorkerLoad {
  cpu_pct: number | null;
  mem_pct: number | null;
  mem_total_gb: number | null;
  mem_avail_gb: number | null;
}

export interface WorkerHardware {
  cpu_model?: string | null;
  cpu_cores?: number | null;
  ram_total_gb?: number | null;
  gpu?: string | null;
  os_detail?: { distro?: string | null; host_os?: string | null } | null;
  disks?: { mount: string; total_gb: number; used_gb: number }[] | null;
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
  | 'shift-tab'
  | 'backspace'
  | 'ctrl-c'
  | 'scroll-up'
  | 'scroll-down'
  | 'scroll-bottom';
