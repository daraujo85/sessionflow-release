import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { Location } from '@angular/common';

import { ApiService } from '../../core/api.service';
import { RemoteSession } from '../../core/models';

/**
 * Abre uma sessão de OUTRA conta (bookmark de link de convidado) em tela
 * cheia — mesmo tratamento visual do Detalhe (`/sessao/:id`), não um modal:
 * o link de convidado já é público/funciona sozinho (ver `ShareLink`), então
 * só embedamos ele num iframe cobrindo a tela toda.
 */
@Component({
  selector: 'sf-remota',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="overlay">
      <header class="hdr">
        <button type="button" class="back" (click)="goBack()" aria-label="Voltar">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#C9CDD6"
               stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M15 18l-6-6 6-6" />
          </svg>
        </button>
        <div class="hdr-info">
          <span class="hdr-name">{{ remote()?.label || 'Sessão remota' }}</span>
          <span class="hdr-sub">Sessão de outra conta</span>
        </div>
      </header>

      @if (remote(); as r) {
        <iframe
          class="frame"
          [src]="safeUrl(r.url)"
          allow="clipboard-write"
          title="Sessão remota"
        ></iframe>
      } @else if (loading()) {
        <div class="msg">Carregando…</div>
      } @else {
        <div class="msg">Não foi possível abrir esta sessão (link removido ou inválido).</div>
      }
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
        background: #0b0d10;
        color: #e6e8ec;
      }
      .hdr {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 14px 16px;
        border-bottom: 1px solid #22262c;
        flex: none;
      }
      .back {
        display: grid;
        place-items: center;
        width: 36px;
        height: 36px;
        border: none;
        border-radius: 10px;
        background: #181c1b;
        cursor: pointer;
        flex: none;
      }
      .hdr-info {
        display: flex;
        flex-direction: column;
        min-width: 0;
      }
      .hdr-name {
        font-size: 15px;
        font-weight: 700;
        color: #e7eae9;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .hdr-sub {
        font-size: 12px;
        color: #7a8090;
      }
      .frame {
        flex: 1;
        width: 100%;
        border: none;
        background: #000;
      }
      .msg {
        flex: 1;
        display: grid;
        place-items: center;
        color: #7a8090;
        font-size: 14px;
        padding: 24px;
        text-align: center;
      }
    `,
  ],
})
export class RemotaComponent {
  private readonly api = inject(ApiService);
  private readonly route = inject(ActivatedRoute);
  private readonly location = inject(Location);
  private readonly sanitizer = inject(DomSanitizer);

  protected readonly remote = signal<RemoteSession | null>(null);
  protected readonly loading = signal(true);

  private readonly id = computed(() => this.route.snapshot.paramMap.get('id') ?? '');

  constructor() {
    this.api.getRemoteSession(this.id()).subscribe({
      next: (r) => {
        this.remote.set(r);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  protected safeUrl(url: string): SafeResourceUrl {
    return this.sanitizer.bypassSecurityTrustResourceUrl(url);
  }

  protected goBack(): void {
    this.location.back();
  }
}
