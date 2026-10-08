import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { Location } from '@angular/common';

import { ApiService } from '../../core/api.service';
import { RemoteSession } from '../../core/models';

/**
 * Abre uma sessão de OUTRA conta (bookmark de link de convidado) em tela
 * cheia — o link de convidado já é uma página completa (com o próprio
 * cabeçalho: nome, status, botões), então não duplicamos um header aqui.
 * Só uma faixa BEM fina com a seta de voltar, ocupando espaço de verdade
 * (não flutua por cima) — flutuando ela colidia com o badge "Compartilhado"
 * que a própria página de convidado já desenha no canto superior esquerdo.
 */
@Component({
  selector: 'sf-remota',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="overlay">
      <div class="bar">
        <button type="button" class="back" (click)="goBack()" aria-label="Voltar">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#C9CDD6"
               stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M15 18l-6-6 6-6" />
          </svg>
        </button>
      </div>

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
      .bar {
        flex: none;
        display: flex;
        align-items: center;
        padding: 6px 8px;
        background: #14171c;
      }
      .back {
        display: grid;
        place-items: center;
        width: 30px;
        height: 30px;
        border: none;
        border-radius: 8px;
        background: transparent;
        cursor: pointer;
      }
      .back:hover {
        background: #20242b;
      }
      .frame {
        flex: 1;
        display: block;
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
