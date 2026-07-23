import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { Location } from '@angular/common';

import { ApiService } from '../../core/api.service';
import { RemoteSession } from '../../core/models';

/**
 * Abre uma sessão de OUTRA conta (bookmark de link de convidado) em tela
 * cheia — o link de convidado já é uma página completa (com o próprio
 * cabeçalho: nome, status, botões), então NÃO temos um header próprio aqui
 * por cima (dava dois cabeçalhos empilhados, layout quebrado). Só um botão
 * de voltar flutuante sobre o iframe.
 */
@Component({
  selector: 'sf-remota',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="overlay">
      <button type="button" class="back" (click)="goBack()" aria-label="Voltar">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#C9CDD6"
             stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M15 18l-6-6 6-6" />
        </svg>
      </button>

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
        position: relative;
        display: block;
        height: 100%;
        background: #0b0d10;
        color: #e6e8ec;
      }
      .back {
        position: absolute;
        top: 12px;
        left: 12px;
        z-index: 1;
        display: grid;
        place-items: center;
        width: 34px;
        height: 34px;
        border: none;
        border-radius: 999px;
        background: rgba(10, 12, 15, 0.72);
        backdrop-filter: blur(4px);
        cursor: pointer;
      }
      .frame {
        display: block;
        width: 100%;
        height: 100%;
        border: none;
        background: #000;
      }
      .msg {
        position: absolute;
        inset: 0;
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
