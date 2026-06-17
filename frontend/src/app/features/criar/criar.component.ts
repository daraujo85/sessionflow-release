import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { Subject, switchMap } from 'rxjs';
import { debounceTime, distinctUntilChanged } from 'rxjs/operators';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { HttpErrorResponse } from '@angular/common/http';

import { ApiService } from '../../core/api.service';
import { AgentModel, AgentType, CreateSessionPayload, Directory } from '../../core/models';
import { AGENT_META, AgentMeta } from '../../shared/status-color';

/** Reasoning-effort options (hidden for the gemini agent). */
type Effort = 'Baixo' | 'Médio' | 'Alto' | 'Máximo';
const EFFORTS: Effort[] = ['Baixo', 'Médio', 'Alto', 'Máximo'];

/** Selectable agents shown in the 2x2 grid (excludes "desconhecido"). */
const AGENTS: AgentType[] = ['claude', 'codex', 'gemini', 'opencode'];

@Component({
  selector: 'sf-criar',
  standalone: true,
  imports: [FormsModule],
  template: `
    <section class="overlay">
      <!-- Header -->
      <header class="hdr">
        <button type="button" class="back" (click)="goBack()" aria-label="Voltar">←</button>
        <h1>Nova sessão</h1>
      </header>

      <div class="body">
        <!-- Nome -->
        <label class="field">
          <span class="label">Nome da sessão</span>
          <input
            class="input"
            type="text"
            placeholder="ex: refatorar autenticação"
            [ngModel]="name()"
            (ngModelChange)="name.set($event)"
          />
        </label>

        <!-- Tipo de agente -->
        <div class="field">
          <span class="label">Tipo de agente</span>
          <div class="agent-grid">
            @for (a of agents; track a) {
              <button
                type="button"
                class="agent-card"
                [class.selected]="agent() === a"
                [style.--c]="meta(a).color"
                (click)="selectAgent(a)"
              >
                <span class="agent-short" [style.background]="meta(a).color">{{ meta(a).short }}</span>
                <span class="agent-text">
                  <span class="agent-label">{{ meta(a).label }}</span>
                  <span class="mono agent-cmd">{{ meta(a).cmd }}</span>
                </span>
              </button>
            }
          </div>
        </div>

        <!-- Modelo -->
        <div class="field">
          <span class="label">Modelo</span>
          @if (modelsLoading()) {
            <span class="hint-muted">Carregando modelos…</span>
          } @else if (models().length) {
            <div class="chips">
              @for (m of models(); track m.id) {
                <button
                  type="button"
                  class="chip"
                  [class.selected]="model() === m.id"
                  [attr.title]="m.description || null"
                  (click)="model.set(m.id)"
                >
                  {{ m.label }}
                </button>
              }
            </div>
          } @else {
            <!-- Lista vazia (ex: gemini): campo livre. -->
            <input
              class="input"
              type="text"
              placeholder="digite o modelo (opcional)"
              [ngModel]="freeModel()"
              (ngModelChange)="freeModel.set($event)"
            />
            <span class="hint-muted">
              Sem modelos pré-definidos — deixe vazio para usar o padrão do agente.
            </span>
          }
        </div>

        <!-- Esforço de raciocínio (oculto p/ gemini) -->
        @if (agent() !== 'gemini') {
          <div class="field">
            <span class="label">Esforço de raciocínio</span>
            <div class="chips">
              @for (e of efforts; track e) {
                <button
                  type="button"
                  class="chip"
                  [class.selected]="effort() === e"
                  (click)="effort.set(e)"
                >
                  {{ e }}
                </button>
              }
            </div>
          </div>
        }

        <!-- Diretório de trabalho (autocomplete) -->
        <div class="field">
          <span class="label">Diretório de trabalho</span>
          <input
            class="input mono"
            type="text"
            placeholder="/caminho/do/projeto"
            autocomplete="off"
            [ngModel]="workDir()"
            (ngModelChange)="onDirInput($event)"
            (focus)="dirFocused.set(true)"
            (blur)="onDirBlur()"
          />
          @if (dirFocused() && suggestions().length) {
            <ul class="suggestions">
              @for (d of suggestions(); track d.path) {
                <li>
                  <button type="button" class="sugg" (mousedown)="pickDir(d)">
                    <span class="mono sugg-path">{{ d.path }}</span>
                    <span class="sugg-name">{{ d.name }}</span>
                  </button>
                </li>
              }
            </ul>
          } @else if (workDir().trim() && !suggestions().length && dirSearched()) {
            <span class="hint">Nenhum diretório existente — será criado novo.</span>
          }
        </div>

        <!-- Erro -->
        @if (errorMsg()) {
          <p class="error">{{ errorMsg() }}</p>
        }
      </div>

      <!-- Ação -->
      <footer class="ftr">
        <button
          type="button"
          class="submit"
          [disabled]="!canSubmit() || submitting()"
          (click)="submit()"
        >
          {{ submitting() ? 'Criando…' : 'Criar sessão' }}
        </button>
      </footer>
    </section>
  `,
  styles: [
    `
      :host {
        position: fixed;
        inset: 0;
        z-index: 1000;
        display: block;
      }
      .overlay {
        display: flex;
        flex-direction: column;
        height: 100%;
        background: var(--surface-page);
        color: var(--text-body);
      }
      .hdr {
        display: flex;
        align-items: center;
        gap: var(--space-3);
        padding: var(--space-4);
        border-bottom: 1px solid var(--border-default);
      }
      .hdr h1 {
        margin: 0;
        font-size: var(--text-md);
        font-weight: var(--fw-semibold);
        color: var(--text-strong);
      }
      .back {
        display: grid;
        place-items: center;
        width: 36px;
        height: 36px;
        border: none;
        border-radius: var(--radius-md);
        background: var(--surface-card);
        color: var(--text-strong);
        font-size: 20px;
        cursor: pointer;
      }
      .body {
        flex: 1;
        overflow-y: auto;
        padding: var(--space-4);
        display: flex;
        flex-direction: column;
        gap: var(--space-6);
      }
      .field {
        display: flex;
        flex-direction: column;
        gap: var(--space-2);
        position: relative;
      }
      .label {
        font-size: var(--text-sm);
        font-weight: var(--fw-medium);
        color: var(--text-strong);
      }
      .input {
        width: 100%;
        padding: var(--space-3);
        border: 1px solid var(--border-default);
        border-radius: var(--radius-lg);
        background: var(--surface-card);
        color: var(--text-strong);
        font-size: var(--text-base);
        font-family: inherit;
      }
      .input.mono {
        font-family: var(--font-mono);
        font-size: var(--text-sm);
      }
      .input:focus {
        outline: none;
        border-color: var(--color-accent);
        box-shadow: 0 0 0 3px var(--focus-ring);
      }

      /* Agent grid 2x2 */
      .agent-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: var(--space-3);
      }
      .agent-card {
        display: flex;
        align-items: center;
        gap: var(--space-3);
        padding: var(--space-3);
        border: 2px solid var(--border-default);
        border-radius: var(--radius-lg);
        background: var(--surface-card);
        color: var(--text-body);
        cursor: pointer;
        text-align: left;
      }
      .agent-card.selected {
        border-color: var(--c);
        background: var(--surface-raised);
      }
      .agent-short {
        flex: 0 0 auto;
        display: grid;
        place-items: center;
        width: 28px;
        height: 28px;
        border-radius: var(--radius-md);
        color: #fff;
        font-size: var(--text-xs);
        font-weight: var(--fw-bold);
      }
      .agent-text {
        display: flex;
        flex-direction: column;
        min-width: 0;
      }
      .agent-label {
        font-size: var(--text-sm);
        font-weight: var(--fw-medium);
        color: var(--text-strong);
      }
      .agent-cmd {
        font-size: var(--text-xs);
        color: var(--text-muted);
      }

      /* Chips */
      .chips {
        display: flex;
        flex-wrap: wrap;
        gap: var(--space-2);
      }
      .chip {
        padding: var(--space-2) var(--space-3);
        border: 1px solid var(--border-default);
        border-radius: var(--radius-full);
        background: var(--surface-card);
        color: var(--text-body);
        font-size: var(--text-sm);
        cursor: pointer;
      }
      .chip.selected {
        border-color: var(--color-accent);
        background: rgba(var(--color-accent-rgb), 0.12);
        color: var(--color-accent);
        font-weight: var(--fw-medium);
      }

      /* Autocomplete */
      .suggestions {
        list-style: none;
        margin: var(--space-1) 0 0;
        padding: var(--space-1);
        border: 1px solid var(--border-default);
        border-radius: var(--radius-lg);
        background: var(--surface-card);
        max-height: 220px;
        overflow-y: auto;
      }
      .sugg {
        display: flex;
        flex-direction: column;
        gap: 2px;
        width: 100%;
        padding: var(--space-2) var(--space-3);
        border: none;
        border-radius: var(--radius-md);
        background: transparent;
        color: var(--text-body);
        text-align: left;
        cursor: pointer;
      }
      .sugg:hover {
        background: var(--surface-raised);
      }
      .sugg-path {
        font-size: var(--text-sm);
        color: var(--text-strong);
      }
      .sugg-name {
        font-size: var(--text-xs);
        color: var(--text-muted);
      }
      .hint {
        font-size: var(--text-xs);
        color: var(--warning);
      }
      .hint-muted {
        font-size: var(--text-xs);
        color: var(--text-muted);
      }
      .error {
        margin: 0;
        padding: var(--space-3);
        border-radius: var(--radius-lg);
        background: rgba(248, 113, 113, 0.12);
        color: var(--danger);
        font-size: var(--text-sm);
      }

      /* Footer / submit */
      .ftr {
        padding: var(--space-4);
        border-top: 1px solid var(--border-default);
      }
      .submit {
        width: 100%;
        padding: var(--space-4);
        border: none;
        border-radius: var(--radius-lg);
        background: var(--color-accent);
        color: var(--text-on-accent);
        font-size: var(--text-base);
        font-weight: var(--fw-semibold);
        cursor: pointer;
      }
      .submit:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }
    `,
  ],
})
export class CriarComponent {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);

  /** Static option lists exposed to the template. */
  readonly agents = AGENTS;
  readonly efforts = EFFORTS;

  // --- Form state (signals) ---
  readonly name = signal('');
  readonly agent = signal<AgentType>('claude');
  /** Selected model id (when a chip list is available). */
  readonly model = signal<string>('');
  /** Free-text model (used when the agent has no predefined models). */
  readonly freeModel = signal<string>('');
  readonly effort = signal<Effort | null>(null);
  readonly workDir = signal('');

  // --- Models (fetched per agent) ---
  readonly models = signal<AgentModel[]>([]);
  readonly modelsLoading = signal(false);

  // --- Autocomplete state ---
  readonly suggestions = signal<Directory[]>([]);
  readonly dirFocused = signal(false);
  readonly dirSearched = signal(false);

  // --- Submit state ---
  readonly submitting = signal(false);
  readonly errorMsg = signal('');

  /** Whether the form is ready to submit. */
  readonly canSubmit = computed(
    () => this.name().trim().length > 0 && this.workDir().trim().length > 0,
  );

  private readonly dirQuery$ = new Subject<string>();

  constructor() {
    this.dirQuery$
      .pipe(
        debounceTime(250),
        distinctUntilChanged(),
        switchMap((q) => this.api.searchDirectories(q)),
        takeUntilDestroyed(),
      )
      .subscribe({
        next: (dirs) => {
          this.suggestions.set(dirs);
          this.dirSearched.set(true);
        },
        error: () => {
          this.suggestions.set([]);
          this.dirSearched.set(true);
        },
      });

    // Load models for the initial agent.
    this.loadModels(this.agent());
  }

  meta(a: AgentType): AgentMeta {
    return AGENT_META[a];
  }

  selectAgent(a: AgentType): void {
    if (this.agent() === a) {
      return;
    }
    this.agent.set(a);
    // Gemini has no reasoning-effort concept.
    if (a === 'gemini') {
      this.effort.set(null);
    }
    this.loadModels(a);
  }

  /** Token used to ignore responses from superseded model requests. */
  private modelReqToken = 0;

  /** Fetch real models for an agent and pick a sensible default. */
  private loadModels(a: AgentType): void {
    const token = ++this.modelReqToken;
    this.models.set([]);
    this.model.set('');
    this.freeModel.set('');
    this.modelsLoading.set(true);

    this.api
      .getModels(a)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (res) => {
          if (token !== this.modelReqToken) {
            return;
          }
          const list = res?.models ?? [];
          this.models.set(list);
          // Default = is_default if any, else the first option.
          const def = list.find((m) => m.is_default) ?? list[0];
          this.model.set(def?.id ?? '');
          this.modelsLoading.set(false);
        },
        error: () => {
          if (token !== this.modelReqToken) {
            return;
          }
          // Don't block the screen: fall back to a free-text field.
          this.models.set([]);
          this.model.set('');
          this.modelsLoading.set(false);
        },
      });
  }

  onDirInput(value: string): void {
    this.workDir.set(value);
    this.dirSearched.set(false);
    const q = value.trim();
    if (q.length === 0) {
      this.suggestions.set([]);
      return;
    }
    this.dirQuery$.next(q);
  }

  pickDir(d: Directory): void {
    this.workDir.set(d.path);
    this.suggestions.set([]);
    this.dirFocused.set(false);
  }

  onDirBlur(): void {
    // Delay so an in-progress mousedown on a suggestion still fires.
    setTimeout(() => this.dirFocused.set(false), 150);
  }

  goBack(): void {
    this.router.navigate(['/sessoes']);
  }

  submit(): void {
    if (!this.canSubmit() || this.submitting()) {
      return;
    }
    this.errorMsg.set('');
    this.submitting.set(true);

    const isGemini = this.agent() === 'gemini';
    // When the agent has predefined models use the selected id; otherwise the
    // free-text field. Empty in either case means null.
    const hasModelList = this.models().length > 0;
    const modelValue = hasModelList ? this.model() : this.freeModel().trim();
    const payload: CreateSessionPayload = {
      name: this.name().trim(),
      agent_type: this.agent(),
      work_dir: this.workDir().trim(),
      model: modelValue || null,
      // effort is null for gemini or when nothing is selected.
      effort: isGemini ? null : (this.effort() ?? null),
    };

    this.api.createSession(payload).subscribe({
      next: () => {
        this.submitting.set(false);
        this.router.navigate(['/sessoes']);
      },
      error: (err: HttpErrorResponse) => {
        this.submitting.set(false);
        if (err.status === 409) {
          this.errorMsg.set('Já existe uma sessão com esse nome. Escolha outro.');
        } else {
          const detail =
            (err.error && (err.error.detail || err.error.message)) || err.message;
          this.errorMsg.set(`Falha ao criar a sessão: ${detail ?? 'erro desconhecido'}`);
        }
      },
    });
  }
}
