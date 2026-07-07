import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { forkJoin, of, catchError, map } from 'rxjs';
import { Router } from '@angular/router';
import { ApiService } from '../../core/api.service';
import { Session, SessionStatus } from '../../core/models';
import { SseService } from '../../core/sse.service';
import { JarvisAudioService } from '../../core/jarvis-audio.service';
import { STATUS_META, agentMeta, isWorkerSession } from '../../shared/status-color';
import { timeAgo as fmtTimeAgo } from '../../shared/time-ago';

/** One selectable filter chip. `status` undefined means "Todas". */
interface FilterChip {
  readonly key: string;
  readonly label: string;
  readonly status?: SessionStatus;
}

/** Filter chips shown horizontally above the list (mockup "SESSÕES"). */
const FILTERS: readonly FilterChip[] = [
  { key: 'favorites', label: '★ Favoritas' },
  { key: 'running', label: 'Ativas', status: 'running' },
  { key: 'waiting_input', label: 'Aguardando', status: 'waiting_input' },
  { key: 'completed', label: 'Concluídas', status: 'completed' },
  { key: 'detached', label: 'Detached', status: 'detached' },
];

@Component({
  selector: 'sf-sessoes',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="sf-sessoes">
      <header class="sf-head">
        <h1 class="sf-title">Sessões</h1>
        <button
          type="button"
          class="sf-select-toggle"
          [class.is-active]="selectionMode()"
          (click)="toggleSelectionMode()"
          [attr.aria-pressed]="selectionMode()"
          [attr.aria-label]="selectionMode() ? 'Sair do modo seleção' : 'Selecionar várias sessões'"
        >
          @if (selectionMode()) {
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
            Cancelar
          } @else {
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M9 11l3 3L22 4" />
              <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
            </svg>
            Selecionar
          }
        </button>
      </header>

      <div class="sf-search">
        <svg
          class="sf-search__icon"
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <circle cx="11" cy="11" r="7" />
          <path d="M21 21l-4.3-4.3" />
        </svg>
        <input
          type="text"
          class="sf-search__input"
          placeholder="Buscar sessão..."
          aria-label="Buscar sessão"
          [value]="query()"
          (input)="query.set($any($event.target).value)"
        />
        @if (query()) {
          <button
            type="button"
            class="sf-search__clear"
            aria-label="Limpar busca"
            (click)="query.set('')"
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        }
      </div>

      <nav class="sf-chips" role="tablist" aria-label="Filtrar sessões">
        @for (chip of filters; track chip.key) {
          <button
            type="button"
            role="tab"
            class="sf-chip"
            [class.is-active]="activeKey() === chip.key"
            [attr.aria-selected]="activeKey() === chip.key"
            (click)="selectFilter(chip)"
          >
            {{ chip.label }}
          </button>
        }
      </nav>

      @if (loading() && sessions().length === 0) {
        <p class="sf-msg">Carregando…</p>
      } @else if (error() && sessions().length === 0) {
        <p class="sf-msg sf-msg--error">Não foi possível carregar as sessões.</p>
      } @else if (visibleSessions().length === 0) {
        <div class="sf-empty">
          <p class="sf-empty__title">Nenhuma sessão</p>
          <p class="sf-empty__sub">Nada por aqui neste filtro.</p>
        </div>
      } @else {
        <ul class="sf-list">
          @for (s of visibleSessions(); track s.id; let i = $index) {
            <li class="sf-card-wrap sf-enter" [style.animation-delay]="enterDelay(i)">
              <button
                type="button"
                class="sf-delete"
                [class.is-open]="offset(s.id) <= -72"
                tabindex="-1"
                [attr.aria-hidden]="offset(s.id) > -72"
                (click)="confirmEliminate(s)"
                aria-label="Eliminar sessão"
              >
                <span class="sf-delete__inner">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
                       stroke="currentColor" stroke-width="2" stroke-linecap="round"
                       stroke-linejoin="round" aria-hidden="true">
                    <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m2 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                    <path d="M10 11v6M14 11v6" />
                  </svg>
                  <span class="sf-delete__label">Eliminar</span>
                </span>
              </button>
              <button
                type="button"
                class="sf-fav"
                [class.on]="!!s.favorite"
                (click)="toggleFav(s)"
                [attr.aria-label]="s.favorite ? 'Desfavoritar' : 'Favoritar'"
                title="Favoritar sessão"
                [style.transform]="'translateX(' + offset(s.id) + 'px)'"
              >
                <svg width="18" height="18" viewBox="0 0 24 24"
                     [attr.fill]="s.favorite ? 'currentColor' : 'none'"
                     stroke="currentColor" stroke-width="2" stroke-linecap="round"
                     stroke-linejoin="round" aria-hidden="true">
                  <path d="M12 2l3 6.5 7 .9-5 4.8 1.3 7L12 18l-6.3 3.2L7 14.2l-5-4.8 7-.9z" />
                </svg>
              </button>
              <button
                type="button"
                class="sf-card"
                [class.is-dragging]="dragId() === s.id"
                [class.is-waiting]="s.status === 'waiting_input'"
                [class.sf-flash]="taskFlash(s)"
                [class.is-selecting]="selectionMode()"
                [class.is-selected]="isSelected(s)"
                [style.transform]="'translateX(' + offset(s.id) + 'px)'"
                (click)="onCardClick(s, $event)"
                (pointerdown)="onPointerDown(s, $event)"
                (pointermove)="onPointerMove(s, $event)"
                (pointerup)="onPointerUp(s, $event)"
                (pointercancel)="onPointerUp(s, $event)"
                [attr.aria-pressed]="selectionMode() ? isSelected(s) : null"
                [attr.aria-label]="
                  selectionMode()
                    ? (isSelected(s) ? 'Desmarcar ' : 'Selecionar ') + displayName(s)
                    : 'Abrir sessão ' + displayName(s)
                "
              >
                <span class="sf-press">
                <span class="sf-row">
                  @if (selectionMode()) {
                    <span class="sf-check" [class.on]="isSelected(s)" aria-hidden="true">
                      @if (isSelected(s)) {
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                             stroke="currentColor" stroke-width="3" stroke-linecap="round"
                             stroke-linejoin="round"><path d="M5 12l4 4 10-10" /></svg>
                      }
                    </span>
                  }
                  <span
                    class="sf-avatar"
                    [style.color]="agent(s).color"
                    [style.background]="tint(agent(s).color, 0.16)"
                    >{{ agent(s).short }}</span
                  >

                  <span class="sf-info">
                    <span class="sf-name-row">
                      <span class="sf-name">{{ displayName(s) }}</span>
                      @if (isWorker(s)) {
                        <span class="sf-worker-chip" title="Worker / sub-agente">
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                               stroke="currentColor" stroke-width="2" stroke-linecap="round"
                               stroke-linejoin="round" aria-hidden="true">
                            <path d="M12 8V4H8" />
                            <rect width="16" height="12" x="4" y="8" rx="2" />
                            <path d="M2 14h2M20 14h2M15 13v2M9 13v2" />
                          </svg>
                          worker
                        </span>
                      }
                      @if (isSpeaking(s)) {
                        <span class="sf-speaker" title="Áudio desta sessão tocando agora"
                              aria-label="Falando">
                          <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
                               stroke="currentColor" stroke-width="2" stroke-linecap="round"
                               stroke-linejoin="round" aria-hidden="true">
                            <path d="M11 5 6 9H2v6h4l5 4z" />
                            <path class="sf-wave sf-wave1" d="M15.5 8.5a5 5 0 0 1 0 7" />
                            <path class="sf-wave sf-wave2" d="M18.5 5.5a9 9 0 0 1 0 13" />
                          </svg>
                        </span>
                      }
                    </span>
                    @if (hasParent(s)) {
                      <span class="sf-parent-chip"
                            [title]="'Delegada por ' + parentLabel(s)">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                             stroke="currentColor" stroke-width="2" stroke-linecap="round"
                             stroke-linejoin="round" aria-hidden="true">
                          <path d="M9 5v6a4 4 0 0 0 4 4h7" />
                          <path d="M16 11l4 4-4 4" />
                        </svg>
                        delegada por {{ parentLabel(s) }}
                      </span>
                    }
                    <span class="mono sf-dir">{{ s.work_dir || '—' }}</span>
                  </span>

                </span>

                <span class="sf-footer">
                  <span
                    class="sf-pill"
                    [style.color]="meta(s).color"
                    [style.background]="tint(meta(s).color, 0.13)"
                  >
                    <span
                      class="sf-stat-icon"
                      [class.sf-stat-pulse]="isActiveIcon(s)"
                      [style.color]="meta(s).color"
                    >
                      @switch (statusIcon(s)) {
                        @case ('think') {
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" /></svg>
                        }
                        @case ('code') {
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 9l-3 3 3 3M16 9l3 3-3 3" /></svg>
                        }
                        @case ('analyze') {
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="6" /><path d="M20 20l-3.5-3.5" /></svg>
                        }
                        @case ('run') {
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 8l4 4-4 4M12 16h6" /></svg>
                        }
                        @case ('wait') {
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 4h10M7 20h10M8 4c0 4 8 6 8 8s-8 4-8 8M16 4c0 4-8 6-8 8" /></svg>
                        }
                        @case ('done') {
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12l4 4 10-10" /></svg>
                        }
                        @case ('play') {
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M9 7l8 5-8 5z" /></svg>
                        }
                        @default {
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M9 9h6v6H9z" /></svg>
                        }
                      }
                    </span>
                    {{ statusLabel(s) }}
                  </span>
                  @if (timeAgo(s)) {
                    <span class="sf-time">{{ timeAgo(s) }}</span>
                  }
                </span>
                </span>
              </button>
            </li>
          }
        </ul>
      }

      @if (selectionMode()) {
        <div class="sf-actionbar" role="region" aria-label="Ações de seleção">
          <div class="sf-actionbar__inner">
            <span class="sf-actionbar__count">
              {{ selectedCount() }} selecionada{{ selectedCount() === 1 ? '' : 's' }}
            </span>
            <button
              type="button"
              class="sf-ab-btn sf-ab-btn--ghost"
              (click)="selectAllVisible()"
              [disabled]="purging() || visibleSessions().length === 0"
            >
              Selecionar todas
            </button>
            <span class="sf-actionbar__spacer"></span>
            <button
              type="button"
              class="sf-ab-btn sf-ab-btn--ghost"
              (click)="exitSelection()"
              [disabled]="purging()"
            >
              Cancelar
            </button>
            <button
              type="button"
              class="sf-ab-btn sf-ab-btn--danger"
              (click)="confirmBulkDelete()"
              [disabled]="selectedCount() === 0 || purging()"
              [attr.aria-label]="'Excluir ' + selectedCount() + ' sessões selecionadas'"
            >
              @if (purging()) {
                <span class="sf-ab-spin" aria-hidden="true"></span>
                Excluindo…
              } @else {
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m2 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                  <path d="M10 11v6M14 11v6" />
                </svg>
                Excluir
              }
            </button>
          </div>
        </div>
      }
    </section>
  `,
  styles: [
    `
      :host {
        display: block;
        background: #0e1113;
        min-height: 100%;
        color: #e7eae9;
      }
      .sf-sessoes {
        padding: 6px 20px 120px;
        max-width: 720px;
        margin: 0 auto;
      }
      .sf-head {
        padding: 10px 0 16px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
      }
      .sf-title {
        margin: 0;
        font-size: 28px;
        font-weight: 700;
        color: #f4f5f7;
        letter-spacing: -0.6px;
      }
      /* Toggle "Selecionar" no cabeçalho — mesmo visual dos chips. */
      .sf-select-toggle {
        flex: 0 0 auto;
        display: inline-flex;
        align-items: center;
        gap: 6px;
        appearance: none;
        border: 1px solid #283230;
        background: #181c1b;
        color: #c9cdd6;
        font: inherit;
        font-size: 13.5px;
        font-weight: 600;
        padding: 8px 14px;
        border-radius: 11px;
        cursor: pointer;
        -webkit-tap-highlight-color: transparent;
        transition: background 0.15s, color 0.15s, border-color 0.15s, transform 0.1s;
      }
      .sf-select-toggle:active {
        transform: scale(0.97);
      }
      .sf-select-toggle.is-active {
        background: #1d2221;
        border-color: #34403d;
        color: #f4f5f7;
      }

      .sf-search {
        position: relative;
        display: flex;
        align-items: center;
        width: 100%;
        margin: 0 0 14px;
      }
      .sf-search__icon {
        position: absolute;
        left: 12px;
        color: #6b7180;
        pointer-events: none;
      }
      .sf-search__input {
        width: 100%;
        height: 40px;
        box-sizing: border-box;
        appearance: none;
        border: 1px solid #283230;
        background: #15191a;
        color: #f4f5f7;
        font: inherit;
        font-size: 14.5px;
        padding: 0 38px 0 38px;
        border-radius: 12px;
        outline: none;
        transition: border-color 0.15s, background 0.15s;
      }
      .sf-search__input::placeholder {
        color: #6b7180;
      }
      .sf-search__input:focus {
        border-color: #34d399;
        background: #181c1b;
      }
      .sf-search__clear {
        position: absolute;
        right: 8px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 26px;
        height: 26px;
        border: none;
        background: none;
        border-radius: 8px;
        color: #8a90a0;
        cursor: pointer;
        -webkit-tap-highlight-color: transparent;
      }
      .sf-search__clear:hover {
        color: #f4f5f7;
      }

      .sf-chips {
        display: flex;
        gap: 8px;
        overflow-x: auto;
        margin: 0 -20px 18px;
        padding: 0 20px;
        scrollbar-width: none;
      }
      .sf-chips::-webkit-scrollbar {
        display: none;
      }
      .sf-chip {
        flex: 0 0 auto;
        appearance: none;
        border: 1px solid #283230;
        background: #181c1b;
        color: #c9cdd6;
        font: inherit;
        font-size: 13.5px;
        font-weight: 600;
        padding: 8px 15px;
        border-radius: 11px;
        cursor: pointer;
        transition: background 0.15s, color 0.15s, border-color 0.15s;
      }
      .sf-chip.is-active {
        background: #00e4b4;
        border-color: #00e4b4;
        color: #04140f;
      }
      /* Press feedback on filter chips. */
      .sf-chip:active {
        transform: scale(0.97);
      }

      .sf-msg {
        color: #9aa0ae;
        font-size: 14px;
        padding: 24px 4px;
      }
      .sf-msg--error {
        color: #f87171;
      }

      .sf-empty {
        text-align: center;
        padding: 56px 16px;
        color: #9aa0ae;
      }
      .sf-empty__title {
        margin: 0 0 6px;
        font-size: 15px;
        font-weight: 600;
        color: #e7eae9;
      }
      .sf-empty__sub {
        margin: 0;
        font-size: 13px;
      }

      .sf-list {
        list-style: none;
        margin: 0;
        padding: 0;
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      /* Telas largas: cards lado a lado em grid (não esticam 1 por linha). */
      @media (min-width: 768px) {
        .sf-list {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
        }
      }
      /* Desktop: solta o limite de 720px p/ o grid usar a largura toda. */
      @media (min-width: 1024px) {
        .sf-sessoes {
          max-width: none;
        }
      }
      .sf-card-wrap {
        position: relative;
        overflow: hidden;
        border-radius: 18px;
      }
      /* Red "Eliminar" action sitting BEHIND the card on the right. */
      .sf-delete {
        position: absolute;
        top: 0;
        right: 0;
        bottom: 0;
        z-index: 0;
        width: 104px;
        display: flex;
        align-items: center;
        justify-content: center;
        appearance: none;
        border: none;
        /* Soft danger gradient + subtle inner depth; corners are clipped by
           the wrapper's overflow:hidden + radius so this sits flush. */
        background: linear-gradient(135deg, #7f1d1d, #b91c1c);
        box-shadow: inset 1px 0 0 rgba(0, 0, 0, 0.25),
          inset 0 1px 0 rgba(255, 255, 255, 0.04);
        color: #fecaca;
        cursor: pointer;
        -webkit-tap-highlight-color: transparent;
      }
      .sf-delete__inner {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 5px;
        /* Gently scale + fade in as the row reveals the action. */
        opacity: 0;
        transform: scale(0.85);
        transition: opacity 0.22s cubic-bezier(0.22, 1, 0.36, 1),
          transform 0.22s cubic-bezier(0.22, 1, 0.36, 1);
        will-change: opacity, transform;
      }
      .sf-delete.is-open .sf-delete__inner {
        opacity: 1;
        transform: scale(1);
      }
      .sf-delete__label {
        font-size: 11.5px;
        font-weight: 700;
        letter-spacing: 0.2px;
      }
      @media (prefers-reduced-motion: reduce) {
        .sf-delete__inner {
          transition: none;
        }
      }
      .sf-fav {
        position: absolute;
        top: 19px;
        right: 12px;
        z-index: 2;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 30px;
        height: 30px;
        border: none;
        background: none;
        border-radius: 8px;
        color: #5a6072;
        cursor: pointer;
        -webkit-tap-highlight-color: transparent;
        transition: transform 0.22s cubic-bezier(0.22, 1, 0.36, 1);
        will-change: transform;
      }
      .sf-fav.on {
        color: #fbbf24;
      }
      .sf-card {
        position: relative;
        z-index: 1;
        display: block;
        width: 100%;
        text-align: left;
        appearance: none;
        border: 1px solid #283230;
        background: #181c1b;
        color: inherit;
        font: inherit;
        padding: 15px 16px;
        border-radius: 18px;
        cursor: pointer;
        touch-action: pan-y;
        transition: border-color 0.15s, background 0.15s,
          transform 0.22s cubic-bezier(0.22, 1, 0.36, 1);
        will-change: transform;
      }
      /* No transform animation while finger/mouse is actively dragging. */
      .sf-card.is-dragging {
        transition: border-color 0.15s, background 0.15s;
      }
      .sf-card:active {
        background: #1d2221;
      }
      .sf-card:hover {
        border-color: #34403d;
      }
      /* Modo seleção: sem gesto de swipe, cursor de escolha. */
      .sf-card.is-selecting {
        touch-action: pan-y;
      }
      .sf-card.is-selected {
        border-color: #00e4b4;
        background: #12211d;
      }
      /* Checkbox circular à esquerda (só no modo seleção). */
      .sf-check {
        flex: 0 0 auto;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 24px;
        height: 24px;
        border-radius: 50%;
        border: 2px solid #3a453f;
        color: #04140f;
        background: transparent;
        transition: background 0.15s, border-color 0.15s;
      }
      .sf-check.on {
        background: #00e4b4;
        border-color: #00e4b4;
      }
      /* Aguardando AÇÃO do usuário: fundo âmbar sutil + brilho neon pulsante
         (a cor "aguardando" da paleta) — "tô parado esperando você". */
      .sf-card.is-waiting {
        border-color: #4a3a16;
        background: #1b1710;
        animation: sf-wait-glow 2.1s ease-in-out infinite;
      }
      /* Tarefa concluída: destaque verde pulsante por alguns segundos. */
      .sf-card.sf-flash {
        border-color: #1f7a5c;
        animation: sf-task-glow 1s ease-in-out 3;
      }
      @keyframes sf-task-glow {
        0%,
        100% {
          box-shadow: 0 0 0 1px rgba(0, 228, 180, 0.25);
        }
        50% {
          box-shadow: 0 0 18px 2px rgba(0, 228, 180, 0.55);
        }
      }
      @keyframes sf-wait-glow {
        0%,
        100% {
          box-shadow:
            0 0 0 1px rgba(251, 191, 36, 0.22),
            0 0 12px -3px rgba(251, 191, 36, 0.28);
        }
        50% {
          box-shadow:
            0 0 0 1px rgba(251, 191, 36, 0.55),
            0 0 22px 0 rgba(251, 191, 36, 0.5);
        }
      }
      @media (prefers-reduced-motion: reduce) {
        .sf-card.is-waiting {
          animation: none;
          box-shadow: 0 0 0 1px rgba(251, 191, 36, 0.45);
        }
      }

      /* Press feedback lives on an INNER wrapper, NOT on .sf-card itself.
         The swipe gesture drives an inline translateX on .sf-card; putting a
         scale here keeps the two transforms on separate elements so the tap
         scale never fights the swipe drag/snap. While dragging we also drop the
         scale transition so a press during a swipe stays silent. */
      .sf-press {
        display: block;
        transition: transform 120ms cubic-bezier(0.22, 1, 0.36, 1);
        will-change: transform;
      }
      .sf-card:active .sf-press {
        transform: scale(0.97);
      }
      .sf-card.is-dragging .sf-press {
        transform: none;
        transition: none;
      }

      .sf-row {
        display: flex;
        align-items: center;
        gap: 11px;
        /* Reserva o canto direito para a estrela de favorito (evita o nome
           passar por baixo do ícone). */
        padding-right: 32px;
      }

      .sf-avatar {
        flex: 0 0 auto;
        width: 38px;
        height: 38px;
        border-radius: 11px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 12px;
        font-weight: 800;
        letter-spacing: 0.3px;
      }

      .sf-info {
        display: flex;
        flex-direction: column;
        min-width: 0;
        flex: 1;
      }
      .sf-name-row {
        display: flex;
        align-items: center;
        gap: 8px;
        min-width: 0;
      }
      .sf-name {
        font-size: 16px;
        font-weight: 600;
        color: #f4f5f7;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      /* Alto-falante "falando agora": ondas pulsando enquanto o áudio toca. */
      .sf-speaker {
        flex: none;
        display: inline-flex;
        align-items: center;
        color: #00e4b4;
      }
      .sf-speaker .sf-wave {
        transform-origin: 9px 12px;
        animation: sf-speaker-wave 1.1s ease-in-out infinite;
      }
      .sf-speaker .sf-wave2 {
        animation-delay: 0.18s;
      }
      @keyframes sf-speaker-wave {
        0%, 100% {
          opacity: 0.35;
        }
        50% {
          opacity: 1;
        }
      }
      @media (prefers-reduced-motion: reduce) {
        .sf-speaker .sf-wave {
          animation: none;
          opacity: 0.9;
        }
      }
      .sf-worker-chip {
        flex: none;
        display: inline-flex;
        align-items: center;
        gap: 4px;
        font-size: 10.5px;
        font-weight: 700;
        letter-spacing: 0.3px;
        color: #c084fc;
        background: rgba(192, 132, 252, 0.14);
        border: 1px solid rgba(192, 132, 252, 0.3);
        padding: 2px 7px;
        border-radius: 7px;
        white-space: nowrap;
      }
      .sf-dir {
        font-size: 12.5px;
        color: #7a8090;
        margin-top: 2px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      /* Chip discreto "↳ delegada por <pai>" (sessões filhas delegadas). */
      .sf-parent-chip {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        max-width: 100%;
        margin-top: 4px;
        font-size: 11px;
        font-weight: 600;
        color: #7dd3fc;
        background: rgba(56, 189, 248, 0.12);
        border: 1px solid rgba(56, 189, 248, 0.28);
        padding: 2px 8px;
        border-radius: 7px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .sf-parent-chip svg {
        flex: none;
      }
      .mono {
        font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, 'SF Mono',
          Menlo, Consolas, monospace;
      }

      .sf-footer {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-top: 13px;
        padding-top: 13px;
        border-top: 1px solid #23262f;
        gap: 10px;
        min-width: 0;
      }
      .sf-pill {
        display: inline-flex;
        align-items: center;
        gap: 7px;
        font-size: 12.5px;
        font-weight: 600;
        padding: 4px 10px;
        border-radius: 8px;
        white-space: nowrap;
      }
      .sf-stat-icon {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        flex: 0 0 auto;
        line-height: 0;
      }
      .sf-stat-icon svg {
        display: block;
      }
      .sf-stat-pulse {
        animation: sf-icon-pulse 1.4s ease-in-out infinite;
      }
      @keyframes sf-icon-pulse {
        0%,
        100% {
          opacity: 1;
        }
        50% {
          opacity: 0.6;
        }
      }
      @media (prefers-reduced-motion: reduce) {
        .sf-stat-pulse {
          animation: none;
        }
      }
      .sf-time {
        font-size: 12.5px;
        color: #6b7180;
        white-space: nowrap;
        margin-left: auto;
      }

      /* Barra de ação fixa, logo acima da bottom-nav (64px + safe-area). */
      .sf-actionbar {
        position: fixed;
        left: 0;
        right: 0;
        bottom: calc(64px + env(safe-area-inset-bottom, 0px));
        z-index: 45;
        display: flex;
        justify-content: center;
        padding: 8px 12px;
        pointer-events: none;
      }
      .sf-actionbar__inner {
        pointer-events: auto;
        display: flex;
        align-items: center;
        gap: 8px;
        width: 100%;
        max-width: 560px;
        padding: 10px 12px;
        background: #14181a;
        border: 1px solid #283230;
        border-radius: 16px;
        box-shadow: 0 12px 32px -8px rgba(0, 0, 0, 0.65);
        animation: sf-ab-in 0.2s cubic-bezier(0.22, 1, 0.36, 1);
      }
      @keyframes sf-ab-in {
        from {
          opacity: 0;
          transform: translateY(12px);
        }
        to {
          opacity: 1;
          transform: translateY(0);
        }
      }
      .sf-actionbar__count {
        font-size: 13.5px;
        font-weight: 700;
        color: #f4f5f7;
        white-space: nowrap;
      }
      .sf-actionbar__spacer {
        flex: 1;
      }
      .sf-ab-btn {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        appearance: none;
        border: 1px solid #283230;
        background: #1d2221;
        color: #c9cdd6;
        font: inherit;
        font-size: 13.5px;
        font-weight: 600;
        padding: 9px 13px;
        border-radius: 11px;
        cursor: pointer;
        white-space: nowrap;
        -webkit-tap-highlight-color: transparent;
        transition: background 0.15s, color 0.15s, border-color 0.15s, transform 0.1s;
      }
      .sf-ab-btn:active:not(:disabled) {
        transform: scale(0.97);
      }
      .sf-ab-btn:disabled {
        opacity: 0.45;
        cursor: default;
      }
      .sf-ab-btn--ghost {
        background: transparent;
      }
      .sf-ab-btn--danger {
        background: linear-gradient(135deg, #b91c1c, #dc2626);
        border-color: #dc2626;
        color: #fff;
      }
      .sf-ab-btn--danger:disabled {
        background: #3a2323;
        border-color: #4a2c2c;
        color: #d9a8a8;
      }
      .sf-ab-spin {
        width: 14px;
        height: 14px;
        flex: none;
        border-radius: 50%;
        border: 2px solid rgba(255, 255, 255, 0.35);
        border-top-color: #fff;
        animation: sf-spin 0.7s linear infinite;
      }
      @keyframes sf-spin {
        to {
          transform: rotate(360deg);
        }
      }
      @media (prefers-reduced-motion: reduce) {
        .sf-actionbar__inner,
        .sf-ab-spin {
          animation: none;
        }
      }
      /* Telas estreitas: some o "Selecionar todas" pra caber os botões-chave. */
      @media (max-width: 420px) {
        .sf-actionbar__inner {
          gap: 6px;
          padding: 9px 10px;
        }
        .sf-ab-btn {
          padding: 9px 11px;
        }
      }

      /* Respect reduced-motion: disable entrance + press feedback.
         Note: the swipe transform on .sf-card is driven by inline style and
         user intent, so we intentionally leave it untouched. */
      @media (prefers-reduced-motion: reduce) {
        .sf-card-wrap.sf-enter {
          animation: none !important;
        }
        .sf-press,
        .sf-chip {
          transition: none !important;
          transform: none !important;
        }
      }
    `,
  ],
})
export class SessoesComponent {
  private readonly api = inject(ApiService);
  private readonly sse = inject(SseService);
  private readonly jarvis = inject(JarvisAudioService);
  private readonly router = inject(Router);

  /** True quando o áudio (JARVIS) tocando agora é DESTA sessão → mostra o ícone. */
  protected isSpeaking(s: Session): boolean {
    return !!s.tmux_name && this.jarvis.speakingSessionId() === s.tmux_name;
  }

  /** True por alguns segundos após ESTA sessão concluir uma tarefa → destaca. */
  protected taskFlash(s: Session): boolean {
    return !!s.tmux_name && this.sse.taskDoneFlash() === s.tmux_name;
  }
  private readonly destroyRef = inject(DestroyRef);

  protected readonly filters = FILTERS;

  /** Staggered entrance delay per list index, capped so long lists don't lag. */
  protected enterDelay(i: number): string {
    return Math.min(i * 28, 220) + 'ms';
  }

  /** Filtro ativo. Vazio = "todas" (sem chip marcado; não há mais chip "Todas"). */
  protected readonly activeKey = signal<string>('');
  protected readonly loading = signal<boolean>(true);
  protected readonly error = signal<boolean>(false);

  /** Texto de busca livre (case-insensitive) por nome da sessão. */
  protected readonly query = signal<string>('');

  // ── Seleção múltipla + excluir em massa ─────────────────────────────────
  /** Modo seleção ativo: o tap no card ALTERNA a seleção em vez de abrir. */
  protected readonly selectionMode = signal<boolean>(false);
  /** Ids das sessões marcadas para exclusão. */
  protected readonly selectedIds = signal<Set<string>>(new Set<string>());
  /** True enquanto o batch de purge está em andamento (trava a barra). */
  protected readonly purging = signal<boolean>(false);
  /** Quantidade selecionada (dirige o rótulo "N selecionada(s)"). */
  protected readonly selectedCount = computed(() => this.selectedIds().size);

  protected isSelected(s: Session): boolean {
    return this.selectedIds().has(s.id);
  }

  /** Entra/sai do modo seleção. Ao entrar, fecha qualquer swipe aberto. */
  protected toggleSelectionMode(): void {
    if (this.selectionMode()) {
      this.exitSelection();
      return;
    }
    this.closeAll();
    this.selectionMode.set(true);
  }

  /** Sai do modo seleção e limpa a seleção. */
  protected exitSelection(): void {
    this.selectionMode.set(false);
    this.selectedIds.set(new Set<string>());
  }

  /** Alterna a marcação de uma sessão. */
  protected toggleSelect(s: Session): void {
    this.selectedIds.update((set) => {
      const next = new Set(set);
      if (next.has(s.id)) {
        next.delete(s.id);
      } else {
        next.add(s.id);
      }
      return next;
    });
  }

  /** Marca todas as sessões atualmente visíveis (respeita filtro/busca). */
  protected selectAllVisible(): void {
    this.selectedIds.set(new Set(this.visibleSessions().map((s) => s.id)));
  }

  /**
   * Exclui em massa: confirma, purga cada sessão em paralelo (erro por-item não
   * derruba o lote), remove as bem-sucedidas de forma otimista e recarrega.
   */
  protected confirmBulkDelete(): void {
    if (this.purging()) {
      return;
    }
    const ids = [...this.selectedIds()];
    if (ids.length === 0) {
      return;
    }
    const ok = confirm(
      `Excluir ${ids.length} sessão${ids.length === 1 ? '' : 'ões'}? ` +
        'Mata no Mac e remove daqui — não dá pra desfazer.',
    );
    if (!ok) {
      return;
    }

    this.purging.set(true);
    const calls = ids.map((id) =>
      this.api.purgeSession(id).pipe(
        map(() => ({ id, ok: true })),
        catchError(() => of({ id, ok: false })),
      ),
    );

    forkJoin(calls)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((results) => {
        // Remove otimista as que deram certo; mantém as que falharam.
        const okIds = new Set(results.filter((r) => r.ok).map((r) => r.id));
        this.sessions.update((list) => list.filter((x) => !okIds.has(x.id)));
        this.purging.set(false);
        this.exitSelection();
        // Recarrega do servidor no filtro atual (pega o estado real; o purge
        // roda em background no worker).
        const chip = this.filters.find((c) => c.key === this.activeKey());
        this.load(chip?.status);
      });
  }

  /** All sessions fetched from the API (live status applied via SSE). */
  protected readonly sessions = signal<Session[]>([]);

  /** Sessions matching the active filter (client-side so SSE keeps it live). */
  protected readonly visibleSessions = computed<Session[]>(() => {
    const key = this.activeKey();
    const chip = this.filters.find((c) => c.key === key);
    const wanted = chip?.status;
    const list = this.sessions();
    const byFilter =
      key === 'favorites'
        ? list.filter((s) => !!s.favorite)
        : wanted
          ? list.filter((s) => s.status === wanted)
          : list;
    // Busca por nome (case-insensitive) combinada com o filtro/chip ativo.
    const q = this.query().trim().toLowerCase();
    const filtered = q
      ? byFilter.filter(
          (s) =>
            (s.tmux_name ?? '').toLowerCase().includes(q) ||
            (s.display_name ?? '').toLowerCase().includes(q),
        )
      : byFilter;
    // Favoritas primeiro (mantém a ordem original dentro de cada grupo).
    return [...filtered].sort(
      (a, b) => (b.favorite ? 1 : 0) - (a.favorite ? 1 : 0),
    );
  });

  // ── Swipe-to-delete (iOS style) ─────────────────────────────────────────
  /** Limites do arrasto. Aberto = -88px; gatilho de snap = -72px. */
  private static readonly OPEN = -88;
  private static readonly SNAP = -72;
  private static readonly CLAMP = -96;
  private static readonly TAP_THRESHOLD = 8;

  /** translateX por sessão (px). 0 = fechado. */
  private readonly offsets = signal<Map<string, number>>(new Map());
  /** Id da linha em arrasto ativo (desabilita a transição CSS). */
  protected readonly dragId = signal<string | null>(null);

  /** Estado transitório do gesto em andamento. */
  private gesture: {
    id: string;
    startX: number;
    startY: number;
    baseOffset: number;
    locked: boolean; // true = decidido que é swipe horizontal
    moved: boolean; // passou do threshold → suprime o click
    cancelled: boolean; // virou scroll vertical → ignora
  } | null = null;

  /** Offset atual (px) de uma linha; 0 quando fechada. */
  protected offset(id: string): number {
    return this.offsets().get(id) ?? 0;
  }

  private setOffset(id: string, value: number): void {
    this.offsets.update((m) => {
      const next = new Map(m);
      if (value === 0) {
        next.delete(id);
      } else {
        next.set(id, value);
      }
      return next;
    });
  }

  /** Fecha todas as linhas, opcionalmente exceto uma. */
  private closeAll(except?: string): void {
    this.offsets.update((m) => {
      if (m.size === 0) {
        return m;
      }
      const next = new Map<string, number>();
      if (except && m.has(except)) {
        next.set(except, m.get(except)!);
      }
      return next;
    });
  }

  protected onPointerDown(s: Session, ev: PointerEvent): void {
    // No modo seleção não há swipe: o tap só marca/desmarca.
    if (this.selectionMode()) {
      return;
    }
    // Só botão primário do mouse / toque / caneta.
    if (ev.pointerType === 'mouse' && ev.button !== 0) {
      return;
    }
    this.gesture = {
      id: s.id,
      startX: ev.clientX,
      startY: ev.clientY,
      baseOffset: this.offset(s.id),
      locked: false,
      moved: false,
      cancelled: false,
    };
  }

  protected onPointerMove(s: Session, ev: PointerEvent): void {
    const g = this.gesture;
    if (!g || g.id !== s.id || g.cancelled) {
      return;
    }
    const dx = ev.clientX - g.startX;
    const dy = ev.clientY - g.startY;

    // Decide direção no primeiro movimento relevante.
    if (!g.locked) {
      if (Math.abs(dx) < 4 && Math.abs(dy) < 4) {
        return;
      }
      // Scroll vertical vence → deixa a página rolar, aborta o swipe.
      if (Math.abs(dy) > Math.abs(dx)) {
        g.cancelled = true;
        return;
      }
      g.locked = true;
      this.dragId.set(s.id);
      // Fecha outras linhas ao começar a abrir esta.
      this.closeAll(s.id);
      (ev.target as Element).setPointerCapture?.(ev.pointerId);
    }

    if (Math.abs(dx) > SessoesComponent.TAP_THRESHOLD) {
      g.moved = true;
    }

    let next = g.baseOffset + dx;
    // Rubber-band suave além dos limites.
    if (next > 0) {
      next = next * 0.25; // resiste ao arrasto para a direita
    } else if (next < SessoesComponent.CLAMP) {
      const over = next - SessoesComponent.CLAMP;
      next = SessoesComponent.CLAMP + over * 0.25;
    }
    this.setOffset(s.id, next);
  }

  protected onPointerUp(s: Session, ev: PointerEvent): void {
    const g = this.gesture;
    if (!g || g.id !== s.id) {
      return;
    }
    this.gesture = null;
    this.dragId.set(null);
    if (g.cancelled || !g.locked) {
      return;
    }
    (ev.target as Element).releasePointerCapture?.(ev.pointerId);
    // Houve arrasto horizontal → suprime o click que vem logo a seguir.
    if (g.moved) {
      this.lastWasSwipe = true;
    }
    // Snap: passou de -72 abre; senão fecha.
    const cur = this.offset(s.id);
    this.setOffset(s.id, cur <= SessoesComponent.SNAP ? SessoesComponent.OPEN : 0);
  }

  /** Um TAP normal abre a sessão; um swipe (moveu > threshold) é suprimido. */
  protected onCardClick(s: Session, ev: MouseEvent): void {
    // Modo seleção: o clique alterna a marcação em vez de abrir a sessão.
    if (this.selectionMode()) {
      ev.preventDefault();
      ev.stopPropagation();
      this.toggleSelect(s);
      return;
    }
    const moved = this.offset(s.id) !== 0;
    if (this.lastWasSwipe || moved) {
      ev.preventDefault();
      ev.stopPropagation();
      // Se estava aberta, um toque na linha fecha em vez de abrir.
      if (moved) {
        this.setOffset(s.id, 0);
      }
      this.lastWasSwipe = false;
      return;
    }
    this.open(s);
  }

  /** Sinaliza que o último gesto foi um swipe (setado no pointerup). */
  private lastWasSwipe = false;

  /** Tap no botão vermelho: confirma, remove otimista e chama purge. */
  protected confirmEliminate(s: Session): void {
    const name = this.displayName(s);
    const ok = confirm(
      'Eliminar a sessão "' +
        name +
        '"? Mata no Mac e remove daqui — não dá pra desfazer.',
    );
    if (!ok) {
      this.setOffset(s.id, 0); // snap back
      return;
    }

    // Remoção otimista (guarda p/ rollback).
    const prev = this.sessions();
    this.setOffset(s.id, 0);
    this.sessions.update((list) => list.filter((x) => x.id !== s.id));

    this.api
      .purgeSession(s.id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        error: () => {
          // Rollback: restaura o estado anterior e recarrega do servidor.
          this.sessions.set(prev);
          this.load();
        },
      });
  }

  /** Favorita/desfavorita (otimista + persiste no servidor). */
  protected toggleFav(s: Session): void {
    const next = !s.favorite;
    this.sessions.update((list) =>
      list.map((x) => (x.id === s.id ? { ...x, favorite: next } : x)),
    );
    this.api.setFavorite(s.id, next).subscribe({
      error: () =>
        this.sessions.update((list) =>
          list.map((x) => (x.id === s.id ? { ...x, favorite: !next } : x)),
        ),
    });
  }

  constructor() {
    this.load();

    // SSE drives live status updates. Each new event re-applies any status
    // changes carried on the session feed; we re-read the broker's events.
    this.sse.connect();
    this.destroyRef.onDestroy(() => this.sse.disconnect());

    // React to live frames: when an event references a known session, refresh
    // the list so statuses stay current without a manual reload.
    effect(() => {
      const last = this.sse.lastEvent();
      if (last && 'session_id' in last && last.session_id) {
        this.refreshStatuses();
      }
    });
  }

  protected selectFilter(chip: FilterChip): void {
    // Toggle: clicar no chip já ativo desmarca → volta a "todas" (vazio).
    const isActive = this.activeKey() === chip.key;
    this.activeKey.set(isActive ? '' : chip.key);
    // Refetch scoped to the chosen status (server-side filter); ao desmarcar,
    // recarrega sem filtro. Cai p/ filtro client-side enquanto o request roda.
    this.load(isActive ? undefined : chip.status);
  }

  protected open(s: Session): void {
    void this.router.navigate(['/sessao', s.id]);
  }

  /** Worker/sub-agente pela convenção de nome (mostra chip ⑂ worker). */
  protected isWorker(s: Session): boolean {
    return isWorkerSession(s.tmux_name ?? s.display_name);
  }

  /** True se esta sessão foi DELEGADA por outra (tem um pai registrado). */
  protected hasParent(s: Session): boolean {
    return !!(s.parent && s.parent.trim());
  }

  /**
   * Rótulo do pai p/ o chip "↳ delegada por X": usa o ``display_name`` do pai
   * se ele estiver na lista; senão o próprio ``parent`` (tmux_name).
   */
  protected parentLabel(s: Session): string {
    const p = (s.parent || '').trim();
    if (!p) {
      return '';
    }
    const parent = this.sessions().find((x) => x.tmux_name === p);
    return parent?.display_name || p;
  }

  protected agent(s: Session) {
    return agentMeta(s.agent_type);
  }

  protected meta(s: Session) {
    return STATUS_META[s.status] ?? STATUS_META.detached;
  }

  /**
   * Texto do pill de status. Para sessões RODANDO com um ``activity`` derivado
   * (ex.: "Codificando", "Pensando"), mostra esse rótulo fino no lugar do
   * genérico "Executando"; mantém a cor/ponto do status. Demais estados usam o
   * label normal do STATUS_META.
   */
  protected statusLabel(s: Session): string {
    if (s.status === 'running' && s.activity) {
      return s.activity;
    }
    return this.meta(s).label;
  }

  /**
   * Ícone expressivo do status do card, escolhido pela ``activity`` (sessões
   * rodando) ou pelo status bruto. Mapeia para uma das chaves do template SVG.
   */
  protected statusIcon(
    s: Session,
  ): 'think' | 'code' | 'analyze' | 'run' | 'wait' | 'done' | 'play' | 'stopped' {
    if (s.status === 'waiting_input') {
      return 'wait';
    }
    if (s.status === 'completed') {
      return 'done';
    }
    if (s.status === 'stopped' || s.status === 'detached') {
      return 'stopped';
    }
    if (s.status === 'running') {
      switch (s.activity) {
        case 'Pensando':
          return 'think';
        case 'Codificando':
          return 'code';
        case 'Analisando':
          return 'analyze';
        case 'Rodando comando':
          return 'run';
        case 'Aguardando você':
          return 'wait';
        case 'Concluído':
          return 'done';
        default:
          return 'play';
      }
    }
    return 'stopped';
  }

  /** Ícones "vivos" (com pulse sutil) — estados de trabalho ativo. */
  protected isActiveIcon(s: Session): boolean {
    const k = this.statusIcon(s);
    return k === 'think' || k === 'code' || k === 'analyze' || k === 'run' || k === 'play';
  }

  /** Builds a translucent tint (`rgba`) from a `#rrggbb` color for backgrounds. */
  protected tint(hex: string, alpha: number): string {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
    if (!m) {
      return hex;
    }
    const n = parseInt(m[1], 16);
    const r = (n >> 16) & 255;
    const g = (n >> 8) & 255;
    const b = n & 255;
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  protected displayName(s: Session): string {
    return s.display_name || s.tmux_name || s.id;
  }

  /**
   * "Última atividade há X". Usa ``last_activity_at`` (instante real em que a
   * tela mudou / houve input) — NÃO ``updated_at``, que o worker bate todo ciclo
   * e mostraria "agora" sempre. Cai p/ updated/created só se faltar.
   */
  protected timeAgo(s: Session): string {
    const raw =
      s.last_activity_at ??
      (s['updated_at'] as string | undefined) ??
      (s['created_at'] as string | undefined);
    return fmtTimeAgo(raw);
  }

  private load(status?: SessionStatus): void {
    this.loading.set(true);
    this.error.set(false);
    this.api
      .listSessions(status)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (list) => {
          this.sessions.set(list ?? []);
          this.loading.set(false);
        },
        error: () => {
          this.loading.set(false);
          this.error.set(true);
        },
      });
  }

  /** Quietly refetch the current filter to pick up live status changes. */
  private refreshStatuses(): void {
    const chip = this.filters.find((c) => c.key === this.activeKey());
    this.api
      .listSessions(chip?.status)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (list) => this.sessions.set(list ?? []),
        error: () => {
          /* keep last known state on transient errors */
        },
      });
  }
}
