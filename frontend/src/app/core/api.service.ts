import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, InjectionToken, inject } from '@angular/core';
import { Observable, map } from 'rxjs';
import {
  AgentModels,
  AgentType,
  AppSettings,
  CreateSessionPayload,
  Directory,
  EventItem,
  Notification,
  OutputLine,
  Session,
  SessionStatus,
  Task,
  TerminalKey,
  UsageInfo,
  WorkerStatus,
} from './models';

/**
 * Base URL of the SessionFlow API. Override via DI in production
 * (e.g. provide API_BASE_URL with the prod URL); falls back to localhost.
 */
/**
 * Resolve a base da API a partir do host onde o app está servido:
 * - Acesso externo (`*.boletoazap.dev.br`) → subdomínio `api.sessionflow.*`.
 * - Local/LAN (localhost, 127.0.0.1, IP) → mesmo host na porta 8000
 *   (mantém a origem coerente com o CORS: 127.0.0.1↔127.0.0.1, etc).
 */
function resolveApiBase(): string {
  if (typeof window === 'undefined') {
    return 'http://localhost:8000';
  }
  const { protocol, hostname } = window.location;
  if (hostname.endsWith('boletoazap.dev.br')) {
    // Subdomínio de 1 nível: coberto pelo Universal SSL `*.boletoazap.dev.br`
    // (um 2-níveis como `api.sessionflow.*` não tem cert → handshake TLS falha).
    return 'https://api-sessionflow.boletoazap.dev.br';
  }
  return `${protocol}//${hostname}:8000`;
}

export const API_BASE_URL = new InjectionToken<string>('API_BASE_URL', {
  providedIn: 'root',
  factory: resolveApiBase,
});

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = inject(API_BASE_URL);

  private url(path: string): string {
    return `${this.baseUrl}${path}`;
  }

  /** A API devolve envelopes `{items: [...]}`; desembrulha (tolera array direto). */
  private items<T>() {
    return map((res: { items?: T[] } | T[]): T[] =>
      Array.isArray(res) ? res : (res.items ?? []),
    );
  }

  // --- Sessions ---

  listSessions(status?: SessionStatus): Observable<Session[]> {
    let params = new HttpParams();
    if (status) {
      params = params.set('status', status);
    }
    return this.http
      .get<{ items: Session[] }>(this.url('/sessions'), { params })
      .pipe(this.items<Session>());
  }

  getSession(id: string): Observable<Session> {
    return this.http.get<Session>(this.url(`/sessions/${id}`));
  }

  createSession(payload: CreateSessionPayload): Observable<Session> {
    return this.http.post<Session>(this.url('/sessions'), payload);
  }

  deleteSession(id: string): Observable<void> {
    return this.http.delete<void>(this.url(`/sessions/${id}`));
  }

  /** Elimina de vez: mata o tmux (se vivo) + remove o registro do host/app. */
  purgeSession(id: string): Observable<void> {
    return this.http.delete<void>(this.url(`/sessions/${id}/purge`));
  }

  /** Favorita/desfavorita a sessão (persistido no servidor). */
  setFavorite(id: string, favorite: boolean): Observable<{ favorite: boolean }> {
    return this.http.put<{ favorite: boolean }>(this.url(`/sessions/${id}/favorite`), {
      favorite,
    });
  }

  /** Liga/desliga o JARVIS (resumo falado) para esta sessão. */
  setJarvis(id: string, jarvis: boolean): Observable<{ jarvis: boolean }> {
    return this.http.put<{ jarvis: boolean }>(this.url(`/sessions/${id}/jarvis`), {
      jarvis,
    });
  }

  renameSession(id: string, displayName: string): Observable<Session> {
    return this.http.patch<Session>(this.url(`/sessions/${id}`), {
      display_name: displayName,
    });
  }

  resumeSession(id: string): Observable<Session> {
    return this.http.post<Session>(this.url(`/sessions/${id}/resume`), {});
  }

  // --- Models ---

  /** Modelos reais para um agente; devolve o 1º item do envelope ou null. */
  getModels(agent: AgentType): Observable<AgentModels | null> {
    const params = new HttpParams().set('agent', agent);
    return this.http
      .get<{ items: AgentModels[] }>(this.url('/models'), { params })
      .pipe(
        this.items<AgentModels>(),
        map((items) => items[0] ?? null),
      );
  }

  // --- Interaction ---

  sendInput(id: string, text: string, enter = true): Observable<void> {
    return this.http.post<void>(this.url(`/sessions/${id}/input`), { text, enter });
  }

  /** Envia uma tecla especial (up/down/enter/space/escape/tab…) p/ navegar TUI. */
  sendKey(id: string, key: TerminalKey): Observable<void> {
    return this.http.post<void>(this.url(`/sessions/${id}/key`), { key });
  }

  /** Instrui a sessão a trabalhar em tarefas/marcos (idempotente; gated no server). */
  instructMilestones(id: string): Observable<{ status: string }> {
    return this.http.post<{ status: string }>(
      this.url(`/sessions/${id}/instruct-milestones`),
      {},
    );
  }

  /** Config geral do app (auto-instruir tarefas, JARVIS global). */
  getSettings(): Observable<AppSettings> {
    return this.http.get<AppSettings>(this.url('/settings'));
  }

  setSettings(settings: AppSettings): Observable<AppSettings> {
    return this.http.put<AppSettings>(this.url('/settings'), settings);
  }

  uploadAudio(id: string, file: File | Blob): Observable<void> {
    const form = new FormData();
    form.append('file', file);
    return this.http.post<void>(this.url(`/sessions/${id}/audio`), form);
  }

  /** Anexa um arquivo/imagem à sessão (o worker injeta o caminho no agente). */
  uploadFile(id: string, file: File): Observable<void> {
    const form = new FormData();
    form.append('file', file);
    return this.http.post<void>(this.url(`/sessions/${id}/file`), form);
  }

  // --- Output / Events ---

  getOutput(id: string, after?: number): Observable<OutputLine[]> {
    let params = new HttpParams();
    if (after !== undefined) {
      params = params.set('after', String(after));
    }
    return this.http
      .get<{ items: OutputLine[] }>(this.url(`/sessions/${id}/output`), { params })
      .pipe(this.items<OutputLine>());
  }

  /**
   * Espelho da tela visível atual do pane (ANSI removido, com `\n`).
   * `scrollback` traz o histórico mais profundo (tela visível + linhas roladas)
   * lido sob demanda — usado pelo modo "Histórico" do terminal.
   */
  getScreen(
    id: string,
  ): Observable<{ text: string; at: string | null; scrollback?: string }> {
    return this.http.get<{ text: string; at: string | null; scrollback?: string }>(
      this.url(`/sessions/${id}/screen`),
    );
  }

  getHistory(day?: string): Observable<EventItem[]> {
    let params = new HttpParams();
    if (day) {
      params = params.set('day', day);
    }
    return this.http
      .get<{ items: EventItem[] }>(this.url('/events/history'), { params })
      .pipe(this.items<EventItem>());
  }

  getNotifications(): Observable<Notification[]> {
    return this.http
      .get<{ items: Notification[] }>(this.url('/notifications'))
      .pipe(this.items<Notification>());
  }

  /** Status REAL do Worker (host) para o card do Perfil. */
  getWorker(): Observable<WorkerStatus> {
    return this.http.get<WorkerStatus>(this.url('/worker'));
  }

  /** Limites de uso reais por provider (hoje só Claude). */
  getUsage(): Observable<UsageInfo> {
    return this.http.get<UsageInfo>(this.url('/usage'));
  }

  /** Chave pública VAPID (para assinar a subscrição Web Push no navegador). */
  getVapidKey(): Observable<{ public_key: string }> {
    return this.http.get<{ public_key: string }>(this.url('/push/vapid'));
  }

  /** Registra a subscrição Web Push no servidor (notificação com app fechado). */
  subscribePush(sub: unknown): Observable<void> {
    return this.http.post<void>(this.url('/push/subscribe'), sub);
  }

  /** Foto de perfil (persistida no servidor — nunca em localStorage). */
  getProfile(): Observable<{ photo: string | null }> {
    return this.http.get<{ photo: string | null }>(this.url('/profile'));
  }

  setProfilePhoto(photo: string): Observable<{ photo: string | null }> {
    return this.http.put<{ photo: string | null }>(this.url('/profile/photo'), {
      photo,
    });
  }

  clearProfilePhoto(): Observable<{ photo: string | null }> {
    return this.http.delete<{ photo: string | null }>(this.url('/profile/photo'));
  }

  getTasks(session?: string): Observable<Task[]> {
    let params = new HttpParams();
    if (session) {
      params = params.set('session', session);
    }
    return this.http
      .get<{ items: Task[] }>(this.url('/tasks'), { params })
      .pipe(this.items<Task>());
  }

  /** Apaga uma tarefa (marco): some daqui e do arquivo de marcos no Mac. */
  deleteTask(taskId: string): Observable<void> {
    return this.http.delete<void>(this.url(`/tasks/${taskId}`));
  }

  // --- Directories ---

  searchDirectories(q?: string): Observable<Directory[]> {
    let params = new HttpParams();
    if (q) {
      params = params.set('q', q);
    }
    return this.http
      .get<{ items: Directory[] }>(this.url('/directories'), { params })
      .pipe(this.items<Directory>());
  }
}
