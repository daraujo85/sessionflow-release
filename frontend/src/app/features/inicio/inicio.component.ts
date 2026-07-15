import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { Router } from '@angular/router';
import { ApiService } from '../../core/api.service';
import { SseService } from '../../core/sse.service';
import { JarvisAudioService } from '../../core/jarvis-audio.service';
import { Session, SessionStatus, Task, TaskState } from '../../core/models';

/** Display state for a task row; extends TaskState with a derived "paused". */
type TaskDisplayState = TaskState | 'paused';
import { STATUS_META, agentMeta, isWorkerSession } from '../../shared/status-color';
import { timeAgo as fmtTimeAgo } from '../../shared/time-ago';
import { WorkersStore } from '../../core/workers-store';

/** Session statuses considered "active" on the home screen. */
const ACTIVE_STATUSES: readonly SessionStatus[] = ['running', 'waiting_input'];

/**
 * Home screen ("Início"). Shows a greeting, the live count of active sessions,
 * the active-session cards and the most recent tasks. Subscribes to the SSE
 * stream so the lists update live as the backend emits events.
 */
@Component({
  selector: 'sf-inicio',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="sf-inicio">
      <!-- Toast de "tarefa concluída" 🎉 (some sozinho). -->
      @if (sse.taskDoneToast(); as t) {
        <div class="sf-task-toast" role="status" aria-live="polite">
          <span class="sf-task-toast-emoji" aria-hidden="true">🎉</span>
          <span class="sf-task-toast-body">
            <span class="sf-task-toast-title">Tarefa concluída</span>
            <span class="sf-task-toast-sub">{{ t.title }} · {{ t.session }}</span>
          </span>
        </div>
      }
      <!-- Header -->
      <header class="sf-header">
        <div class="sf-brand">
          <span class="sf-logo" aria-hidden="true">
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="#06231d"
              stroke-width="2.6"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path d="M17 7H9a3 3 0 0 0 0 6h6a3 3 0 0 1 0 6H6" />
            </svg>
          </span>
          <span class="sf-brand-name">SessionFlow</span>
        </div>

        <!-- Custo global estimado (todas as sessões) — some se não há dado. -->
        @if (globalCost(); as g) {
          <button
            type="button"
            class="sf-cost-chip mono"
            [class.is-open]="costPanelOpen()"
            [title]="globalCostTip()"
            (click)="costPanelOpen.set(!costPanelOpen())"
            aria-label="Custo estimado de todas as sessões"
          >
            <span aria-hidden="true">💰</span>
            <span>{{ '~$' + fmtMoney(g.usd) }}</span>
            @if (g.brl != null) {
              <span class="sf-cost-chip-brl">· ~R$ {{ fmtMoney(g.brl) }}</span>
            }
          </button>
        }

        <button
          type="button"
          class="sf-bell"
          aria-label="Notificações"
          (click)="openNotifications()"
        >
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="#C9CDD6"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
            <path d="M13.7 21a2 2 0 0 1-3.4 0" />
          </svg>
          @if (notifCount() > 0) {
            <span class="sf-bell-badge">{{ notifCount() }}</span>
          }
        </button>
      </header>

      <!-- Mini-painel do custo global: quebra por modelo (abre pelo chip). -->
      @if (costPanelOpen() && globalCost(); as g) {
        <div class="sf-cost-panel">
          <div class="sf-cost-panel-head">
            <span class="sf-cost-panel-title">Custo estimado · todas as sessões</span>
            <span class="mono sf-cost-panel-total">
              {{ '~$' + fmtMoney(g.usd) }}
              @if (g.brl != null) {
                <span class="sf-cost-panel-brl">· ~R$ {{ fmtMoney(g.brl) }}</span>
              }
            </span>
          </div>
          <div class="mono sf-cost-panel-toks">
            in {{ fmtTok(g.tokensIn) }} · out {{ fmtTok(g.tokensOut) }}
          </div>
          @for (r of g.rows; track r.model) {
            <div class="sf-cost-row">
              <span class="sf-cost-model">{{ r.model }}</span>
              <span class="mono sf-cost-toks"
                >in {{ fmtTok(r.input) }} · out {{ fmtTok(r.output) }} · cache
                {{ fmtTok(r.cache_read + r.cache_write) }}</span
              >
              <span class="mono sf-cost-usd">{{
                r.usd != null ? '~$' + fmtUsd(r.usd) : '— (preço desconhecido)'
              }}</span>
            </div>
          }
          <div class="sf-cost-note">
            Estimativa em preço de API; sessões sem dado de custo não entram na
            soma.
            @if (g.rate != null) {
              Câmbio do dia: US$ 1 = R$ {{ fmtUsd(g.rate) }}.
            }
          </div>
        </div>
      }

      <!-- Greeting -->
      <h1 class="sf-greeting">{{ greeting() }}, Diego 👋</h1>
      <p class="sf-active-count">{{ activeCountLabel() }}</p>

      <!-- Active sessions -->
      <div class="sf-section-head">
        <h2>Sessões ativas</h2>
        <button type="button" class="sf-link" (click)="goSessoes()">
          Ver todas
        </button>
      </div>

      @if (activeSessions().length > 0) {
        <div class="sf-cards">
          @for (s of activeSessions(); track s.id; let i = $index) {
            <button
              type="button"
              class="sf-card sf-enter"
              [class.is-waiting]="s.status === 'waiting_input'"
              [class.sf-has-agents]="subAgentCount(s) > 0"
              [class.sf-flash]="taskFlash(s)"
              [style.animation-delay]="enterDelay(i)"
              (click)="openSession(s.id)"
            >
              <span
                class="sf-stat-icon"
                [class.sf-stat-pulse]="isActiveIcon(s)"
                [style.color]="statusMeta(s.status).color"
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
              <span class="sf-card-body">
                <span class="sf-card-top">
                  <span class="sf-card-name">{{ displayName(s) }}</span>
                  <span
                    class="sf-agent"
                    [style.color]="agent(s).color"
                    [style.background]="agentBg(s)"
                    >{{ agent(s).short }}</span
                  >
                  @if (isWorker(s)) {
                    <span class="sf-worker-chip" title="Worker / sub-agente">
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
                           stroke="currentColor" stroke-width="2" stroke-linecap="round"
                           stroke-linejoin="round" aria-hidden="true">
                        <path d="M12 8V4H8" />
                        <rect width="16" height="12" x="4" y="8" rx="2" />
                        <path d="M2 14h2M20 14h2M15 13v2M9 13v2" />
                      </svg>
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
                  @if (subAgentCount(s) > 0) {
                    <span class="sf-subagents" [title]="subAgentTip(s)"
                          [attr.aria-label]="subAgentCount(s) + ' sub-agentes rodando'">
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
                           stroke="currentColor" stroke-width="2" stroke-linecap="round"
                           stroke-linejoin="round" aria-hidden="true">
                        <path d="M12 8V4H8" /><rect width="16" height="12" x="4" y="8" rx="2" />
                        <path d="M2 14h2M20 14h2M15 13v2M9 13v2" />
                      </svg>
                      {{ subAgentCount(s) }}
                    </span>
                  }
                  @if (hostBadge(s); as host) {
                    <span class="sf-host-chip" [title]="'Roda em: ' + host">
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                           stroke="currentColor" stroke-width="2" stroke-linecap="round"
                           stroke-linejoin="round" aria-hidden="true">
                        <rect x="3" y="4" width="18" height="8" rx="2" />
                        <rect x="3" y="12" width="18" height="8" rx="2" />
                        <path d="M7 8h.01M7 16h.01" />
                      </svg>
                    </span>
                  }
                </span>
                <span class="sf-card-status" [style.color]="statusMeta(s.status).color">{{
                  statusLabel(s)
                }}</span>
                @if (timeAgo(s)) {
                  <span class="sf-card-time">· {{ timeAgo(s) }}</span>
                }
                <span class="sf-card-sub mono">{{ subline(s) }}</span>
              </span>
              <svg
                class="sf-chevron"
                width="20"
                height="20"
                viewBox="0 0 24 24"
                fill="none"
                stroke="#5A6072"
                stroke-width="2.2"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <path d="M9 6l6 6-6 6" />
              </svg>
            </button>
          }
        </div>
      } @else {
        <p class="sf-empty">Nenhuma sessão ativa no momento.</p>
      }

      <!-- Top 3 sessões por consumo de tokens (in+out) — só aparece quando há
           ao menos 1 sessão com métricas de uso (feature recente). -->
      @if (topTokenSessions().length > 0 || topTokensPeriod() !== 'all') {
        <div class="sf-section-head">
          <h2>Top consumo</h2>
          <div class="sf-period-tabs" role="group" aria-label="Período do Top consumo">
            @for (opt of topTokensPeriodOptions; track opt.key) {
              <button
                type="button"
                class="sf-period-tab"
                [class.is-on]="topTokensPeriod() === opt.key"
                (click)="topTokensPeriod.set(opt.key)"
              >
                {{ opt.label }}
              </button>
            }
          </div>
        </div>
        @if (topTokenSessions().length > 0) {
          <div class="sf-top-toks">
            @for (r of topTokenSessions(); track r.session.id; let i = $index) {
              <button
                type="button"
                class="sf-top-tok-row"
                (click)="openSession(r.session.id)"
              >
                <span class="sf-top-tok-rank">{{ i + 1 }}</span>
                <span class="sf-top-tok-name">{{ displayName(r.session) }}</span>
                <span class="mono sf-top-tok-toks"
                  >in {{ fmtTok(r.tokensIn) }} · out {{ fmtTok(r.tokensOut) }}</span
                >
                <span class="mono sf-top-tok-usd">{{
                  r.usd != null ? '~$' + fmtUsd(r.usd) : fmtTok(r.total) + 'tok'
                }}</span>
              </button>
            }
          </div>
        } @else {
          <p class="sf-empty">Sem consumo registrado nesse período.</p>
        }
      }

      <!-- Tarefas (marcos do agente, via .sessionflow/milestones.json) -->
      <div class="sf-section-head">
        <h2>Tarefas</h2>
        <button type="button" class="sf-link" (click)="goTimeline()">
          Ver todas
        </button>
      </div>

      @if (tasks().length > 0) {
        <div class="sf-task-filters">
          <div class="sf-tchips">
            @for (f of taskFilters; track f.key) {
              <button
                type="button"
                class="sf-tfilter"
                [class.sel]="taskStatus() === f.key"
                (click)="taskStatus.set(taskStatus() === f.key ? 'all' : f.key)"
              >
                {{ f.label }}
              </button>
            }
          </div>
          @if (taskSessions().length > 1) {
            <select
              class="sf-tsessel"
              (change)="taskSession.set($any($event.target).value)"
            >
              <option value="">Todas as sessões</option>
              @for (s of taskSessions(); track s) {
                <option [value]="s" [selected]="taskSession() === s">{{ s }}</option>
              }
            </select>
          }
        </div>
      }

      @if (recentTasks().length > 0) {
        <div class="sf-task-list">
          @for (t of recentTasks(); track t.id; let first = $first; let i = $index) {
            <div
              class="sf-task-wrap sf-enter"
              [style.animation-delay]="enterDelay(i)"
              [class.sf-task-divider]="!first"
            >
              <button
                type="button"
                class="sf-tdelete"
                [class.is-open]="taskOffset(t.id) <= -72"
                tabindex="-1"
                [attr.aria-hidden]="taskOffset(t.id) > -72"
                (click)="confirmEliminateTask(t)"
                aria-label="Apagar tarefa"
              >
                <span class="sf-tdelete__inner">
                  <svg width="19" height="19" viewBox="0 0 24 24" fill="none"
                       stroke="currentColor" stroke-width="2" stroke-linecap="round"
                       stroke-linejoin="round" aria-hidden="true">
                    <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m2 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                    <path d="M10 11v6M14 11v6" />
                  </svg>
                  <span class="sf-tdelete__label">Eliminar</span>
                </span>
              </button>
            <div
              class="sf-task"
              [class.is-dragging]="taskDragId() === t.id"
              [class.sf-task-clickable]="!!t.session_id"
              [style.transform]="'translateX(' + taskOffset(t.id) + 'px)'"
              (click)="onTaskClick(t, $event)"
              (pointerdown)="onTaskPointerDown(t, $event)"
              (pointermove)="onTaskPointerMove(t, $event)"
              (pointerup)="onTaskPointerUp(t, $event)"
              (pointercancel)="onTaskPointerUp(t, $event)"
            >
              <span
                class="sf-task-icon"
                [style.color]="taskMeta(effectiveTaskState(t)).color"
                [style.background]="taskMeta(effectiveTaskState(t)).bg"
              >
                @switch (effectiveTaskState(t)) {
                  @case ('done') {
                    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5" /></svg>
                  }
                  @case ('doing') {
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 6v6l4 2" /><circle cx="12" cy="12" r="9" /></svg>
                  }
                  @case ('paused') {
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="7" y="6" width="3.2" height="12" rx="1" /><rect x="13.8" y="6" width="3.2" height="12" rx="1" /></svg>
                  }
                  @case ('blocked') {
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9" /><path d="M5.6 5.6 18.4 18.4" /></svg>
                  }
                  @case ('attention') {
                    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8v5" /><path d="M12 17h.01" /><circle cx="12" cy="12" r="9" /></svg>
                  }
                  @default {
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.5" /></svg>
                  }
                }
              </span>
              <span class="sf-task-body">
                <span class="sf-task-title">{{ t.title }}</span>
                <span class="sf-task-meta" [style.color]="taskMeta(effectiveTaskState(t)).color">{{
                  taskMeta(effectiveTaskState(t)).label
                }}</span>
              </span>
              @if (t.state === 'todo') {
                <button
                  type="button"
                  class="sf-task-play"
                  aria-label="Iniciar tarefa"
                  (click)="startTask(t, $event)"
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z" /></svg>
                </button>
              }
              <span class="sf-task-session mono">{{ sessionShort(t) }}</span>
            </div>
            </div>
          }
          @if (remainingTasks() > 0) {
            <button type="button" class="sf-task-more" (click)="showMoreTasks()">
              Ver mais {{ remainingTasks() }}
            </button>
          }
        </div>
      } @else {
        <p class="sf-empty">
          {{ tasks().length > 0 ? 'Nenhuma tarefa com esse filtro.' : 'Nenhuma tarefa ainda.' }}
        </p>
      }
    </section>
  `,
  styles: [
    `
      :host {
        display: block;
      }

      .sf-inicio {
        padding: 6px 20px 120px;
      }

      /* Header */
      .sf-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 8px 0 18px;
      }
      .sf-brand {
        display: flex;
        align-items: center;
        gap: 11px;
      }
      .sf-logo {
        width: 36px;
        height: 36px;
        border-radius: 11px;
        background: linear-gradient(150deg, #2cecc4, #00a482);
        display: flex;
        align-items: center;
        justify-content: center;
        box-shadow: 0 6px 16px -4px rgba(0, 200, 160, 0.55);
      }
      .sf-brand-name {
        font-size: 20px;
        font-weight: 700;
        color: var(--text-strong);
        letter-spacing: -0.3px;
      }
      /* Chip discreto do custo global (header). */
      .sf-cost-chip {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        margin-left: auto;
        margin-right: 10px;
        min-width: 0;
        padding: 5px 10px;
        border-radius: 999px;
        border: 1px solid var(--border-default);
        background: var(--surface-card);
        color: #c9cdd6;
        font-size: 12px;
        font-weight: 700;
        white-space: nowrap;
        cursor: pointer;
        -webkit-tap-highlight-color: transparent;
      }
      .sf-cost-chip.is-open {
        border-color: rgba(0, 228, 180, 0.45);
        color: #e7eae9;
      }
      .sf-cost-chip-brl {
        color: #7a8090;
        font-weight: 600;
      }
      /* Telas estreitas: esconde o R$ pro header nunca quebrar (fica no painel). */
      @media (max-width: 459px) {
        .sf-cost-chip-brl {
          display: none;
        }
      }

      /* Mini-painel do custo global por modelo (mesmo dark dos cards). */
      .sf-cost-panel {
        background: var(--surface-card);
        border: 1px solid var(--border-default);
        border-radius: 14px;
        padding: 12px 14px;
        margin: 0 0 16px;
      }
      .sf-cost-panel-head {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 10px;
        margin-bottom: 8px;
      }
      .sf-cost-panel-title {
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.3px;
        text-transform: uppercase;
        color: #9aa0ae;
      }
      .sf-cost-panel-total {
        font-size: 12.5px;
        font-weight: 700;
        color: #00e4b4;
        white-space: nowrap;
      }
      .sf-cost-panel-brl {
        color: #7a8090;
        font-weight: 600;
      }
      .sf-cost-panel-toks {
        font-size: 11.5px;
        color: #7a8090;
        white-space: nowrap;
        margin-bottom: 6px;
      }
      .sf-cost-row {
        display: flex;
        align-items: baseline;
        gap: 8px;
        padding: 4px 0;
        min-width: 0;
      }
      .sf-cost-model {
        font-size: 13px;
        font-weight: 600;
        color: var(--text-strong);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        max-width: 40%;
      }
      .sf-cost-toks {
        flex: 1;
        min-width: 0;
        font-size: 11px;
        color: #7a8090;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .sf-cost-usd {
        flex: none;
        font-size: 12px;
        font-weight: 700;
        color: #00e4b4;
      }
      .sf-cost-note {
        margin-top: 8px;
        font-size: 11px;
        color: #6a7080;
        line-height: 1.45;
      }

      /* Filtro de período do Top consumo */
      .sf-period-tabs {
        display: flex;
        gap: 4px;
        background: var(--surface-card);
        border: 1px solid var(--border-default);
        border-radius: 10px;
        padding: 3px;
      }
      .sf-period-tab {
        appearance: none;
        background: transparent;
        border: none;
        border-radius: 8px;
        color: #9aa0ae;
        font-size: 12px;
        font-weight: 600;
        padding: 5px 10px;
        cursor: pointer;
      }
      .sf-period-tab.is-on {
        background: var(--color-accent-strong);
        color: var(--text-on-accent);
      }

      /* Top 3 sessões por consumo de tokens */
      .sf-top-toks {
        display: flex;
        flex-direction: column;
        gap: 8px;
        margin-bottom: 8px;
      }
      .sf-top-tok-row {
        display: flex;
        align-items: center;
        gap: 10px;
        width: 100%;
        background: var(--surface-card);
        border: 1px solid var(--border-default);
        border-radius: 12px;
        padding: 10px 12px;
        cursor: pointer;
        text-align: left;
      }
      .sf-top-tok-rank {
        flex: none;
        width: 20px;
        height: 20px;
        border-radius: 50%;
        background: rgba(0, 228, 180, 0.14);
        color: #00e4b4;
        font-size: 11.5px;
        font-weight: 800;
        display: flex;
        align-items: center;
        justify-content: center;
      }
      .sf-top-tok-name {
        flex: 1;
        min-width: 0;
        font-size: 13.5px;
        font-weight: 600;
        color: var(--text-strong);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .sf-top-tok-toks {
        flex: none;
        font-size: 11px;
        color: #7a8090;
        white-space: nowrap;
      }
      .sf-top-tok-usd {
        flex: none;
        font-size: 12px;
        font-weight: 700;
        color: #00e4b4;
        white-space: nowrap;
      }

      .sf-bell {
        position: relative;
        width: 42px;
        height: 42px;
        border-radius: 13px;
        background: var(--surface-card);
        border: 1px solid var(--border-default);
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        padding: 0;
      }
      .sf-bell-badge {
        position: absolute;
        top: -5px;
        right: -5px;
        min-width: 19px;
        height: 19px;
        padding: 0 5px;
        border-radius: 10px;
        background: var(--color-accent-strong);
        color: var(--text-on-accent);
        font-size: 11px;
        font-weight: 800;
        display: flex;
        align-items: center;
        justify-content: center;
        border: 2px solid var(--surface-page);
      }

      /* Greeting */
      .sf-greeting {
        font-size: 26px;
        font-weight: 700;
        color: var(--text-strong);
        letter-spacing: -0.5px;
        margin: 0;
      }
      .sf-active-count {
        font-size: 16px;
        font-weight: 600;
        color: var(--color-accent);
        margin: 4px 0 0;
      }

      /* Section headers */
      .sf-section-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin: 28px 0 14px;
      }
      .sf-section-head h2 {
        font-size: 18px;
        font-weight: 700;
        color: var(--text-strong);
        margin: 0;
      }
      .sf-link {
        font-size: 14px;
        font-weight: 600;
        color: var(--color-accent);
        cursor: pointer;
        background: none;
        border: none;
        padding: 0;
      }

      /* Active session cards */
      .sf-cards {
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      /* Telas largas: sessões ativas em grid. */
      @media (min-width: 768px) {
        .sf-cards {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
        }
      }
      .sf-card {
        background: var(--surface-card);
        border: 1px solid var(--border-default);
        border-radius: 18px;
        padding: 16px;
        display: flex;
        align-items: center;
        gap: 13px;
        cursor: pointer;
        text-align: left;
        width: 100%;
        -webkit-tap-highlight-color: transparent;
        transition: transform 120ms cubic-bezier(0.22, 1, 0.36, 1),
          border-color 0.15s;
      }
      /* Press feedback (no swipe here, so scale goes on the card directly). */
      .sf-card:active {
        transform: scale(0.97);
      }
      /* Aguardando AÇÃO do usuário: fundo âmbar + brilho neon pulsante. */
      .sf-card.is-waiting {
        border-color: #4a3a16;
        background: #1b1710;
        animation: sf-wait-glow 2.1s ease-in-out infinite;
      }
      /* Toast "tarefa concluída" 🎉 — banner fixo no topo, some sozinho. */
      .sf-task-toast {
        position: fixed;
        top: calc(env(safe-area-inset-top, 0px) + 12px);
        left: 50%;
        transform: translateX(-50%);
        z-index: 1000;
        display: inline-flex;
        align-items: center;
        gap: 10px;
        max-width: calc(100vw - 24px);
        padding: 10px 16px;
        border-radius: 14px;
        background: linear-gradient(150deg, #14b88f, #00926f);
        color: #04140f;
        box-shadow: 0 10px 30px -8px rgba(0, 0, 0, 0.6);
        animation: sf-task-toast-in 0.25s cubic-bezier(0.22, 1, 0.36, 1);
      }
      .sf-task-toast-emoji {
        font-size: 20px;
        flex: none;
      }
      .sf-task-toast-body {
        display: flex;
        flex-direction: column;
        min-width: 0;
      }
      .sf-task-toast-title {
        font-weight: 800;
        font-size: 13.5px;
      }
      .sf-task-toast-sub {
        font-size: 12px;
        opacity: 0.85;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      @keyframes sf-task-toast-in {
        from {
          opacity: 0;
          transform: translate(-50%, -8px);
        }
        to {
          opacity: 1;
          transform: translate(-50%, 0);
        }
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
      .sf-stat-icon {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        flex: none;
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
      .sf-card-body {
        flex: 1;
        min-width: 0;
        display: flex;
        flex-direction: column;
      }
      .sf-card-top {
        display: flex;
        align-items: center;
        gap: 8px;
        min-width: 0;
      }
      .sf-card-name {
        font-size: 16px;
        font-weight: 600;
        color: var(--text-strong);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .sf-agent {
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 0.4px;
        padding: 2px 7px;
        border-radius: 6px;
        flex: none;
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
        0%,
        100% {
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
        color: #c084fc;
        background: rgba(192, 132, 252, 0.14);
        border: 1px solid rgba(192, 132, 252, 0.3);
        padding: 2px 5px;
        border-radius: 6px;
      }
      /* Badge de host (multi-host) — compacto (só ícone + tooltip), só
         aparece com >1 host ativo. */
      .sf-host-chip {
        flex: none;
        display: inline-flex;
        align-items: center;
        color: #d4a373;
        background: rgba(212, 163, 115, 0.14);
        border: 1px solid rgba(212, 163, 115, 0.3);
        padding: 2px 5px;
        border-radius: 6px;
      }
      /* Badge de sub-agents rodando (contador + tooltip com nomes) */
      .sf-subagents {
        flex: none;
        display: inline-flex;
        align-items: center;
        gap: 3px;
        color: #38bdf8;
        background: rgba(56, 189, 248, 0.16);
        border: 1px solid rgba(56, 189, 248, 0.4);
        padding: 1px 6px 1px 4px;
        border-radius: 999px;
        font-size: 11px;
        font-weight: 800;
        white-space: nowrap;
        animation: sf-agents-pulse 1.6s ease-in-out infinite;
      }
      @keyframes sf-agents-pulse {
        0%, 100% { box-shadow: 0 0 0 0 rgba(56, 189, 248, 0); }
        50% { box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.18); }
      }
      /* Card com sub-agents rodando: borda-guia sutil pra destacar na lista. */
      .sf-card.sf-has-agents {
        border-color: rgba(56, 189, 248, 0.45);
      }
      .sf-card-status {
        font-size: 13.5px;
        margin-top: 3px;
      }
      .sf-card-time {
        font-size: 12.5px;
        color: #7a8090;
        margin-left: 4px;
      }
      .sf-card-sub {
        font-size: 13px;
        color: #7a8090;
        margin-top: 2px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .sf-chevron {
        flex: none;
      }

      /* Tasks */
      /* Mobile: chips numa linha que rola + seletor de sessão largura cheia. */
      .sf-task-filters {
        display: flex;
        flex-direction: column;
        gap: 8px;
        margin-bottom: 12px;
      }
      .sf-tchips {
        display: flex;
        gap: 6px;
        overflow-x: auto;
        scrollbar-width: none;
        -webkit-overflow-scrolling: touch;
        padding-bottom: 2px;
      }
      .sf-tchips::-webkit-scrollbar {
        display: none;
      }
      .sf-tfilter {
        flex: none;
        padding: 6px 13px;
        border-radius: 999px;
        border: 1px solid #283230;
        background: #181c1b;
        color: #9aa0ae;
        font-size: 12.5px;
        font-weight: 600;
        white-space: nowrap;
        cursor: pointer;
        -webkit-tap-highlight-color: transparent;
      }
      .sf-tfilter.sel {
        color: #06231d;
        background: var(--color-accent, #00e4b4);
        border-color: transparent;
      }
      .sf-tsessel {
        width: 100%;
        padding: 8px 10px;
        border-radius: 10px;
        border: 1px solid #283230;
        background: #181c1b;
        color: #d4d4d4;
        font-size: 13px;
        font-family: inherit;
      }
      /* Desktop: tudo numa linha (chips + seletor à direita). */
      @media (min-width: 768px) {
        .sf-task-filters {
          flex-direction: row;
          align-items: center;
        }
        .sf-tchips {
          flex-wrap: wrap;
          overflow: visible;
        }
        .sf-tsessel {
          width: auto;
          max-width: 200px;
          margin-left: auto;
        }
      }
      .sf-task-list {
        background: var(--surface-card);
        border: 1px solid var(--border-default);
        border-radius: 18px;
        overflow: hidden;
      }
      /* Wrap que segura o swipe: a zona vermelha fica ATRÁS da linha. */
      .sf-task-wrap {
        position: relative;
        overflow: hidden;
      }
      .sf-task {
        position: relative;
        z-index: 1;
        display: flex;
        align-items: center;
        gap: 13px;
        padding: 15px 16px;
        background: var(--surface-card);
        touch-action: pan-y;
        transition: transform 0.22s cubic-bezier(0.22, 1, 0.36, 1);
        will-change: transform;
      }
      /* Sem animação de transform enquanto o dedo/mouse arrasta. */
      .sf-task.is-dragging {
        transition: none;
      }
      .sf-task-divider {
        border-top: 1px solid #23262f;
      }
      .sf-task-clickable {
        cursor: pointer;
        -webkit-tap-highlight-color: transparent;
      }
      .sf-task-clickable:hover {
        background: #1c2422;
      }
      /* Ação vermelha "Eliminar" atrás da linha, à direita. */
      .sf-tdelete {
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
        /* Soft danger gradient + subtle inner depth; the wrap/list clip the
           corners (overflow:hidden) so this sits flush behind the row. */
        background: linear-gradient(135deg, #7f1d1d, #b91c1c);
        box-shadow: inset 1px 0 0 rgba(0, 0, 0, 0.25),
          inset 0 1px 0 rgba(255, 255, 255, 0.04);
        color: #fecaca;
        cursor: pointer;
        -webkit-tap-highlight-color: transparent;
      }
      .sf-tdelete__inner {
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
      .sf-tdelete.is-open .sf-tdelete__inner {
        opacity: 1;
        transform: scale(1);
      }
      .sf-tdelete__label {
        font-size: 11.5px;
        font-weight: 700;
        letter-spacing: 0.2px;
      }
      @media (prefers-reduced-motion: reduce) {
        .sf-tdelete__inner {
          transition: none;
        }
      }
      .sf-task-icon {
        width: 30px;
        height: 30px;
        border-radius: 9px;
        flex: none;
        display: flex;
        align-items: center;
        justify-content: center;
      }
      .sf-task-body {
        flex: 1;
        min-width: 0;
        display: flex;
        flex-direction: column;
      }
      .sf-task-title {
        font-size: 15px;
        font-weight: 600;
        color: var(--text-strong);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .sf-task-meta {
        font-size: 12.5px;
        margin-top: 1px;
      }
      /* Chip com o NOME real da sessão (ellipsis só se muito longo). */
      .sf-task-session {
        flex: none;
        max-width: 120px;
        padding: 3px 9px;
        border-radius: 8px;
        background: #22272a;
        border: 1px solid #283230;
        color: #9aa0ae;
        font-size: 11px;
        font-weight: 600;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }

      /* Botão ▶ para iniciar tarefas 'todo'. */
      .sf-task-play {
        flex: none;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 28px;
        height: 28px;
        padding: 0;
        border: none;
        background: transparent;
        color: #34d399;
        cursor: pointer;
        border-radius: 8px;
      }
      .sf-task-play {
        transition: transform 120ms cubic-bezier(0.22, 1, 0.36, 1),
          background 0.12s;
      }
      .sf-task-play:active {
        background: #1b2a24;
        transform: scale(0.92);
      }
      .sf-tfilter:active {
        transform: scale(0.97);
      }

      /* Empty state */
      .sf-empty {
        font-size: 14px;
        color: var(--text-muted);
        margin: 0;
        padding: 4px 2px;
      }
      .sf-task-more {
        display: block;
        width: fit-content;
        margin: 12px auto 2px;
        padding: 8px 18px;
        background: transparent;
        border: 1px solid #263038;
        border-radius: 999px;
        color: #9fb0ad;
        font: inherit;
        font-size: 12.5px;
        font-weight: 600;
        cursor: pointer;
      }
      .sf-task-more:hover {
        color: #e7eae9;
        border-color: #37464f;
      }

      /* Respect reduced-motion: disable entrance + press feedback. */
      @media (prefers-reduced-motion: reduce) {
        .sf-card.sf-enter,
        .sf-task-wrap.sf-enter {
          animation: none !important;
        }
        .sf-card,
        .sf-task-play,
        .sf-tfilter {
          transition: none !important;
          transform: none !important;
        }
      }
    `,
  ],
})
export class InicioComponent implements OnInit {
  private readonly api = inject(ApiService);
  protected readonly sse = inject(SseService);
  private readonly jarvis = inject(JarvisAudioService);
  protected readonly workers = inject(WorkersStore);
  private readonly router = inject(Router);

  /** True quando o áudio (JARVIS) tocando agora é DESTA sessão → mostra o ícone. */
  protected isSpeaking(s: Session): boolean {
    return !!s.tmux_name && this.jarvis.speakingSessionId() === s.tmux_name;
  }

  /**
   * FILHOS REAIS desta sessão: sessões delegadas (``parent === s.tmux_name``)
   * que ainda estão ATIVAS (status != stopped). É o sinal mais confiável de
   * "sub-agents rodando" — bate a heurística de tela quando existe.
   */
  private realChildren(s: Session): Session[] {
    const parent = s.tmux_name;
    if (!parent) {
      return [];
    }
    return this.sessions().filter(
      (c) => c.parent === parent && c.status !== 'stopped',
    );
  }

  /**
   * Quantos sub-agents estão rodando nesta sessão. PREFERE os filhos reais
   * delegados (mais confiável); na ausência deles cai na heurística da tela
   * (``subagents``). 0 = nenhum/desconhecido.
   */
  protected subAgentCount(s: Session): number {
    const kids = this.realChildren(s).length;
    if (kids > 0) {
      return kids;
    }
    const n = s.subagents;
    return typeof n === 'number' && n > 0 ? n : 0;
  }

  /** Tooltip do badge: lista os filhos reais (ou os nomes da heurística). */
  protected subAgentTip(s: Session): string {
    const kids = this.realChildren(s);
    const names = kids.length
      ? kids.map((c) => c.display_name || c.tmux_name)
      : Array.isArray(s.subagent_names)
        ? s.subagent_names
        : [];
    const n = this.subAgentCount(s);
    const head = `${n} sub-agente${n === 1 ? '' : 's'} rodando`;
    return names.length ? `${head}:\n• ${names.join('\n• ')}` : head;
  }

  /** True por alguns segundos após ESTA sessão concluir uma tarefa → destaca. */
  protected taskFlash(s: Session): boolean {
    return !!s.tmux_name && this.sse.taskDoneFlash() === s.tmux_name;
  }
  private readonly destroyRef = inject(DestroyRef);

  /** Poll periódico (ms): atualiza atividade das sessões + tarefas sem SSE. */
  private static readonly POLL_MS = 6000;
  private pollHandle: ReturnType<typeof setInterval> | null = null;

  /** All sessions loaded from the API, refreshed on SSE activity. */
  private readonly sessions = signal<Session[]>([]);
  /** Recent tasks loaded from the API. */
  protected readonly tasks = signal<Task[]>([]);

  /**
   * Badge do sino = SÓ as notificações não limpas (buffer SSE). Antes entrava
   * também o nº de sessões em ``waiting_input`` (Math.max) — aí o "Limpar
   * todas" zerava o buffer mas o badge continuava mostrando as aguardando e
   * NUNCA zerava. Sessão aguardando já é sinalizada no próprio card
   * (destaque "is-waiting"); o sino fica só com o que é notificação de fato.
   */
  readonly notifCount = computed(() => this.sse.notifications().length);

  /** Painel do custo global (abre/fecha pelo chip do header). */
  protected readonly costPanelOpen = signal(false);

  /**
   * Custo GLOBAL estimado: agrega o ``metrics.cost`` de TODAS as sessões.
   * Soma USD/BRL ignorando nulls, pega o câmbio mais recente disponível e
   * consolida a quebra por modelo (tokens + usd). Null = nenhuma sessão tem
   * custo ainda (feature recente) → o chip não renderiza.
   */
  protected readonly globalCost = computed(() => {
    let usd = 0;
    let hasUsd = false;
    let brl = 0;
    let hasBrl = false;
    let rate: number | null = null;
    let rateTs = '';
    const byModel = new Map<
      string,
      {
        model: string;
        input: number;
        output: number;
        cache_read: number;
        cache_write: number;
        usd: number | null;
      }
    >();

    for (const s of this.sessions()) {
      const c = s.metrics?.cost;
      if (!c) {
        continue;
      }
      if (c.total_usd != null) {
        usd += c.total_usd;
        hasUsd = true;
      }
      if (c.total_brl != null) {
        brl += c.total_brl;
        hasBrl = true;
      } else if (c.total_usd != null && c.brl_rate != null) {
        // Sessão com USD mas sem BRL calculado: converte com o câmbio dela.
        brl += c.total_usd * c.brl_rate;
        hasBrl = true;
      }
      if (c.brl_rate != null) {
        const ts =
          s.last_activity_at ?? (s['updated_at'] as string | undefined) ?? '';
        if (rate === null || ts > rateTs) {
          rate = c.brl_rate;
          rateTs = ts;
        }
      }
      for (const r of c.by_model ?? []) {
        const cur = byModel.get(r.model) ?? {
          model: r.model,
          input: 0,
          output: 0,
          cache_read: 0,
          cache_write: 0,
          usd: null as number | null,
        };
        cur.input += r.input;
        cur.output += r.output;
        cur.cache_read += r.cache_read;
        cur.cache_write += r.cache_write;
        if (r.usd != null) {
          cur.usd = (cur.usd ?? 0) + r.usd;
        }
        byModel.set(r.model, cur);
      }
    }

    if (!hasUsd) {
      return null;
    }
    // Ordena por usd desc; modelos sem preço (usd null) por último.
    const rows = [...byModel.values()].sort(
      (a, b) => (b.usd ?? -1) - (a.usd ?? -1),
    );
    const tokensIn = rows.reduce((n, r) => n + r.input, 0);
    const tokensOut = rows.reduce((n, r) => n + r.output, 0);
    return {
      usd,
      brl: hasBrl ? brl : null,
      rate,
      rows,
      tokensIn,
      tokensOut,
    };
  });

  /** Tooltip do chip: quebra por modelo + nota de estimativa. */
  protected readonly globalCostTip = computed(() => {
    const g = this.globalCost();
    if (!g) {
      return '';
    }
    const lines = g.rows.map(
      (r) =>
        `${r.model}: ${r.usd != null ? '~$' + this.fmtUsd(r.usd) : '—'} ` +
        `(in ${this.fmtTok(r.input)}tok · out ${this.fmtTok(r.output)}tok)`,
    );
    return [
      `Todas as sessões — in ${this.fmtTok(g.tokensIn)}tok · out ${this.fmtTok(g.tokensOut)}tok`,
      ...lines,
      'Estimativa em preço de API.',
    ].join('\n');
  });

  /**
   * Período do Top 3 de tokens. "today/week/month" são janelas ROLANTES
   * (últimas 24h/7d/30d, calculadas no worker a partir do timestamp de cada
   * turno no JSONL) — não calendário. "all" é o consumo total já existente
   * (tokens_in/tokens_out/cost, sem filtro de tempo).
   */
  protected readonly topTokensPeriod = signal<'today' | 'week' | 'month' | 'all'>('all');
  protected readonly topTokensPeriodOptions: { key: 'today' | 'week' | 'month' | 'all'; label: string }[] = [
    { key: 'today', label: 'Hoje' },
    { key: 'week', label: 'Semana' },
    { key: 'month', label: 'Mês' },
    { key: 'all', label: 'Sempre' },
  ];

  /**
   * TOP 3 sessões por consumo de tokens (in+out) NO PERÍODO selecionado,
   * maior primeiro. Ignora sessões sem dado pra aquele período (sessão nova,
   * sem atividade na janela, ou métricas antigas sem `tokens_periods` ainda
   * — feature recente). Ranking por tokens brutos (não por USD): reflete uso
   * real mesmo quando o preço do modelo é desconhecido (``usd`` null).
   */
  protected readonly topTokenSessions = computed(() => {
    const period = this.topTokensPeriod();
    const rows = this.sessions()
      .map((s) => {
        const m = s.metrics;
        const usage = period === 'all' ? m : m?.tokens_periods?.[period];
        const tokensIn = usage?.tokens_in ?? 0;
        const tokensOut = usage?.tokens_out ?? 0;
        const total = tokensIn + tokensOut;
        return {
          session: s,
          tokensIn,
          tokensOut,
          total,
          usd: usage?.cost?.total_usd ?? null,
        };
      })
      .filter((r) => r.total > 0)
      .sort((a, b) => b.total - a.total)
      .slice(0, 3);
    return rows;
  });

  /** Formata tokens em k/M compactos: 248000→"248k", 18300→"18,3k". */
  protected fmtTok(n: number | null | undefined): string {
    if (n == null) {
      return '—';
    }
    if (n >= 1_000_000) {
      const m = Math.round((n / 1_000_000) * 10) / 10;
      return String(m).replace('.', ',') + 'M';
    }
    if (n >= 1000) {
      const k = Math.round((n / 1000) * 10) / 10;
      return String(k).replace('.', ',') + 'k';
    }
    return String(n);
  }

  /** Formata USD: 2 casas; valores <0,01 ganham mais precisão (0.003). */
  protected fmtUsd(n: number): string {
    if (n > 0 && n < 0.01) {
      return n.toFixed(3);
    }
    return n.toFixed(2);
  }

  /** Dinheiro compacto pro chip: >=1000 vira "1,2k"; senão 2 casas. */
  protected fmtMoney(n: number): string {
    if (n >= 1000) {
      const k = Math.round((n / 1000) * 10) / 10;
      return String(k).replace('.', ',') + 'k';
    }
    return this.fmtUsd(n);
  }

  /** Active sessions = running or waiting_input. */
  readonly activeSessions = computed(() =>
    this.sessions().filter((s) => ACTIVE_STATUSES.includes(s.status)),
  );

  /** Saudação conforme a hora LOCAL do cliente (fuso do aparelho). */
  protected greeting(): string {
    const h = new Date().getHours();
    if (h < 12) {
      return 'Bom dia';
    }
    if (h < 18) {
      return 'Boa tarde';
    }
    return 'Boa noite';
  }

  readonly activeCountLabel = computed(() => {
    const n = this.activeSessions().length;
    return `${n} ${n === 1 ? 'sessão ativa' : 'sessões ativas'}`;
  });

  /** Filtros das tarefas: por status e por sessão. */
  readonly taskStatus = signal<'all' | 'todo' | 'doing' | 'done' | 'blocked'>('all');
  readonly taskSession = signal<string>('');
  readonly taskFilters: { key: 'todo' | 'doing' | 'done' | 'blocked'; label: string }[] = [
    { key: 'doing', label: 'Em andamento' },
    { key: 'todo', label: 'A fazer' },
    { key: 'blocked', label: 'Bloqueadas' },
    { key: 'done', label: 'Concluídas' },
  ];
  /** Sessões distintas que têm tarefas (para o seletor). */
  readonly taskSessions = computed(() => {
    const set = new Set<string>();
    for (const t of this.tasks()) {
      if (t.session_id) set.add(t.session_id);
    }
    return [...set].sort();
  });

  /** Tamanho do "lote" carregado a cada avanço do infinite scroll. */
  private static readonly TASKS_PAGE = 10;
  /** Limite atual; null = mostrar só as de HOJE (default). */
  readonly taskLimit = signal<number | null>(null);

  /** Tarefas filtradas (status/sessão) e ordenadas por mais recentes primeiro. */
  readonly filteredTasks = computed(() => {
    const st = this.taskStatus();
    const ses = this.taskSession();
    return this.tasks()
      .filter(
        (t) =>
          (st === 'all' || t.state === st) && (!ses || t.session_id === ses),
      )
      .slice()
      .sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? ''));
  });

  /**
   * Tarefas exibidas: por padrão só as de HOJE; se não houver nenhuma hoje,
   * mostra um primeiro lote (p/ não ficar vazio). Ao rolar/"ver mais", o
   * {@link taskLimit} cresce e passa a incluir as mais antigas.
   */
  readonly recentTasks = computed(() => {
    const all = this.filteredTasks();
    const limit = this.taskLimit();
    if (limit === null) {
      const today = all.filter((t) => isToday(t.updated_at));
      return today.length > 0 ? today : all.slice(0, InicioComponent.TASKS_PAGE);
    }
    return all.slice(0, limit);
  });

  /** Quantas tarefas ainda faltam além das visíveis (0 = tudo à mostra). */
  readonly remainingTasks = computed(() =>
    Math.max(0, this.filteredTasks().length - this.recentTasks().length),
  );

  /** Carrega o próximo lote (botão "Ver mais" / infinite scroll). */
  showMoreTasks(): void {
    if (this.remainingTasks() === 0) {
      return;
    }
    this.taskLimit.set(this.recentTasks().length + InicioComponent.TASKS_PAGE);
  }

  /** Ao rolar a página perto do fim da lista, carrega mais (infinite scroll). */
  onTasksScroll(): void {
    if (this.remainingTasks() === 0) {
      return;
    }
    const doc = document.documentElement;
    if (doc.scrollHeight - doc.scrollTop - doc.clientHeight < 240) {
      this.showMoreTasks();
    }
  }

  /** Tracks how many SSE events we have already reacted to. */
  private lastEventCount = 0;

  constructor() {
    // Live updates: whenever the SSE event buffer grows, re-fetch the lists.
    // Reading the signal inside an effect registers the dependency, so this
    // runs again on every new frame the service decodes.
    effect(() => {
      const count = this.sse.events().length;
      if (count !== this.lastEventCount) {
        this.lastEventCount = count;
        this.reloadSessions();
        this.reloadTasks();
      }
    });
    // Trocar filtro/sessão volta a mostrar só as de hoje (reseta a paginação).
    effect(() => {
      this.taskStatus();
      this.taskSession();
      this.taskLimit.set(null);
    });
  }

  ngOnInit(): void {
    if (!this.sse.connected()) {
      this.sse.connect();
    }
    this.reloadSessions();
    this.reloadTasks();

    // Poll periódico: a ATIVIDADE das sessões ("Pensando"/"Codificando"…) e o
    // estado das tarefas mudam no ciclo do worker sem disparar SSE. Re-busca
    // ambas as listas em intervalo curto p/ a home não ficar parada. O refetch
    // por SSE continua valendo (atualização imediata em eventos).
    this.pollHandle = setInterval(() => {
      this.reloadSessions();
      this.reloadTasks();
    }, InicioComponent.POLL_MS);
    // Infinite scroll da lista de tarefas: carrega mais ao chegar perto do fim.
    const onScroll = () => this.onTasksScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    this.destroyRef.onDestroy(() => {
      if (this.pollHandle !== null) {
        clearInterval(this.pollHandle);
        this.pollHandle = null;
      }
      window.removeEventListener('scroll', onScroll);
    });
  }

  private reloadSessions(): void {
    this.api.listSessions().subscribe({
      next: (list) => this.sessions.set(list ?? []),
      error: () => {
        /* keep last known state */
      },
    });
  }

  private reloadTasks(): void {
    this.api.getTasks().subscribe({
      next: (list) => this.tasks.set(list ?? []),
      error: () => {
        /* keep last known state */
      },
    });
  }

  // --- View helpers ---

  /** Staggered entrance delay per list index, capped so long lists don't lag. */
  enterDelay(i: number): string {
    return Math.min(i * 28, 220) + 'ms';
  }

  statusMeta(status: SessionStatus) {
    return STATUS_META[status] ?? STATUS_META.detached;
  }

  /**
   * Rótulo do status do card: para sessões RODANDO com ``activity`` derivado
   * mostra o que o agente está fazendo (ex.: "Pensando"); senão o label padrão.
   */
  statusLabel(s: Session): string {
    if (s.status === 'running' && s.activity) {
      return s.activity;
    }
    return this.statusMeta(s.status).label;
  }

  /**
   * "Última atividade há X" — usa ``last_activity_at`` (tela mudou / input real),
   * não ``updated_at`` (batido todo ciclo). Mostra se a sessão está parada faz tempo.
   */
  protected timeAgo(s: Session): string {
    return fmtTimeAgo(
      s.last_activity_at ??
        (s['updated_at'] as string | undefined) ??
        (s['created_at'] as string | undefined),
    );
  }

  /**
   * Ícone expressivo do status do card, escolhido pela ``activity`` (sessões
   * rodando) ou pelo status bruto.
   */
  statusIcon(
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
  isActiveIcon(s: Session): boolean {
    const k = this.statusIcon(s);
    return k === 'think' || k === 'code' || k === 'analyze' || k === 'run' || k === 'play';
  }

  agent(s: Session) {
    return agentMeta(s.agent_type);
  }

  /** Worker/sub-agente pela convenção de nome (chip ⑂). */
  isWorker(s: Session): boolean {
    return isWorkerSession(s.tmux_name ?? s.display_name);
  }

  /**
   * Nome do host desta sessão (multi-host, AD-011) — só quando existe MAIS
   * DE 1 host ativo (não polui o card do caso comum de hoje, 1 host só).
   */
  hostBadge(s: Session): string | null {
    if (!this.workers.hasMultipleHosts()) {
      return null;
    }
    return this.workers.hostname(s.host_id);
  }

  agentBg(s: Session): string {
    return this.hexToRgba(this.agent(s).color, 0.16);
  }

  displayName(s: Session): string {
    return s.display_name || s.tmux_name || s.id;
  }

  subline(s: Session): string {
    return s.work_dir || this.agent(s).label;
  }

  taskMeta(state: TaskDisplayState): { label: string; color: string; bg: string } {
    const map: Record<TaskDisplayState, { label: string; color: string }> = {
      todo: { label: 'A fazer', color: 'var(--text-muted)' },
      doing: { label: 'Em andamento', color: 'var(--warning)' },
      blocked: { label: 'Bloqueada', color: 'var(--danger)' },
      done: { label: 'Concluída', color: 'var(--positive)' },
      attention: { label: 'Requer atenção', color: 'var(--warning)' },
      paused: { label: 'Pausada', color: '#9aa0ad' },
    };
    const m = map[state] ?? map.todo;
    return { ...m, bg: this.cssVarToRgba(m.color) };
  }

  /** Status da sessão (tmux_name) a que a tarefa pertence — null se não achar. */
  private sessionStatusFor(t: Task): string | null {
    const s = this.sessions().find((s) => s.tmux_name === t.session_id);
    return s ? s.status : null;
  }

  /**
   * Estado de exibição efetivo: rebaixa 'doing' para 'paused' quando a sessão
   * da tarefa não está rodando (parada, desanexada ou inexistente). Assim uma
   * tarefa não aparece "Em andamento" sem o agente trabalhando de fato.
   */
  protected effectiveTaskState(t: Task): TaskDisplayState {
    if (t.state === 'doing' && this.sessionStatusFor(t) !== 'running') {
      return 'paused';
    }
    return t.state;
  }

  /** Nome da sessão da tarefa (chip à direita) — nome real, sem cortar a 6. */
  sessionShort(t: Task): string {
    return t.session_id ?? '';
  }

  // --- Navigation ---

  openNotifications(): void {
    this.router.navigate(['/notificacoes']);
  }
  goSessoes(): void {
    this.router.navigate(['/sessoes']);
  }
  goTimeline(): void {
    this.router.navigate(['/timeline']);
  }
  openSession(id: string): void {
    this.router.navigate(['/sessao', id]);
  }
  /** Resolve o tmux_name da tarefa para o _id real da sessão. */
  private sessionIdForTask(t: Task): string | null {
    const match = this.sessions().find(
      (s) => s.tmux_name === t.session_id || s.display_name === t.session_id,
    );
    return match ? match.id : null;
  }

  /** Clique na tarefa → abre a sessão onde ela está acontecendo. */
  openTaskSession(t: Task): void {
    const id = this.sessionIdForTask(t);
    if (id) {
      this.router.navigate(['/sessao', id], { queryParams: { task: t.title } });
    }
  }

  // ── Swipe-to-delete nas tarefas (iOS style, espelha SessoesComponent) ─────
  private static readonly OPEN = -88;
  private static readonly SNAP = -72;
  private static readonly CLAMP = -96;
  private static readonly TAP_THRESHOLD = 8;

  /** translateX por tarefa (px). 0 = fechado. */
  private readonly taskOffsets = signal<Map<string, number>>(new Map());
  /** Id da tarefa em arrasto ativo (desabilita a transição CSS). */
  protected readonly taskDragId = signal<string | null>(null);
  /** Sinaliza que o último gesto foi swipe (suprime o tap seguinte). */
  private lastTaskWasSwipe = false;

  private taskGesture: {
    id: string;
    startX: number;
    startY: number;
    baseOffset: number;
    locked: boolean;
    moved: boolean;
    cancelled: boolean;
  } | null = null;

  protected taskOffset(id: string): number {
    return this.taskOffsets().get(id) ?? 0;
  }

  private setTaskOffset(id: string, value: number): void {
    this.taskOffsets.update((m) => {
      const next = new Map(m);
      if (value === 0) {
        next.delete(id);
      } else {
        next.set(id, value);
      }
      return next;
    });
  }

  private closeAllTasks(except?: string): void {
    this.taskOffsets.update((m) => {
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

  protected onTaskPointerDown(t: Task, ev: PointerEvent): void {
    if (ev.pointerType === 'mouse' && ev.button !== 0) {
      return;
    }
    this.taskGesture = {
      id: t.id,
      startX: ev.clientX,
      startY: ev.clientY,
      baseOffset: this.taskOffset(t.id),
      locked: false,
      moved: false,
      cancelled: false,
    };
  }

  protected onTaskPointerMove(t: Task, ev: PointerEvent): void {
    const g = this.taskGesture;
    if (!g || g.id !== t.id || g.cancelled) {
      return;
    }
    const dx = ev.clientX - g.startX;
    const dy = ev.clientY - g.startY;

    if (!g.locked) {
      if (Math.abs(dx) < 4 && Math.abs(dy) < 4) {
        return;
      }
      // Scroll vertical vence → deixa rolar, aborta o swipe.
      if (Math.abs(dy) > Math.abs(dx)) {
        g.cancelled = true;
        return;
      }
      g.locked = true;
      this.taskDragId.set(t.id);
      this.closeAllTasks(t.id);
      (ev.target as Element).setPointerCapture?.(ev.pointerId);
    }

    if (Math.abs(dx) > InicioComponent.TAP_THRESHOLD) {
      g.moved = true;
    }

    let next = g.baseOffset + dx;
    if (next > 0) {
      next = next * 0.25;
    } else if (next < InicioComponent.CLAMP) {
      const over = next - InicioComponent.CLAMP;
      next = InicioComponent.CLAMP + over * 0.25;
    }
    this.setTaskOffset(t.id, next);
  }

  protected onTaskPointerUp(t: Task, ev: PointerEvent): void {
    const g = this.taskGesture;
    if (!g || g.id !== t.id) {
      return;
    }
    this.taskGesture = null;
    this.taskDragId.set(null);
    if (g.cancelled || !g.locked) {
      return;
    }
    (ev.target as Element).releasePointerCapture?.(ev.pointerId);
    if (g.moved) {
      this.lastTaskWasSwipe = true;
    }
    const cur = this.taskOffset(t.id);
    this.setTaskOffset(
      t.id,
      cur <= InicioComponent.SNAP ? InicioComponent.OPEN : 0,
    );
  }

  /** Tap na linha: abre a sessão; suprime quando houve swipe / está aberta. */
  protected onTaskClick(t: Task, ev: MouseEvent): void {
    const moved = this.taskOffset(t.id) !== 0;
    if (this.lastTaskWasSwipe || moved) {
      ev.preventDefault();
      ev.stopPropagation();
      if (moved) {
        this.setTaskOffset(t.id, 0);
      }
      this.lastTaskWasSwipe = false;
      return;
    }
    this.openTaskSession(t);
  }

  /** Tap no botão vermelho: confirma, remove otimista e chama deleteTask. */
  protected confirmEliminateTask(t: Task): void {
    const ok = confirm(
      'Apagar a tarefa "' +
        (t.title ?? '') +
        '"? Some daqui e do arquivo de marcos no Mac.',
    );
    if (!ok) {
      this.setTaskOffset(t.id, 0); // snap back
      return;
    }

    // Remoção otimista (guarda p/ rollback).
    const prev = this.tasks();
    this.setTaskOffset(t.id, 0);
    this.tasks.update((list) => list.filter((x) => x.id !== t.id));

    this.api.deleteTask(t.id).subscribe({
      error: () => {
        // Rollback + recarrega do servidor.
        this.tasks.set(prev);
        this.reloadTasks();
      },
    });
  }

  /** Botão ▶ em tarefas 'todo' → manda a sessão começar a trabalhar nela. */
  startTask(t: Task, ev: Event): void {
    ev.stopPropagation();
    const id = this.sessionIdForTask(t);
    if (!id) {
      return;
    }
    this.api
      .sendInput(id, 'Comece a trabalhar nesta tarefa: ' + t.title)
      .subscribe({ next: () => {}, error: () => {} });
  }

  // --- Color utils ---

  private hexToRgba(hex: string, alpha: number): string {
    const h = hex.replace('#', '');
    const full = h.length === 3
      ? h.split('').map((c) => c + c).join('')
      : h;
    const r = parseInt(full.slice(0, 2), 16);
    const g = parseInt(full.slice(2, 4), 16);
    const b = parseInt(full.slice(4, 6), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  /** For CSS-var colors we can't parse, fall back to color-mix for the tint. */
  private cssVarToRgba(color: string): string {
    if (color.startsWith('#')) {
      return this.hexToRgba(color, 0.16);
    }
    return `color-mix(in srgb, ${color} 16%, transparent)`;
  }
}

/** True se o ISO cai no dia de HOJE (fuso local). Sem data → false. */
function isToday(iso: string | null | undefined): boolean {
  if (!iso) {
    return false;
  }
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) {
    return false;
  }
  const now = new Date();
  return (
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  );
}
