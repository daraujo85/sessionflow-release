import {
  AfterViewChecked,
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  computed,
  effect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { Location } from '@angular/common';

import { ApiService } from '../../core/api.service';
import { SseService } from '../../core/sse.service';
import { Session, SessionMetrics, Task, TerminalKey } from '../../core/models';
import { STATUS_META, agentMeta } from '../../shared/status-color';
import { AudioRecorderComponent } from '../../shared/audio-recorder/audio-recorder.component';
import { ansiToHtml } from '../../shared/ansi-html';

@Component({
  selector: 'sf-detalhe',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, AudioRecorderComponent],
  template: `
    <section class="overlay">
      <!-- Header -->
      <header class="hdr">
        <div class="hdr-top">
          <button type="button" class="back" (click)="goBack()" aria-label="Voltar">
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="#C9CDD6"
              stroke-width="2.2"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <path d="M15 18l-6-6 6-6" />
            </svg>
          </button>

          <div class="hdr-info">
            <div class="hdr-title">{{ displayName() }}</div>
            <div class="mono hdr-dir">{{ session()?.work_dir || '—' }}</div>
          </div>

          <span
            class="agent-badge"
            [style.color]="agent().color"
            [style.background]="agent().color + '22'"
            >{{ agent().short }}</span
          >
        </div>

        <div class="status-row">
          <span
            class="status-pill"
            [style.color]="statusMeta().color"
            [style.background]="statusMeta().dot + '1f'"
          >
            <span class="status-dot" [style.background]="statusMeta().dot"></span>
            {{ statusMeta().label }}
          </span>

          <span class="status-actions">
            <button
              type="button"
              class="act act--ghost"
              [disabled]="acting()"
              (click)="resume()"
            >
              Retomar
            </button>
            <button
              type="button"
              class="act act--danger"
              [disabled]="acting()"
              (click)="end()"
            >
              Encerrar
            </button>
          </span>
        </div>
      </header>

      <!-- Bloco de métricas (sem endpoint real → "—"/"indisponível") -->
      <section class="metrics" aria-label="Métricas da sessão">
        <div class="metrics-top">
          <div class="metrics-model">
            <span class="agent-dot" [style.background]="agent().color"></span>
            <div class="metrics-model-info">
              <div class="metric-label">Modelo</div>
              <div class="metric-value">{{ modelLabel() }}</div>
            </div>
          </div>
          <div class="metrics-ctx">
            <div class="metric-label">Contexto usado</div>
            @if (metrics(); as m) {
              <div class="metrics-ctx-pct" [style.color]="ctxColor()">
                {{ m.context_pct }}%
              </div>
            } @else {
              <div class="metrics-ctx-pct">—</div>
            }
          </div>
        </div>

        <div class="ctx-bar" aria-hidden="true">
          @if (metrics(); as m) {
            <span
              class="ctx-fill"
              [style.width.%]="m.context_pct"
              [style.background]="ctxColor()"
            ></span>
          } @else {
            <span class="ctx-fill" style="width: 0%"></span>
          }
        </div>
        <div class="ctx-foot">
          @if (metrics(); as m) {
            <span class="mono ctx-label"
              >{{ fmtTok(m.context_used) }} / {{ fmtTok(m.context_max) }}
              tokens</span
            >
          } @else {
            <span class="mono ctx-label">indisponível</span>
          }
        </div>

        <div class="metric-cards">
          <div class="mcard">
            <svg
              width="17"
              height="17"
              viewBox="0 0 24 24"
              fill="none"
              stroke="#34D399"
              stroke-width="2.2"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <path d="M12 5v12" />
              <path d="m6 11 6 6 6-6" />
            </svg>
            <div class="mcard-info">
              <div class="mcard-label">Entrada</div>
              <div class="mono mcard-value">
                {{ metrics() ? fmtTok(metrics()!.tokens_in) : '—' }}
              </div>
            </div>
          </div>
          <div class="mcard">
            <svg
              width="17"
              height="17"
              viewBox="0 0 24 24"
              fill="none"
              stroke="#4796E3"
              stroke-width="2.2"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <path d="M12 19V7" />
              <path d="m6 13 6-6 6 6" />
            </svg>
            <div class="mcard-info">
              <div class="mcard-label">Saída</div>
              <div class="mono mcard-value">
                {{ metrics() ? fmtTok(metrics()!.tokens_out) : '—' }}
              </div>
            </div>
          </div>
        </div>

        <div class="metric-cards">
          @if (limits(); as l) {
            <!-- PRIORIDADE 1: limites reais (recolhidos; clique expande reset) -->
            <div
              class="lcard lcard-clickable"
              (click)="limitsExpanded.set(!limitsExpanded())"
            >
              <div class="lcard-top">
                <span class="mcard-label">Sessão (5h)</span>
                <svg class="lcard-chev" [class.open]="limitsExpanded()" width="15" height="15"
                     viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                     stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6" /></svg>
              </div>
              <div class="lcard-value" [style.color]="limitColor(l.session_pct)">
                {{ l.session_pct }}%
              </div>
              @if (limitsExpanded()) {
                <div class="lbar" aria-hidden="true">
                  <span class="lbar-fill" [style.width.%]="l.session_pct"
                        [style.background]="limitColor(l.session_pct)"></span>
                </div>
                <div class="lcard-sub">Reset {{ l.session_reset }}</div>
              }
            </div>
            <div
              class="lcard lcard-clickable"
              (click)="limitsExpanded.set(!limitsExpanded())"
            >
              <div class="lcard-top">
                <span class="mcard-label">Semanal</span>
                <svg class="lcard-chev" [class.open]="limitsExpanded()" width="15" height="15"
                     viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                     stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6" /></svg>
              </div>
              <div class="lcard-value" [style.color]="limitColor(l.week_pct)">
                {{ l.week_pct }}%
              </div>
              @if (limitsExpanded()) {
                <div class="lbar" aria-hidden="true">
                  <span class="lbar-fill" [style.width.%]="l.week_pct"
                        [style.background]="limitColor(l.week_pct)"></span>
                </div>
                <div class="lcard-sub">Reset {{ l.week_reset }}</div>
              }
            </div>
          } @else if (activity(); as a) {
            <!-- PRIORIDADE 2: fallback de atividade (hoje/semana) -->
            <div class="lcard">
              <span class="mcard-label">Atividade hoje</span>
              <div class="lcard-value">{{ fmtCount(a.today_messages) }} msgs</div>
              <div class="lcard-sub">{{ fmtCount(a.today_tools) }} tools</div>
            </div>
            <div class="lcard">
              <span class="mcard-label">Atividade semana</span>
              <div class="lcard-value">{{ fmtCount(a.week_messages) }} msgs</div>
              <div class="lcard-sub">{{ fmtCount(a.week_tools) }} tools</div>
            </div>
          } @else {
            <!-- PRIORIDADE 3: nenhum dado disponível -->
            <div class="lcard">
              <span class="mcard-label">Limite (sessão 5h)</span>
              <div class="lcard-value">—</div>
            </div>
            <div class="lcard">
              <span class="mcard-label">Limite semanal</span>
              <div class="lcard-value">—</div>
            </div>
          }
        </div>
      </section>

      <!-- Tarefas da sessão (marcos) — recolhível p/ não roubar o terminal -->
      @if (tasks().length > 0) {
        <div class="tasks">
          <button type="button" class="tasks-head" (click)="tasksOpen.set(!tasksOpen())">
            <span class="tasks-title">Tarefas ({{ tasks().length }})</span>
            <span class="tasks-sub">{{ tasksDoneCount() }}/{{ tasks().length }} concluídas</span>
            <svg class="tasks-chev" [class.open]="tasksOpen()" width="18" height="18" viewBox="0 0 24 24"
                 fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="m6 9 6 6 6-6" />
            </svg>
          </button>
          @if (tasksOpen()) {
            <div class="tasks-filters">
              @for (f of taskFilters; track f.key) {
                <button
                  type="button"
                  class="tasks-filter"
                  [class.sel]="taskStatusFilter() === f.key"
                  (click)="taskStatusFilter.set(f.key)"
                >
                  {{ f.label }}
                </button>
              }
            </div>
            <ul class="tasks-list">
              @for (t of filteredTasks(); track t.id) {
                <li class="tasks-item">
                  <span class="tasks-dot" [style.background]="taskColor(t.state)"></span>
                  <span class="tasks-item-title">{{ t.title }}</span>
                  <span class="tasks-item-state" [style.color]="taskColor(t.state)">{{ taskLabel(t.state) }}</span>
                </li>
              } @empty {
                <li class="tasks-empty">Nenhuma tarefa nesse status.</li>
              }
            </ul>
          }
        </div>
      }

      <!-- Terminal: espelho ao vivo da tela atual do agente -->
      <div class="term mono" #term aria-label="Tela do terminal">
        @if (screen().length === 0) {
          <div class="term-msg">Conectando ao terminal…</div>
        } @else {
          <pre class="term-screen" [innerHTML]="screenHtml()"></pre>
        }
      </div>

      <!-- Teclado de controle p/ navegar prompts TUI (pickers, listas) -->
      <div class="keypad" role="group" aria-label="Teclas de navegação">
        <button type="button" class="key" (click)="pressKey('up')" aria-label="Cima">↑</button>
        <button type="button" class="key" (click)="pressKey('down')" aria-label="Baixo">↓</button>
        <button type="button" class="key" (click)="pressKey('left')" aria-label="Esquerda">←</button>
        <button type="button" class="key" (click)="pressKey('right')" aria-label="Direita">→</button>
        <button type="button" class="key key-wide" (click)="pressKey('space')">Espaço</button>
        <button type="button" class="key key-wide key-accent" (click)="pressKey('enter')">Enter</button>
        <button type="button" class="key" (click)="pressKey('escape')">Esc</button>
      </div>

      <!-- Input bar -->
      <footer class="inputbar">
        <button
          type="button"
          class="live-toggle"
          [class.is-on]="liveMode()"
          (click)="toggleLive()"
          [attr.aria-pressed]="liveMode()"
          aria-label="Modo ao vivo (encaminha o que você digita pro terminal)"
          title="Modo ao vivo: mostra o autocomplete do CLI enquanto digita"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="m7 8-4 4 4 4M17 8l4 4-4 4M14 4l-4 16" />
          </svg>
        </button>

        <sf-audio-recorder
          class="mic"
          [sessionId]="id()"
          (transcribing)="onAudioTranscribing($event)"
          (uploaded)="onAudioUploaded()"
        ></sf-audio-recorder>

        <input
          class="text-input mono"
          type="text"
          [placeholder]="liveMode() ? 'Digite — ao vivo no terminal…' : 'Enviar comando ao terminal…'"
          autocomplete="off"
          [ngModel]="draft()"
          (ngModelChange)="onDraftChange($event)"
          (keydown.enter)="send()"
          [disabled]="sending()"
        />

        <button
          type="button"
          class="send"
          [disabled]="!canSend()"
          (click)="send()"
          aria-label="Enviar"
        >
          <svg
            width="19"
            height="19"
            viewBox="0 0 24 24"
            fill="none"
            stroke="#04140f"
            stroke-width="2.4"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path d="M22 2 11 13" />
            <path d="M22 2 15 22l-4-9-9-4 20-7z" />
          </svg>
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
        background: #0e1113;
        color: #f4f5f7;
      }
      .mono {
        font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, 'SF Mono',
          Menlo, Consolas, monospace;
      }

      /* Header */
      .hdr {
        flex: none;
        padding: 6px 16px 14px;
        border-bottom: 1px solid #20262a;
      }
      .hdr-top {
        display: flex;
        align-items: center;
        gap: 10px;
      }
      .back {
        flex: none;
        display: flex;
        align-items: center;
        justify-content: center;
        width: 38px;
        height: 38px;
        border: 1px solid #283230;
        border-radius: 11px;
        background: #181c1b;
        cursor: pointer;
        padding: 0;
      }
      .hdr-info {
        flex: 1;
        min-width: 0;
      }
      .hdr-title {
        font-size: 17px;
        font-weight: 700;
        color: #f4f5f7;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .hdr-dir {
        font-size: 12px;
        color: #7a8090;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .agent-badge {
        flex: none;
        font-size: 11px;
        font-weight: 800;
        padding: 4px 9px;
        border-radius: 8px;
      }

      /* Status row */
      .status-row {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-top: 13px;
      }
      .status-pill {
        display: inline-flex;
        align-items: center;
        gap: 7px;
        font-size: 12.5px;
        font-weight: 600;
        padding: 5px 11px;
        border-radius: 9px;
        white-space: nowrap;
      }
      .status-dot {
        width: 7px;
        height: 7px;
        border-radius: 50%;
      }
      .status-actions {
        margin-left: auto;
        display: flex;
        gap: 8px;
      }
      .act {
        appearance: none;
        background: transparent;
        font: inherit;
        font-size: 12.5px;
        font-weight: 600;
        color: #c9cdd6;
        padding: 6px 12px;
        border-radius: 9px;
        border: 1px solid #283230;
        cursor: pointer;
        transition: opacity 0.15s, background 0.15s;
      }
      .act:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }
      .act--danger {
        color: #f87171;
        border-color: #3a2326;
      }
      .act--danger:hover:not(:disabled) {
        background: #2a1c1c;
      }

      /* Bloco de métricas */
      .metrics {
        flex: none;
        padding: 13px 16px 14px;
        border-bottom: 1px solid #20262a;
        background: #121614;
      }
      .metrics-top {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
      }
      .metrics-model {
        display: flex;
        align-items: center;
        gap: 9px;
        min-width: 0;
      }
      .agent-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        flex: none;
      }
      .metrics-model-info {
        min-width: 0;
      }
      .metric-label {
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.6px;
        text-transform: uppercase;
        color: #6b7180;
      }
      .metric-value {
        font-size: 14px;
        font-weight: 600;
        color: #f4f5f7;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .metrics-ctx {
        text-align: right;
        flex: none;
      }
      .metrics-ctx-pct {
        font-size: 19px;
        font-weight: 800;
        line-height: 1.1;
        color: #6b7180;
      }
      .ctx-bar {
        margin-top: 11px;
        height: 7px;
        border-radius: 4px;
        background: #23262f;
        overflow: hidden;
      }
      .ctx-fill {
        display: block;
        height: 100%;
        background: #00e4b4;
        border-radius: 4px;
      }
      .ctx-foot {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        margin-top: 7px;
      }
      .ctx-label {
        font-size: 11.5px;
        color: #7a8090;
      }

      .metric-cards {
        display: flex;
        gap: 10px;
        margin-top: 13px;
      }
      .metric-cards + .metric-cards {
        margin-top: 10px;
      }
      .mcard {
        flex: 1;
        min-width: 0;
        display: flex;
        align-items: center;
        gap: 9px;
        background: #181c1b;
        border: 1px solid #283230;
        border-radius: 12px;
        padding: 10px 12px;
      }
      .mcard svg {
        flex: none;
      }
      .mcard-info {
        min-width: 0;
      }
      .mcard-label {
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.5px;
        text-transform: uppercase;
        color: #6b7180;
      }
      .mcard-value {
        font-size: 14.5px;
        font-weight: 700;
        color: #f4f5f7;
      }
      .lcard {
        flex: 1;
        min-width: 0;
        background: #181c1b;
        border: 1px solid #283230;
        border-radius: 12px;
        padding: 10px 12px;
      }
      .lcard-clickable {
        cursor: pointer;
        -webkit-tap-highlight-color: transparent;
      }
      .lcard-top {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
      }
      .lcard-chev {
        flex: none;
        color: #7a8090;
        transition: transform 0.2s;
      }
      .lcard-chev.open {
        transform: rotate(180deg);
      }
      .lcard-value {
        margin-top: 4px;
        font-size: 20px;
        font-weight: 800;
        color: #f4f5f7;
        line-height: 1.15;
      }
      .lcard-sub {
        margin-top: 2px;
        font-size: 11.5px;
        font-weight: 600;
        color: #7a8090;
      }
      .lcard-head {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 8px;
      }
      .lcard-pct {
        font-size: 18px;
        font-weight: 800;
        line-height: 1.1;
      }
      .lbar {
        margin-top: 8px;
        height: 7px;
        border-radius: 4px;
        background: #23262f;
        overflow: hidden;
      }
      .lbar-fill {
        display: block;
        height: 100%;
        border-radius: 4px;
      }

      /* Terminal */
      /* Painel de tarefas da sessão (recolhível) */
      .tasks {
        flex: none;
        margin: 0 14px;
        border: 1px solid #20262a;
        border-radius: 12px;
        background: #12181a;
        overflow: hidden;
      }
      .tasks-head {
        width: 100%;
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 10px 14px;
        background: none;
        border: none;
        color: #f4f5f7;
        cursor: pointer;
        text-align: left;
      }
      .tasks-title {
        font-size: 13.5px;
        font-weight: 700;
      }
      .tasks-sub {
        flex: 1;
        font-size: 12px;
        color: #7a8090;
      }
      .tasks-chev {
        flex: none;
        color: #7a8090;
        transition: transform 0.2s;
      }
      .tasks-chev.open {
        transform: rotate(180deg);
      }
      .tasks-filters {
        display: flex;
        flex-wrap: wrap;
        gap: 5px;
        padding: 0 14px 8px;
      }
      .tasks-filter {
        padding: 4px 9px;
        border-radius: 999px;
        border: 1px solid #283230;
        background: #181c1b;
        color: #9aa0ae;
        font-size: 11.5px;
        font-weight: 600;
        cursor: pointer;
        -webkit-tap-highlight-color: transparent;
      }
      .tasks-filter.sel {
        color: #06231d;
        background: #00e4b4;
        border-color: transparent;
      }
      .tasks-empty {
        padding: 8px 0;
        font-size: 13px;
        color: #7a8090;
      }
      .tasks-list {
        list-style: none;
        margin: 0;
        padding: 0 14px 10px;
        max-height: 38vh;
        overflow-y: auto;
      }
      .tasks-item {
        display: flex;
        align-items: flex-start;
        gap: 10px;
        padding: 8px 0;
        border-top: 1px solid #1c2226;
      }
      .tasks-dot {
        flex: none;
        width: 9px;
        height: 9px;
        border-radius: 50%;
        margin-top: 5px;
      }
      .tasks-item-title {
        flex: 1;
        font-size: 13.5px;
        color: #e7eae9;
        line-height: 1.4;
      }
      .tasks-item-state {
        flex: none;
        font-size: 11px;
        font-weight: 700;
        white-space: nowrap;
        margin-top: 1px;
      }
      .term {
        flex: 1;
        min-height: 0;
        overflow-y: auto;
        background: #0b0e0f;
        padding: 14px 16px;
        font-size: 12.5px;
        line-height: 1.7;
      }
      .term-msg {
        color: #6b7280;
        font-size: 12px;
      }
      .term-screen {
        margin: 0;
        white-space: pre-wrap;
        word-break: break-word;
        color: #cdd2da;
        font-family: inherit;
        font-size: inherit;
        line-height: inherit;
      }

      /* Input bar */
      .keypad {
        flex: none;
        display: flex;
        gap: 5px;
        padding: 8px 12px 12px;
        background: #0e1113;
        touch-action: manipulation;
        -webkit-tap-highlight-color: transparent;
      }
      .key {
        flex: 1 1 0;
        min-width: 0;
        height: 38px;
        padding: 0 4px;
        border-radius: 10px;
        border: 1px solid #283230;
        background: #181c1b;
        color: #d4d4d4;
        font-size: 15px;
        font-weight: 600;
        font-family: inherit;
        cursor: pointer;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: clip;
        touch-action: manipulation;
        -webkit-tap-highlight-color: transparent;
        transition: background 0.12s, transform 0.06s;
      }
      .key:active {
        transform: scale(0.94);
        background: #22272a;
      }
      .key:disabled {
        opacity: 0.5;
      }
      .key-wide {
        flex: 1.5 1 0;
        font-size: 12.5px;
      }
      .key-accent {
        color: #06231d;
        background: linear-gradient(150deg, #2cecc4, #00a482);
        border-color: transparent;
      }
      .inputbar {
        flex: none;
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 12px 14px calc(16px + env(safe-area-inset-bottom, 0px));
        border-top: 1px solid #20262a;
        background: #0e1113;
      }
      .live-toggle {
        flex: none;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 40px;
        height: 40px;
        border-radius: 12px;
        border: 1px solid #283230;
        background: #181c1b;
        color: #8a90a0;
        cursor: pointer;
        touch-action: manipulation;
        -webkit-tap-highlight-color: transparent;
        transition: background 0.15s, color 0.15s, border-color 0.15s;
      }
      .live-toggle.is-on {
        color: #06231d;
        background: linear-gradient(150deg, #2cecc4, #00a482);
        border-color: transparent;
      }
      .mic {
        flex: none;
      }
      /* Ajusta o botão de mic herdado do componente compartilhado. */
      .inputbar ::ng-deep .sf-rec-btn {
        width: 44px;
        height: 44px;
        border-radius: 13px;
      }
      .inputbar ::ng-deep .sf-rec-error {
        display: none;
      }
      .text-input {
        flex: 1;
        min-width: 0;
        appearance: none;
        height: 44px;
        border: 1px solid #283230;
        background: #181c1b;
        color: #f4f5f7;
        font-size: 14px;
        padding: 0 14px;
        border-radius: 13px;
      }
      .text-input::placeholder {
        color: #6b7180;
      }
      .text-input:focus {
        outline: none;
        border-color: #00e4b4;
      }
      .text-input:disabled {
        opacity: 0.6;
      }
      .send {
        flex: none;
        display: flex;
        align-items: center;
        justify-content: center;
        width: 44px;
        height: 44px;
        border: none;
        border-radius: 13px;
        background: linear-gradient(150deg, #00e4b4, #00a482);
        cursor: pointer;
        padding: 0;
        transition: opacity 0.15s;
      }
      .send:disabled {
        opacity: 0.4;
        cursor: not-allowed;
      }
    `,
  ],
})
export class DetalheComponent implements AfterViewChecked {
  private readonly api = inject(ApiService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  private readonly sse = inject(SseService);
  private readonly sanitizer = inject(DomSanitizer);
  private readonly location = inject(Location);

  private readonly termEl = viewChild<ElementRef<HTMLDivElement>>('term');

  /** Session id from the route (`sessao/:id`). */
  protected readonly id = signal<string>(
    this.route.snapshot.paramMap.get('id') ?? '',
  );

  protected readonly session = signal<Session | null>(null);
  /** Tarefas/marcos desta sessão (painel recolhível). */
  protected readonly tasks = signal<Task[]>([]);
  protected readonly tasksOpen = signal<boolean>(false);
  /** Filtro de status do painel de tarefas da sessão. */
  protected readonly taskStatusFilter = signal<
    'all' | 'todo' | 'doing' | 'done' | 'blocked'
  >('all');
  protected readonly taskFilters: {
    key: 'all' | 'todo' | 'doing' | 'done' | 'blocked';
    label: string;
  }[] = [
    { key: 'all', label: 'Todas' },
    { key: 'doing', label: 'Em andamento' },
    { key: 'todo', label: 'A fazer' },
    { key: 'blocked', label: 'Bloqueadas' },
    { key: 'done', label: 'Concluídas' },
  ];
  protected readonly filteredTasks = computed(() => {
    const st = this.taskStatusFilter();
    return st === 'all'
      ? this.tasks()
      : this.tasks().filter((t) => t.state === st);
  });
  /** Limites recolhidos por padrão (só %); clique expande barra + reset. */
  protected readonly limitsExpanded = signal<boolean>(false);
  protected readonly tasksDoneCount = computed(
    () => this.tasks().filter((t) => t.state === 'done').length,
  );
  protected readonly acting = signal<boolean>(false);
  protected readonly sending = signal<boolean>(false);
  protected readonly draft = signal<string>('');

  /**
   * Modo "ao vivo": encaminha o que está sendo digitado pro pane em tempo real
   * (sem Enter), para o CLI mostrar o autocomplete dele no espelho. Desligado
   * (default) = modo lote (compõe local, envia de uma vez).
   */
  protected readonly liveMode = signal<boolean>(false);
  /** Conteúdo já encaminhado ao pane no modo ao vivo (p/ calcular o diff). */
  private paneBuffer = '';
  /** Debounce do encaminhamento ao vivo. */
  private liveTimer: ReturnType<typeof setTimeout> | null = null;

  /**
   * Espelho da tela visível atual do agente (pane do tmux, ANSI removido).
   * É SUBSTITUÍDO a cada poll — não acumula — pra refletir a tela real do
   * agente TUI (codex/claude) em vez de um log de linhas que vira ruído.
   */
  protected readonly screen = signal<string>('');

  /** Espelho com cores: ANSI (SGR) → HTML seguro, confiável para [innerHTML]. */
  protected readonly screenHtml = computed<SafeHtml>(() =>
    this.sanitizer.bypassSecurityTrustHtml(ansiToHtml(this.screen())),
  );

  protected readonly canSend = computed(
    () => this.draft().trim().length > 0 && !this.sending() && !!this.id(),
  );

  /** Texto da última tela renderizada, para disparar o auto-scroll. */
  private lastRenderedScreen = '';

  /**
   * Auto-scroll só "gruda no fim" quando o usuário JÁ está no fim. Se ele rolou
   * para cima (ex.: acompanhando o highlight de um picker TUI ao navegar com as
   * setas), NÃO arrastamos a viewport — senão a opção em foco some de vista.
   */
  private stickToBottom = true;

  constructor() {
    this.sse.connect(); // idempotente — garante o canal p/ o push do espelho
    this.loadSession();
    this.refreshScreen();

    // Ao ABRIR a sessão, instrui (1x) a trabalhar em tarefas/marcos. O server
    // é idempotente e respeita o toggle global — aqui é só disparar.
    const sid = this.id();
    if (sid) {
      this.api
        .instructMilestones(sid)
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe({ error: () => {} });
    }

    // Espelho PUSHADO: o worker empurra a tela (SSE) assim que muda. Aplicamos
    // o último frame da NOSSA sessão (casado por tmux_name) — feedback quase
    // imediato, sem esperar poll. Atualiza scroll-stick antes de trocar.
    effect(() => {
      const tn = this.session()?.tmux_name;
      if (!tn) {
        return;
      }
      const scr = this.sse.screens()[tn];
      if (scr && scr.text !== this.screen()) {
        this.stickToBottom = this.isAtBottom();
        this.screen.set(scr.text);
      }
    });

    // Fallback: se o SSE cair, um poll lento (4s) garante que a tela não trava.
    // Aproveita p/ atualizar as tarefas (worker sincroniza ~6s).
    const poll = setInterval(() => {
      this.refreshScreen();
      this.loadTasks();
    }, 4000);
    this.destroyRef.onDestroy(() => {
      clearInterval(poll);
      if (this.liveTimer) {
        clearTimeout(this.liveTimer);
      }
    });
  }

  ngAfterViewChecked(): void {
    const text = this.screen();
    if (text !== this.lastRenderedScreen) {
      this.lastRenderedScreen = text;
      // Só desce se o usuário estava no fim (capturado antes do update em
      // refreshScreen); caso contrário preserva a posição de leitura.
      if (this.stickToBottom) {
        this.scrollToBottom();
      }
    }
  }

  /** Métricas reais enriquecidas pelo backend (ou null se indisponíveis). */
  protected readonly metrics = computed<SessionMetrics | null>(
    () => this.session()?.metrics ?? null,
  );

  /** Atividade real (claude stats-cache) ou null pra sessões não-claude. */
  protected readonly activity = computed(() => this.metrics()?.activity ?? null);

  /** Limites reais de uso (sessão 5h + semanal) ou null se ausentes. */
  protected readonly limits = computed(() => this.metrics()?.limits ?? null);

  /** Cor da barra de limite: <70% mint, 70-85% âmbar, ≥85% vermelho. */
  protected limitColor(pct: number | null | undefined): string {
    const p = pct ?? 0;
    if (p >= 85) {
      return '#F87171';
    }
    if (p >= 70) {
      return '#FBBF24';
    }
    return '#00E4B4';
  }

  /** Formata contagem inteira com separador de milhar pt-BR (5977 → "5.977"). */
  protected fmtCount(n: number | null | undefined): string {
    if (n == null) {
      return '—';
    }
    return n.toLocaleString('pt-BR');
  }

  /** MODELO: prioriza metrics.model, depois session.model, senão "—". */
  protected readonly modelLabel = computed<string>(
    () => this.metrics()?.model || this.session()?.model || '—',
  );

  /** Cor da barra de contexto: <70% mint, 70-85% âmbar, ≥85% vermelho. */
  protected readonly ctxColor = computed<string>(() => {
    const pct = this.metrics()?.context_pct ?? 0;
    if (pct >= 85) {
      return '#F87171';
    }
    if (pct >= 70) {
      return '#FBBF24';
    }
    return '#00E4B4';
  });

  /** Formata tokens em k (mockup): 248000→"248k", 18300→"18,3k". */
  protected fmtTok(n: number | null | undefined): string {
    if (n == null) {
      return '—';
    }
    if (n >= 1000) {
      const k = Math.round((n / 1000) * 10) / 10;
      return String(k).replace('.', ',') + 'k';
    }
    return String(n);
  }

  protected displayName(): string {
    const s = this.session();
    return s?.display_name || s?.tmux_name || this.id();
  }

  protected agent() {
    return agentMeta(this.session()?.agent_type ?? 'desconhecido');
  }

  protected statusMeta() {
    const s = this.session();
    return (s && STATUS_META[s.status]) || STATUS_META.detached;
  }

  protected goBack(): void {
    // Volta para a tela de origem (Início ou Sessões), não um destino fixo.
    // navigationId > 1 ⇒ há histórico in-app; senão (deep-link/notificação)
    // cai para o Início para não sair do app.
    const navId = (history.state && history.state.navigationId) || 1;
    if (navId > 1) {
      this.location.back();
    } else {
      void this.router.navigate(['/inicio']);
    }
  }

  protected resume(): void {
    if (this.acting() || !this.id()) {
      return;
    }
    this.acting.set(true);
    this.api
      .resumeSession(this.id())
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (s) => {
          this.session.set(s);
          this.acting.set(false);
        },
        error: () => this.acting.set(false),
      });
  }

  protected end(): void {
    if (this.acting() || !this.id()) {
      return;
    }
    this.acting.set(true);
    this.api
      .deleteSession(this.id())
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.acting.set(false);
          void this.router.navigate(['/sessoes']);
        },
        error: () => this.acting.set(false),
      });
  }

  protected send(): void {
    const id = this.id();
    if (!id) {
      return;
    }
    // Modo ao vivo: o texto JÁ está no pane (foi encaminhado enquanto digitava)
    // → submeter é só um Enter. Limpa o estado local + buffer.
    if (this.liveMode()) {
      this.flushForward(); // garante que o último diff foi enviado
      this.api
        .sendKey(id, 'enter')
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe({ next: () => this.refreshScreen(), error: () => {} });
      this.draft.set('');
      this.paneBuffer = '';
      return;
    }
    if (!this.canSend()) {
      return;
    }
    const text = this.draft().trim();
    // Sem eco local: o espelho de tela mostra o que foi digitado na caixa do
    // agente no próximo poll (~1.2s).
    this.sending.set(true);
    this.api
      .sendInput(this.id(), text)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.draft.set('');
          this.sending.set(false);
        },
        error: () => this.sending.set(false),
      });
  }

  /** Atualiza o draft e, no modo ao vivo, agenda o encaminhamento do diff. */
  protected onDraftChange(value: string): void {
    this.draft.set(value);
    if (this.liveMode()) {
      this.scheduleForward();
    }
  }

  /** Liga/desliga o modo ao vivo, mantendo pane e draft coerentes. */
  protected toggleLive(): void {
    const id = this.id();
    const turningOn = !this.liveMode();
    if (!turningOn && id && this.paneBuffer) {
      // Desligando: apaga no pane o que foi digitado ao vivo (volta ao lote sem
      // duplicar o texto quando enviar depois).
      this.backspacePane(this.paneBuffer.length);
    }
    this.paneBuffer = '';
    this.liveMode.set(turningOn);
    // Ligando com algo já digitado: encaminha o draft atual de uma vez.
    if (turningOn && id && this.draft()) {
      this.forwardDiff();
    }
  }

  private scheduleForward(): void {
    if (this.liveTimer) {
      clearTimeout(this.liveTimer);
    }
    this.liveTimer = setTimeout(() => this.forwardDiff(), 130);
  }

  private flushForward(): void {
    if (this.liveTimer) {
      clearTimeout(this.liveTimer);
      this.liveTimer = null;
    }
    this.forwardDiff();
  }

  /**
   * Encaminha ao pane a diferença entre o draft e o que já foi enviado
   * (``paneBuffer``): apaga o sufixo divergente (Backspace) e digita o novo
   * trecho (texto sem Enter). Mantém o pane sincronizado com a caixa.
   */
  private forwardDiff(): void {
    const id = this.id();
    if (!id) {
      return;
    }
    const target = this.draft();
    const old = this.paneBuffer;
    if (target === old) {
      return;
    }
    let common = 0;
    const max = Math.min(old.length, target.length);
    while (common < max && old[common] === target[common]) {
      common++;
    }
    const backs = old.length - common;
    const append = target.slice(common);
    this.paneBuffer = target;
    if (backs > 0) {
      this.backspacePane(backs);
    }
    if (append) {
      this.api
        .sendInput(id, append, false)
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe({ next: () => this.refreshScreen(), error: () => {} });
    } else {
      this.refreshScreen();
    }
  }

  /** Envia ``n`` Backspaces ao pane (apaga ``n`` chars da caixa do agente). */
  private backspacePane(n: number): void {
    const id = this.id();
    if (!id) {
      return;
    }
    for (let i = 0; i < n; i++) {
      this.api
        .sendKey(id, 'backspace')
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe({ error: () => {} });
    }
  }

  /**
   * Envia uma tecla especial (seta/enter/espaço/esc) ao pane para navegar
   * prompts TUI. O espelho de tela reflete o efeito no próximo poll (~1.2s).
   */
  protected pressKey(key: TerminalKey): void {
    const id = this.id();
    if (!id) {
      return;
    }
    // Fire-and-forget: NÃO bloqueia os botões durante o round-trip (no celular
    // ele tem centenas de ms via túnel). Bloquear deixava o keypad "morto" e
    // só piscando. Teclas são baratas/idempotentes → pode disparar à vontade.
    this.api
      .sendKey(id, key)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => this.refreshScreen(),
        error: () => {
          /* best-effort — ignora falha transitória */
        },
      });
  }

  /**
   * O áudio é transcrito no backend e injetado no pane do agente; o resultado
   * aparece no espelho de tela no próximo poll. Sem ação local necessária.
   */
  protected onAudioTranscribing(_active: boolean): void {
    // noop — o espelho reflete a tela real do agente.
  }

  /** Após upload do áudio o backend processa/transcreve; nada a fazer aqui. */
  protected onAudioUploaded(): void {
    // O resultado aparece no espelho de tela; sem ação local necessária.
  }

  private loadSession(): void {
    if (!this.id()) {
      return;
    }
    this.api
      .getSession(this.id())
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (s) => {
          this.session.set(s);
          this.loadTasks();
        },
        error: () => {
          /* mantém cabeçalho com o id como fallback */
        },
      });
  }

  /** Cor por estado da tarefa. */
  protected taskColor(state: string | null | undefined): string {
    return (
      { done: '#34D399', doing: '#FBBF24', blocked: '#F87171', attention: '#F87171' }[
        state ?? ''
      ] ?? '#7A8090'
    );
  }

  /** Rótulo PT por estado da tarefa. */
  protected taskLabel(state: string | null | undefined): string {
    return (
      {
        done: 'Concluída',
        doing: 'Em andamento',
        blocked: 'Bloqueada',
        attention: 'Atenção',
        todo: 'A fazer',
      }[state ?? ''] ?? 'A fazer'
    );
  }

  /** Carrega as tarefas/marcos desta sessão (por tmux_name). */
  private loadTasks(): void {
    const tn = this.session()?.tmux_name;
    if (!tn) {
      return;
    }
    this.api
      .getTasks(tn)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (list) => this.tasks.set(list ?? []),
        error: () => {
          /* mantém estado anterior */
        },
      });
  }

  /**
   * Busca a tela visível atual do pane e SUBSTITUI o espelho. Best-effort:
   * ignora erros transitórios (mantém a última tela em tela).
   */
  private refreshScreen(): void {
    const id = this.id();
    if (!id) {
      return;
    }
    this.api
      .getScreen(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (resp) => {
          // Captura ANTES do update: o usuário estava colado no fim?
          this.stickToBottom = this.isAtBottom();
          this.screen.set(resp.text ?? '');
        },
        error: () => {
          /* poll é best-effort; ignora erro transitório */
        },
      });
  }

  /** True se a viewport do terminal está (quase) no fim — tolerância de 48px. */
  private isAtBottom(): boolean {
    const el = this.termEl()?.nativeElement;
    if (!el) {
      return true; // antes do primeiro render, queremos descer.
    }
    return el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  }

  private scrollToBottom(): void {
    const el = this.termEl()?.nativeElement;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }
}
