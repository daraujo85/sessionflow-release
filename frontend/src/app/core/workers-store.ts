import { Injectable, computed, inject, signal } from '@angular/core';
import { ApiService } from './api.service';
import { WorkerCapabilities, WorkerStatus } from './models';

/**
 * Lista de hosts/workers conhecidos (multi-host, AD-011) — carregada 1x e
 * compartilhada entre telas (badge de host nos cards, filtro por host,
 * gate de capabilities incompatíveis). Fonte única pra não duplicar a
 * chamada `GET /workers` em cada componente que precisa saber "quantos
 * hosts existem" ou "esse host suporta TTS?".
 */
@Injectable({ providedIn: 'root' })
export class WorkersStore {
  private readonly api = inject(ApiService);

  readonly workers = signal<WorkerStatus[]>([]);
  /** Badge/filtro de host só fazem sentido quando há MAIS de 1 host ativo —
   * não polui a UI do caso comum de hoje (1 host só). */
  readonly hasMultipleHosts = computed(() => this.workers().length > 1);

  constructor() {
    this.refresh();
  }

  /** Rebusca `GET /workers`. Best-effort: falha mantém a lista anterior. */
  refresh(): void {
    this.api.listWorkers().subscribe({
      next: (list) => this.workers.set(list),
      error: () => {
        /* mantém o que já tinha — próxima tela que pedir refresh tenta de novo */
      },
    });
  }

  /** Nome do host pra exibir: display_name (editado no Perfil) se houver,
   * senão o hostname técnico. `null` se desconhecido/sem host_id. */
  hostname(hostId: string | null | undefined): string | null {
    if (!hostId) {
      return null;
    }
    const w = this.workers().find((x) => x.host_id === hostId);
    return w?.display_name || w?.hostname || null;
  }

  /** Emoji do host (editado no Perfil), ou `null` se não definido/host
   * desconhecido — quem consome decide o fallback (ícone genérico). */
  emoji(hostId: string | null | undefined): string | null {
    if (!hostId) {
      return null;
    }
    return this.workers().find((w) => w.host_id === hostId)?.emoji || null;
  }

  /**
   * O host suporta essa capability? **Fail-open** (retorna `true`) quando não
   * dá pra saber ainda — sessão sem `host_id` (legado raríssimo) ou lista de
   * workers ainda não carregada/host offline há muito e nunca visto. Preferir
   * mostrar um botão que talvez não funcione a esconder um que funcionaria.
   */
  supports(hostId: string | null | undefined, key: keyof WorkerCapabilities): boolean {
    if (!hostId) {
      return true;
    }
    const caps = this.workers().find((w) => w.host_id === hostId)?.capabilities;
    return caps ? !!caps[key] : true;
  }
}
