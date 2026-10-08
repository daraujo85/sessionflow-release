import { Injectable, computed, inject, signal } from '@angular/core';
import { ApiService } from './api.service';
import { AppSettings } from './models';

/**
 * Config global do app (`GET/PUT /settings`) — carregada 1x e compartilhada
 * entre componentes que precisam reagir a toggles (dedup de voz/texto, JARVIS
 * all, milestones auto). Sem isso, cada componente faria seu próprio fetch e
 * o toggle mudado no Perfil não propagaria pro Detalhe/split em tempo real.
 *
 * Mesmo padrão do [[WorkersStore]]: signal reativo + `refresh()` best-effort.
 * Defaults são "tudo desligado" — bate com a realidade de um app fresh onde
 * JARVIS e dedup são opt-in.
 */
@Injectable({ providedIn: 'root' })
export class AppSettingsStore {
  private readonly api = inject(ApiService);

  readonly settings = signal<AppSettings>({
    milestones_auto: true,
    jarvis_all: false,
    jarvis_full_all: false,
    voice_dedup_enabled: false,
    voice_dedup_model: 'qwen3:0.6b',
    text_dedup_enabled: false,
    keep_model_warm: false,
    transcription_glossary: '',
  });

  constructor() {
    this.refresh();
  }

  /** Rebusca `GET /settings`. Falha mantém o snapshot anterior. */
  refresh(): void {
    this.api.getSettings().subscribe({
      next: (s) => this.settings.set(s),
      error: () => {
        /* mantém o que já tinha */
      },
    });
  }

  /** Persiste + atualiza o signal local em uma operação. */
  save(patch: AppSettings): void {
    this.api.setSettings(patch).subscribe({
      next: (s: AppSettings) => this.settings.set(s),
      error: () => {
        /* Perfil mostra erro próprio; mantém cache anterior */
      },
    });
  }

  /** Atalhos computados — evita espalhar `settings().xxx` pelos componentes. */
  readonly voiceDedupOn = computed(() => this.settings().voice_dedup_enabled);
  readonly textDedupOn = computed(() => this.settings().text_dedup_enabled);
}