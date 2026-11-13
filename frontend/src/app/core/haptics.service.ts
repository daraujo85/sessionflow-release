import { Injectable } from '@angular/core';

/**
 * Haptics — vibração curta de retorno ao toque.
 *
 * O recurso existe (o `NotifyService` já vibra ao disparar notificação) mas
 * era usado só ali. Botões que disparam AÇÃO (enviar mensagem, confirmar
 * prompt, escolher opção do JARVIS) se beneficiam do retorno tátil: no
 * celular o olho está no conteúdo, não no dedo.
 *
 * Android honra `navigator.vibrate`; iOS Safari/PWA ignora silenciosamente —
 * por isso nada aqui pode depender da vibração pra acontecer (é feedback
 * extra, nunca confirmação de que a ação foi feita).
 */
@Injectable({ providedIn: 'root' })
export class HapticsService {
  /** Toque curto — confirmação de "pressionei". */
  tap(): void {
    this.vibrate(12);
  }

  /** Toque médio — ação concluída (ex.: mensagem enviada). */
  success(): void {
    this.vibrate([18, 40, 26]);
  }

  /** Padrão de alerta — a ação pede atenção (ex.: prompt pendente). */
  alert(): void {
    this.vibrate([60, 60, 60]);
  }

  /** Padrão de erro. */
  error(): void {
    this.vibrate([90, 50, 90]);
  }

  private vibrate(pattern: number | number[]): void {
    try {
      const nav = navigator as Navigator & { vibrate?: (p: number | number[]) => boolean };
      if (typeof nav.vibrate !== 'function') {
        return;
      }
      nav.vibrate(pattern);
    } catch {
      /* sem suporte a vibração (iOS, contexto sem gesto) — silencioso */
    }
  }

  /**
   * "Destrava" a vibração: o Chrome só permite ``navigator.vibrate`` depois
   * de um gesto do usuário na página (sticky activation). Sem isso, a 1ª
   * vibração é descartada silenciosamente e o app parece "vibrar quebrado"
   * até o usuário tocar em algo.
   *
   * Chamar num toque real (pointerdown) resolve: o toque JÁ é o gesto que
   * ativa a permissão. Este método é idempotente e barato — chamar sempre
   * que não atrapalha.
   */
  unlock(): void {
    this.vibrate(1);
  }
}
