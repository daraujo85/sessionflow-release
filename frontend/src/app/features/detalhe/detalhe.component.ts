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
import { ShareSessionService } from '../../core/share-session.service';
import { DraftStore } from '../../core/draft-store';
import { SessionPrefsStore } from '../../core/session-prefs-store';
import { WorkersStore } from '../../core/workers-store';
import {
  AgentType,
  Schedule,
  Session,
  SessionMetrics,
  ShareLink,
  SharedFile,
  Task,
  TerminalKey,
} from '../../core/models';
import { STATUS_META, agentMeta } from '../../shared/status-color';
import { AudioRecorderComponent } from '../../shared/audio-recorder/audio-recorder.component';
import { SessionPanelComponent } from './session-panel.component';
import { ansiToHtml, trimBlankEdges } from '../../shared/ansi-html';

@Component({
  selector: 'sf-detalhe',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, AudioRecorderComponent, SessionPanelComponent],
  template: `
    <section class="overlay" [class.focus]="focusMode()" [class.split-active]="!!compareId()">
      <!-- Header -->
      <header class="hdr">
        <div class="hdr-top">
          @if (!guest()) {
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
          } @else {
            <span class="guest-badge" title="Você está vendo um link compartilhado desta sessão">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M18 8a6 6 0 0 0-9.33-5M6 8a6 6 0 0 0 9.33 5" />
                <circle cx="12" cy="12" r="3" /><path d="M12 2v2M12 20v2" />
              </svg>
              Compartilhado
            </span>
          }

          <div class="hdr-info">
            <div class="hdr-title">
              <span class="hdr-name">{{ displayName() }}</span>
              @if (!guest()) {
                <button type="button" class="hdr-rename" (click)="openRename()"
                        aria-label="Renomear sessão" title="Renomear (nome técnico + nome falado)">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                       stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
                  </svg>
                </button>
              }
              <!-- Status AO LADO do nome: aproveita o espaço vazio da linha do
                   título e libera a linha de baixo só pros botões (no celular a
                   pill quebrava sozinha e desperdiçava uma linha inteira). -->
              <span
                class="status-pill"
                [style.color]="statusMeta().color"
                [style.background]="statusMeta().dot + '1f'"
              >
                <span class="status-dot" [style.background]="statusMeta().dot"></span>
                <span class="status-label">{{ statusMeta().label }}</span>
              </span>
              @if (hostBadge(); as host) {
                <span class="host-pill" [title]="'Roda em: ' + host">
                  @if (hostEmoji(); as emoji) {
                    <span aria-hidden="true">{{ emoji }}</span>
                  } @else {
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                         stroke="currentColor" stroke-width="2" stroke-linecap="round"
                         stroke-linejoin="round" aria-hidden="true">
                      <rect x="3" y="4" width="18" height="8" rx="2" />
                      <rect x="3" y="12" width="18" height="8" rx="2" />
                      <path d="M7 8h.01M7 16h.01" />
                    </svg>
                  }
                  <span class="host-label">{{ host }}</span>
                </span>
              }
            </div>
            <div class="mono hdr-dir">{{ session()?.work_dir || '—' }}</div>
            @if (activeTask()) {
              <div class="hdr-task">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="8" y="2" width="8" height="4" rx="1" /><path d="M9 4H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-3" /><path d="m9 14 2 2 4-4" /></svg>
                Tarefa: {{ activeTask() }}
              </div>
            }
          </div>

          <!-- Compacto: status + botões + badge sobem pra linha do topo (some a
               linha de status separada → mais espaço). Em tela estreita quebra
               pra baixo (flex-wrap). -->
          <div class="hdr-controls">
          <span class="status-actions">
            @if (!guest()) {
              <button
                type="button"
                class="act act--ghost"
                [class.on]="shareOpen()"
                (click)="toggleShare()"
                [attr.aria-pressed]="shareOpen()"
                aria-label="Compartilhar a sessão"
                title="Gerar um link temporário pra alguém ver/controlar só esta sessão"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <circle cx="18" cy="5" r="3" /><circle cx="6" cy="12" r="3" /><circle cx="18" cy="19" r="3" />
                  <path d="m8.6 13.5 6.8 4M15.4 6.5l-6.8 4" />
                </svg>
              </button>
            }
            @if (hostSupportsTts()) {
              <button
                type="button"
                class="act act--jarvis"
                [class.on]="!!session()?.jarvis"
                (click)="toggleJarvis()"
                [attr.aria-pressed]="!!session()?.jarvis"
                aria-label="JARVIS: resumo falado desta sessão"
                title="JARVIS — fala um resumo no celular quando a sessão concluir/aguardar"
              >
                @if (session()?.jarvis) {
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                       stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M11 5 6 9H2v6h4l5 4V5z" />
                    <path d="M15.5 8.5a5 5 0 0 1 0 7M19 5a9 9 0 0 1 0 14" />
                  </svg>
                } @else {
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                       stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M11 5 6 9H2v6h4l5 4V5z" />
                    <path d="M22 9l-6 6M16 9l6 6" />
                  </svg>
                }
              </button>
            }
            <button
              type="button"
              class="act act--ghost"
              [class.on]="focusMode()"
              (click)="toggleFocus()"
              [attr.aria-pressed]="focusMode()"
              aria-label="Modo foco: recolhe Modelo e Tarefas p/ o terminal ocupar mais espaço"
              [title]="focusMode() ? 'Mostrar Modelo e Tarefas' : 'Modo foco (mais espaço pro terminal)'"
            >
              @if (focusMode()) {
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M9 3H5a2 2 0 0 0-2 2v4M15 3h4a2 2 0 0 1 2 2v4M9 21H5a2 2 0 0 1-2-2v-4M15 21h4a2 2 0 0 0 2-2v-4" />
                </svg>
              } @else {
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M3 9V5a2 2 0 0 1 2-2h4M21 9V5a2 2 0 0 0-2-2h-4M3 15v4a2 2 0 0 0 2 2h4M21 15v4a2 2 0 0 1-2 2h-4" />
                </svg>
              }
            </button>
            @if (hostSupportsOpenTerminal()) {
              <button
                type="button"
                class="act act--ghost"
                (click)="openInMac()"
                aria-label="Abrir no terminal do Mac"
                title="Abrir esta sessão num terminal do Mac (tmux attach, lado a lado)"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <rect x="2.5" y="4" width="19" height="16" rx="2" />
                  <path d="M6.5 9l3 3-3 3M13 15h4" />
                </svg>
              </button>
            }
            @if (!guest() && !compareId()) {
              <button
                type="button"
                class="act act--ghost"
                (click)="openSplitPicker()"
                aria-label="Dividir tela com outra sessão"
                title="Dividir tela: acompanhar/conversar com outra sessão lado a lado"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <rect x="3" y="4" width="18" height="16" rx="2" />
                  <path d="M12 4v16" />
                </svg>
              </button>
            }
            @if (!guest()) {
              <button
                type="button"
                class="act act--ghost"
                [class.on]="schedulesOpen()"
                (click)="toggleSchedules()"
                [attr.aria-pressed]="schedulesOpen()"
                aria-label="Comandos programados"
                title="Comandos programados: instrução recorrente pro terminal (ex: rodar uma skill de hora em hora)"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <circle cx="12" cy="12" r="9" />
                  <path d="M12 7v5l3 3" />
                </svg>
              </button>
            }
            @if (!guest()) {
              <button
                type="button"
                class="act act--ghost"
                [class.on]="sharedFilesOpen()"
                (click)="toggleSharedFiles()"
                [attr.aria-pressed]="sharedFilesOpen()"
                aria-label="Arquivos compartilhados"
                title="Arquivos que o agente compartilhou (ver/baixar, inclusive fora do Mac)"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M21.44 11.05 12.25 20.24a5 5 0 0 1-7.07-7.07l9.19-9.19a3.5 3.5 0 0 1 4.95 4.95L10.13 17.9a2 2 0 0 1-2.83-2.83l8.49-8.48" />
                </svg>
                @if (sharedFiles().length > 0) {
                  <span class="act-badge">{{ sharedFiles().length }}</span>
                }
              </button>
            }
            @if (isRunning()) {
              <button
                type="button"
                class="act act--danger"
                [disabled]="acting()"
                (click)="end()"
                aria-label="Parar a sessão"
                title="Parar a sessão (mantém o registro; pode retomar depois)"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                  <rect x="6" y="6" width="12" height="12" rx="2.5" />
                </svg>
              </button>
            } @else {
              <button
                type="button"
                class="act act--ghost"
                [disabled]="acting()"
                (click)="resume()"
                aria-label="Retomar a sessão"
                title="Retomar a sessão (continua de onde parou)"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                  <path d="M8 5v14l11-7z" />
                </svg>
              </button>
            }
            <button
              type="button"
              class="act act--trash"
              [disabled]="acting()"
              (click)="eliminate()"
              aria-label="Eliminar sessão (remove do host e do app)"
              title="Eliminar de vez (some do Mac e daqui)"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6" />
              </svg>
            </button>
          </span>
          <span
            class="agent-badge"
            [class.agent-badge--btn]="!guest()"
            [style.color]="agent().color"
            [style.background]="agent().color + '22'"
            (click)="toggleSwitchFromHeader()"
            [attr.title]="guest() ? null : 'Trocar provedor desta sessão'"
            [attr.role]="guest() ? null : 'button'"
            >{{ agent().short }}</span
          >
          </div>
        </div>
      </header>

      <!-- Picker "dividir tela": escolhe a 2ª sessão -->
      @if (splitPickerOpen()) {
        <section class="split-picker" aria-label="Escolher sessão para dividir tela">
          <div class="split-picker-hdr">
            <span>Dividir tela com…</span>
            <button type="button" class="close-btn" (click)="closeSplitPicker()">✕</button>
          </div>
          <input
            type="text"
            class="split-picker-search"
            placeholder="Pesquisar sessão…"
            [ngModel]="splitSearch()"
            (ngModelChange)="splitSearch.set($event)"
            name="splitSearch"
            autocomplete="off"
          />
          @if (splitCandidatesFiltered().length === 0) {
            <div class="split-picker-empty">
              {{ splitCandidates().length === 0 ? 'Nenhuma outra sessão ativa.' : 'Nenhuma sessão casa com a busca.' }}
            </div>
          }
          @for (s of splitCandidatesFiltered(); track s.id) {
            <button type="button" class="split-picker-item" (click)="pickSplit(s.id)">
              <span class="dot" [style.background]="STATUS_META[s.status].color"></span>
              <span class="name">{{ s.display_name || s.tmux_name }}</span>
              <span class="status">{{ STATUS_META[s.status].label }}</span>
            </button>
          }
        </section>
      }

      <!-- Modo split: duas sessões lado a lado (painel leve, ver
           SessionPanelComponent) — some com o resto do corpo via CSS
           (.overlay.split-active) sem mexer no template normal abaixo.
           Divisor arrastável: arraste pra dar mais espaço a um lado (horizontal
           no desktop, vertical quando empilha no celular); duplo-toque no
           divisor volta pro 50/50. -->
      @if (compareId(); as cmp) {
        <div class="split-view" #splitViewEl [class.dragging]="splitDragging()">
          <div class="split-pane" [style.flex]="splitFlexA()">
            <app-session-panel [sessionId]="id()" (closeRequested)="exitSplit()" />
          </div>
          <div
            class="split-divider"
            (pointerdown)="onSplitDividerDown($event)"
            (dblclick)="resetSplitRatio()"
            aria-label="Arraste pra redimensionar os painéis; duplo-toque reseta 50/50"
            role="separator"
          >
            <span class="split-grip"></span>
          </div>
          <div class="split-pane" [style.flex]="splitFlexB()">
            <app-session-panel [sessionId]="cmp" (closeRequested)="exitSplit()" />
          </div>
        </div>
      }

      <!-- Painel "Compartilhar" (só dono): gera/copia/revoga o link temporário -->
      @if (shareOpen() && !guest()) {
        <section class="share" aria-label="Compartilhar sessão">
          @if (shareLink()?.active) {
            <div class="share-row">
              <input class="share-url mono" type="text" readonly [value]="shareLink()?.url || ''" (focus)="$any($event.target).select()" />
              <button type="button" class="share-btn share-btn--primary" (click)="copyShareLink()">
                {{ shareCopied() ? 'Copiado!' : 'Copiar' }}
              </button>
            </div>
            <div class="share-foot">
              <span class="share-hint">Vale 24h · morre se a sessão for parada/eliminada · controle total</span>
              <span class="share-acts">
                <button type="button" class="share-link-btn" [disabled]="shareBusy()" (click)="generateShareLink()">Gerar novo</button>
                <button type="button" class="share-link-btn share-link-btn--danger" [disabled]="shareBusy()" (click)="revokeShareLink()">Revogar</button>
              </span>
            </div>
          } @else {
            <div class="share-row">
              <span class="share-hint">Nenhum link ativo. Crie um link temporário pra alguém ver e controlar só esta sessão (não acessa o resto do app).</span>
            </div>
            <div class="share-foot">
              <span class="share-hint">Expira em 24h e para de funcionar se você parar/eliminar a sessão.</span>
              <button type="button" class="share-btn share-btn--primary" [disabled]="shareBusy()" (click)="generateShareLink()">
                {{ shareBusy() ? 'Gerando…' : 'Gerar link' }}
              </button>
            </div>
          }
        </section>
      }

      <!-- Painel "Renomear": nome técnico (tmux/Claude Code) + nome falado (TTS) -->
      @if (renameOpen() && !guest()) {
        <section class="rename" aria-label="Renomear sessão">
          <label class="rename-field">
            <span class="rename-lbl">Nome (tmux / Claude Code)</span>
            <input class="rename-input mono" type="text" [ngModel]="renameTech()"
                   (ngModelChange)="renameTech.set($event)" placeholder="ex: garagem-codigo"
                   autocomplete="off" spellcheck="false" />
            <span class="rename-hint">Técnico: só letras/números/-/_ (espaços viram "-").</span>
          </label>
          <label class="rename-field">
            <span class="rename-lbl">Nome falado (app e voz)</span>
            <input class="rename-input" type="text" [ngModel]="renameDisp()"
                   (ngModelChange)="renameDisp.set($event)" placeholder="ex: Garagem do Código"
                   autocomplete="off" />
            <span class="rename-hint">Livre (acentos/espaços). Usado no app e no TTS.</span>
          </label>
          <div class="rename-acts">
            <button type="button" class="rename-btn" [disabled]="renaming()" (click)="closeRename()">Cancelar</button>
            <button type="button" class="rename-btn rename-btn--primary" [disabled]="renaming()" (click)="saveRename()">
              {{ renaming() ? 'Salvando…' : 'Salvar' }}
            </button>
          </div>
        </section>
      }

      <!-- Painel "Trocar provedor": mesmo tmux/registro, com handoff de
           contexto. Abre pelo badge do agente no topo OU pelo botão nas
           métricas (painel de topo, como o Renomear). -->
      @if (switchOpen() && !guest()) {
        <section class="rename" aria-label="Trocar provedor da sessão">
          <label class="rename-field">
            <span class="rename-lbl">Provedor</span>
            <select
              class="rename-input"
              [ngModel]="switchAgentSel()"
              (ngModelChange)="switchAgentSel.set($event)"
            >
              @for (p of switchProviders; track p) {
                <option [value]="p">{{ p }}</option>
              }
            </select>
          </label>
          <label class="rename-field">
            <span class="rename-lbl">Modelo</span>
            <input
              class="rename-input mono"
              type="text"
              [ngModel]="switchModel()"
              (ngModelChange)="switchModel.set($event)"
              placeholder="modelo (opcional — default do provedor)"
              autocomplete="off"
              spellcheck="false"
            />
          </label>
          <span class="rename-hint"
            >O agente atual grava um handoff do contexto e o novo provedor
            assume ESTA sessão (mesmo diretório, registro e histórico).</span
          >
          <div class="rename-acts">
            <button type="button" class="rename-btn" [disabled]="switching()"
                    (click)="switchOpen.set(false)">Cancelar</button>
            <button type="button" class="rename-btn rename-btn--primary"
                    [disabled]="switching()" (click)="confirmSwitch()">
              {{ switching() ? 'Trocando…' : 'Confirmar' }}
            </button>
          </div>
        </section>
      }

      <!-- Painel "Comandos programados": instrução recorrente pro terminal
           desta sessão (criar/pausar/retomar/editar/excluir). -->
      @if (schedulesOpen() && !guest()) {
        <section class="rename" aria-label="Comandos programados">
          <div class="sched-list">
            @for (s of schedules(); track s.id) {
              <div class="sched-item" [class.sched-item--paused]="!s.enabled">
                <div class="sched-item-main">
                  <span class="mono sched-text">{{ s.text }}</span>
                  <span class="sched-meta">
                    a cada {{ formatInterval(s.interval_seconds) }}
                    @if (s.enabled && s.next_run_at) {
                      · próxima em {{ formatRelative(s.next_run_at) }}
                    } @else {
                      · pausado
                    }
                    @if (s.last_error) {
                      · <span class="sched-error">último erro: {{ s.last_error }}</span>
                    }
                  </span>
                </div>
                <div class="sched-item-acts">
                  <button
                    type="button"
                    class="sched-toggle"
                    role="switch"
                    [attr.aria-checked]="s.enabled"
                    [class.on]="s.enabled"
                    [disabled]="scheduleBusy() === s.id"
                    [attr.title]="s.enabled ? 'Pausar' : 'Retomar'"
                    (click)="toggleScheduleEnabled(s)"
                  >
                    <span class="sched-toggle-knob"></span>
                  </button>
                  <button
                    type="button"
                    class="rename-btn rename-btn--danger"
                    [disabled]="scheduleBusy() === s.id"
                    (click)="deleteSchedule(s)"
                  >
                    Excluir
                  </button>
                </div>
              </div>
            } @empty {
              <span class="rename-hint">Nenhum comando programado ainda.</span>
            }
          </div>

          <label class="rename-field">
            <span class="rename-lbl">Nova instrução</span>
            <input
              class="rename-input mono"
              type="text"
              [ngModel]="newScheduleText()"
              (ngModelChange)="newScheduleText.set($event)"
              placeholder="ex: rode a skill de revisão de PRs abertos"
              autocomplete="off"
            />
          </label>
          <label class="rename-field">
            <span class="rename-lbl">Repetir a cada</span>
            <span class="sched-interval-row">
              <input
                class="rename-input mono sched-interval-num"
                type="number"
                min="1"
                [ngModel]="newScheduleIntervalValue()"
                (ngModelChange)="newScheduleIntervalValue.set($event)"
              />
              <select
                class="rename-input sched-interval-unit"
                [ngModel]="newScheduleIntervalUnit()"
                (ngModelChange)="newScheduleIntervalUnit.set($event)"
              >
                <option value="60">minutos</option>
                <option value="3600">horas</option>
                <option value="86400">dias</option>
              </select>
            </span>
          </label>
          <div class="rename-acts">
            <button type="button" class="rename-btn" (click)="closeSchedules()">Fechar</button>
            <button
              type="button"
              class="rename-btn rename-btn--primary"
              [disabled]="!newScheduleText().trim() || schedulesCreating()"
              (click)="createSchedule()"
            >
              {{ schedulesCreating() ? 'Criando…' : 'Criar' }}
            </button>
          </div>
        </section>
      }

      <!-- Painel "Arquivos compartilhados": o agente sobe (via 'sf share') e
           o usuário vê/baixa aqui, inclusive fora do Mac. -->
      @if (sharedFilesOpen() && !guest()) {
        <section class="rename" aria-label="Arquivos compartilhados">
          <div class="sched-list">
            @for (f of sharedFiles(); track f.id) {
              <div class="sched-item">
                <a
                  class="shared-file-main"
                  [href]="sharedFileUrl(f.id)"
                  target="_blank"
                  rel="noopener"
                >
                  <span class="mono sched-text">{{ f.filename }}</span>
                  <span class="sched-meta">
                    {{ formatFileSize(f.size) }} · {{ formatRelativePast(f.created_at) }}
                  </span>
                </a>
                <div class="sched-item-acts">
                  <button
                    type="button"
                    class="rename-btn rename-btn--danger"
                    [disabled]="sharedFileBusy() === f.id"
                    (click)="deleteSharedFile(f)"
                  >
                    Excluir
                  </button>
                </div>
              </div>
            } @empty {
              <span class="rename-hint">
                Nenhum arquivo compartilhado ainda. Peça pro agente rodar
                <code class="mono">sf share &lt;caminho&gt;</code> pra um arquivo
                aparecer aqui.
              </span>
            }
          </div>
          <div class="rename-acts">
            <button type="button" class="rename-btn" (click)="closeSharedFiles()">Fechar</button>
          </div>
        </section>
      }

      <!-- Bloco de métricas (recolhido por padrão; topo resume modelo + ctx%) -->
      <section class="metrics" aria-label="Métricas da sessão">
        <div
          class="metrics-top metrics-top--toggle"
          (click)="metricsOpen.set(!metricsOpen())"
        >
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
          <svg class="metrics-chev" [class.open]="metricsOpen()" width="18" height="18"
               viewBox="0 0 24 24" fill="none" stroke="#7A8090" stroke-width="2"
               stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="m6 9 6 6 6-6" />
          </svg>
        </div>

        @if (metricsOpen()) {

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

        <!-- Custo estimado de tokens (preço de API), quebrado por modelo -->
        @if (cost(); as c) {
          <div class="cost-card">
            <div class="cost-head">
              <span class="mcard-label">Custo estimado</span>
              <span class="mono cost-total">
                {{ c.total_usd != null ? '~$' + fmtUsd(c.total_usd) : '—' }}
                @if (c.total_brl != null) {
                  <span class="cost-brl">· ~R$ {{ fmtUsd(c.total_brl) }}</span>
                }
              </span>
            </div>
            @if (costRows().length > 0) {
              <div class="cost-rows">
                @for (r of costRows(); track r.model) {
                  <div class="cost-row">
                    <span class="cost-model">{{ r.model }}</span>
                    <span class="mono cost-toks"
                      >in {{ fmtTok(r.input) }} · out {{ fmtTok(r.output) }} ·
                      cache {{ fmtTok(r.cache_read + r.cache_write) }}</span
                    >
                    <span class="mono cost-usd">{{
                      r.usd != null
                        ? '~$' + fmtUsd(r.usd)
                        : '— (preço desconhecido)'
                    }}</span>
                  </div>
                }
              </div>
            }
            <div class="cost-note">
              Estimativa em preço de API; assinatura não cobra por token.
              @if (c.brl_rate != null) {
                Câmbio do dia: US$ 1 = R$ {{ fmtUsd(c.brl_rate) }}.
              }
            </div>
          </div>
        }

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

        <!-- Trocar de PROVEDOR nesta MESMA sessão (handoff de contexto) -->
        @if (!guest()) {
          <div class="switch-row">
            <button
              type="button"
              class="switch-btn"
              [disabled]="switching()"
              (click)="openSwitch()"
              title="Trocar o provedor desta sessão (o contexto é transferido)"
            >
              {{ switching() ? 'Trocando provedor…' : 'Trocar provedor' }}
            </button>
          </div>
        }
        }
      </section>

      <!-- Tarefas da sessão (marcos) — recolhível p/ não roubar o terminal -->
      @if (tasks().length > 0) {
        <div class="tasks">
          <button type="button" class="tasks-head" (click)="tasksOpen.set(!tasksOpen())">
            <span class="tasks-head-top">
              <span class="tasks-title">Tarefas ({{ tasks().length }})</span>
              <span class="tasks-sub">{{ tasksDoneCount() }}/{{ tasks().length }} concluídas</span>
              <svg class="tasks-chev" [class.open]="tasksOpen()" width="18" height="18" viewBox="0 0 24 24"
                   fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="m6 9 6 6 6-6" />
              </svg>
            </span>
            @if (currentTask(); as ct) {
              <span class="tasks-current">
                <span class="tasks-current-dot" [style.background]="taskColor(ct.state)"></span>
                <span class="tasks-current-lead" [style.color]="taskColor(ct.state)">{{ currentTaskLead() }}:</span>
                <span class="tasks-current-title">{{ ct.title }}</span>
              </span>
            }
          </button>
          @if (tasksOpen()) {
            <div class="tasks-filters">
              @for (f of taskFilters; track f.key) {
                <button
                  type="button"
                  class="tasks-filter"
                  [class.sel]="taskStatusFilter() === f.key"
                  (click)="taskStatusFilter.set(taskStatusFilter() === f.key ? 'all' : f.key)"
                >
                  {{ f.label }}
                </button>
              }
            </div>
            <ul class="tasks-list" (scroll)="onTasksScroll($event)">
              @for (t of visibleTasks(); track t.id) {
                <li class="tasks-item">
                  <span class="tasks-dot" [style.background]="taskColor(t.state)"></span>
                  <span class="tasks-item-title">{{ t.title }}</span>
                  <span class="tasks-item-state" [style.color]="taskColor(t.state)">{{ taskLabel(t.state) }}</span>
                </li>
              } @empty {
                <li class="tasks-empty">Nenhuma tarefa nesse status.</li>
              }
              @if (remainingTasks() > 0) {
                <li class="tasks-more">
                  <button type="button" class="tasks-more-btn" (click)="showMoreTasks()">
                    Ver mais {{ remainingTasks() }}
                  </button>
                </li>
              }
            </ul>
          }
        </div>
      }

      <!-- Barra do terminal: alterna entre espelho AO VIVO e HISTÓRICO rolável -->
      <div class="term-bar">
        <!-- Último artifact visto na sessão: o rodapé "⧉ <nome>" do Claude Code
             NÃO é OSC 8 (o clique é tratado pelo próprio TUI no Mac) — aqui
             oferecemos a URL real capturada da conversa, clicável no celular. -->
        @if (artifactUrl(); as au) {
          <span class="term-artifact-group">
            <a class="term-artifact" [href]="au" target="_blank" rel="noopener noreferrer"
               title="Abrir o artifact mais recente desta sessão">
              ⧉ artifact
            </a>
            @if (artifactList().length > 1) {
              <button type="button" class="term-artifact term-artifact-more"
                      (click)="artifactMenuOpen.set(!artifactMenuOpen())"
                      [attr.aria-expanded]="artifactMenuOpen()"
                      aria-label="Ver todos os artifacts da sessão">
                {{ artifactList().length }} ▾
              </button>
            }
          </span>
        }
        <!-- Tamanho da fonte do terminal: A− / A+ (persistido por aparelho). -->
        <div class="term-scroll">
          <button type="button" class="term-scroll-btn term-font-btn" (click)="bumpFont(-1)"
                  aria-label="Diminuir fonte" title="Diminuir fonte do terminal">A−</button>
          <button type="button" class="term-scroll-btn term-font-btn" (click)="bumpFont(1)"
                  aria-label="Aumentar fonte" title="Aumentar fonte do terminal">A+</button>
        </div>

        <!-- Rolagem do scrollback: manda ▲/▼ direto pro agente (roda do mouse
             via tmux), que redesenha o próprio histórico interno. -->
        <div class="term-scroll">
          <button type="button" class="term-scroll-btn" (click)="scrollTerm('up')"
                  aria-label="Rolar para cima" title="Subir (histórico do terminal)">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="m6 15 6-6 6 6" />
            </svg>
          </button>
          <button type="button" class="term-scroll-btn" (click)="scrollTerm('down')"
                  aria-label="Rolar para baixo" title="Descer">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="m6 9 6 6 6-6" />
            </svg>
          </button>
        </div>
      </div>

      <!-- Menu com o HISTÓRICO de artifacts da sessão (mais recente primeiro) -->
      @if (artifactMenuOpen() && artifactList().length > 1) {
        <div class="artifact-menu">
          @for (u of artifactList(); track u; let i = $index) {
            <a class="artifact-menu-item" [href]="u" target="_blank" rel="noopener noreferrer"
               (click)="artifactMenuOpen.set(false)">
              ⧉ {{ artifactLabel(u, i) }}
            </a>
          }
        </div>
      }

      <!-- Terminal: espelho ao vivo da tela atual do agente. -->
      <div class="term mono" #term aria-label="Tela do terminal" (scroll)="onTermScroll()"
           (wheel)="onTermWheel($event)"
           (touchstart)="onTermTouchStart($event)"
           (touchmove)="onTermTouchMove($event)"
           (mouseup)="onTermSelect()"
           (touchend)="onTermSelect()"
           [style.fontSize.px]="termFont()">
        @if (bufMode()) {
          <pre class="term-screen" [innerHTML]="bufHtml()"></pre>
        } @else if (screen().length === 0) {
          <div class="term-msg">Conectando ao terminal…</div>
        } @else {
          <pre class="term-screen" [innerHTML]="screenHtml()"></pre>
        }

        <!-- Buffer de scrollback: indicador de "carregando histórico". -->
        @if (bufMode()) {
          <div class="buf-loading" role="status" aria-live="polite">
            @if (bufLoading()) {
              carregando histórico… ({{ bufCount() }})
            } @else if (bufExhaustedUi()) {
              início do histórico · {{ bufCount() }} linhas
            } @else {
              histórico · {{ bufCount() }} linhas · role p/ cima
            }
          </div>
        }

        <!-- Toast "Copiado": confirma a cópia da seleção pro clipboard. -->
        @if (copied()) {
          <div class="term-copied" role="status" aria-live="polite">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M5 12l4 4 10-10" />
            </svg>
            Copiado
          </div>
        }

        <!-- Pill "↓ ao vivo": aparece no modo ao vivo quando o usuário rolou
             p/ cima; toca → snap pro fim e retoma o stick. -->
        @if (bufMode() || showLivePill()) {
          <button type="button" class="live-pill" (click)="snapToLive()" aria-label="Ir para o fim (ao vivo)">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M12 5v14M6 13l6 6 6-6" />
            </svg>
            ao vivo
          </button>
        }
      </div>

      <!-- Teclado de controle (recolhido por padrão p/ dar espaço ao terminal;
           expande no toque do botão ⌨ na barra de input) -->
      @if (keypadOpen()) {
        <div class="keypad kp-anim" role="group" aria-label="Teclas de navegação">
          <button type="button" class="key" (click)="pressKey('up')" aria-label="Cima">↑</button>
          <button type="button" class="key" (click)="pressKey('down')" aria-label="Baixo">↓</button>
          <button type="button" class="key" (click)="pressKey('left')" aria-label="Esquerda">←</button>
          <button type="button" class="key" (click)="pressKey('right')" aria-label="Direita">→</button>
          <button type="button" class="key key-wide" (click)="pressKey('space')">Espaço</button>
          <button type="button" class="key key-wide key-accent" (click)="pressKey('enter')">Enter</button>
          <button type="button" class="key" (click)="pressKey('escape')">Esc</button>
        </div>
      }

      <!-- Input bar -->
      <footer
        class="inputbar"
        [class.drag-over]="dragOver()"
        (dragover)="onDragOver($event)"
        (dragleave)="onDragLeave($event)"
        (drop)="onDrop($event)"
      >
        @if (dragOver()) {
          <div class="drop-hint" aria-hidden="true">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <path d="M7 10l5 5 5-5" />
              <path d="M12 15V3" />
            </svg>
            <span>Solte para anexar</span>
          </div>
        }
        <!-- Input de arquivo (sempre presente; abre via botão de anexar).
             NÃO usar [hidden]/display:none: vários navegadores Android (ex.:
             tablet Xiaomi) ignoram o .click() programático num input file
             escondido assim. Esconder visualmente off-screen mantém o picker. -->
        <input
          #fileInput
          type="file"
          accept="image/*,*/*"
          multiple
          class="visually-hidden"
          (change)="onFileSelected($event)"
        />

        @if (pendingItems().length > 0) {
          <!-- Preview dos anexos numa faixa ACIMA do compositor: o input continua
               disponível, então dá pra escrever uma legenda e enviar imagens +
               texto JUNTOS num envio só (o ✕ remove só aquele item; "Limpar
               todos" descarta tudo de uma vez). -->
          <div class="staged-preview" role="group" aria-label="Anexos prontos para enviar">
            <div class="staged-list">
              @for (it of pendingItems(); track it.key) {
                <div class="staged-item" [title]="it.file.name">
                  @if (it.url) {
                    <img class="staged-thumb" [src]="it.url" alt="" />
                  } @else {
                    <span class="staged-icon" aria-hidden="true">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                           stroke-linecap="round" stroke-linejoin="round">
                        <path d="m21.4 11.05-9.19 9.2a5 5 0 0 1-7.07-7.08l9.2-9.19a3.33 3.33 0 0 1 4.71 4.71l-9.2 9.2a1.67 1.67 0 0 1-2.36-2.36l8.49-8.49" />
                      </svg>
                    </span>
                  }
                  <span class="staged-meta">
                    <span class="staged-name">{{ it.file.name }}</span>
                    <span class="staged-size">{{ sizeKb(it.file) }} KB</span>
                  </span>
                  <button
                    type="button"
                    class="staged-remove"
                    aria-label="Remover anexo"
                    title="Remover este anexo"
                    [disabled]="attaching()"
                    (click)="removeFile(it.key)"
                  >
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"
                         stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                      <path d="M18 6 6 18M6 6l12 12" />
                    </svg>
                  </button>
                </div>
              }
            </div>
            @if (pendingItems().length > 1) {
              <button
                type="button"
                class="staged-clear"
                [disabled]="attaching()"
                (click)="cancelFile()"
              >
                Limpar todos ({{ pendingItems().length }})
              </button>
            }
          </div>
        }

        @if (actionHint(); as hint) {
          <!-- Feedback do "gap": algo enviado (texto/anexo/áudio), aguardando
               aparecer no terminal. -->
          <div class="transcribing" role="status" aria-live="polite">
            <span class="transcribing-spinner" aria-hidden="true"></span>
            <span>{{ hint }}</span>
          </div>
        }

        <div class="composer">
        <!-- Botões de ação: ocultam ao focar o input (mais espaço pra digitar) -->
        @if (!inputFocused()) {
          <!-- Só no celular (CSS): abre/fecha o menu "mais opções" suspenso
               acima do compositor. No desktop fica sempre escondido — os
               botões abaixo continuam inline como sempre. -->
          <button
            type="button"
            class="composer-more-toggle"
            [class.is-on]="composerMoreOpen()"
            (click)="toggleComposerMore()"
            [attr.aria-pressed]="composerMoreOpen()"
            aria-label="Mais opções (teclas, anexar, print, câmera)"
            title="Mais opções"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M12 5v14M5 12h14" />
            </svg>
          </button>
          @if (composerMoreOpen()) {
            <!-- Só existe (e cobre a tela) enquanto o menu tá aberto — no
                 desktop o botão acima nunca abre isso, então nunca renderiza. -->
            <div class="composer-more-backdrop" (click)="composerMoreOpen.set(false)"></div>
          }
          <div class="composer-more" [class.is-open]="composerMoreOpen()">
            <button
              type="button"
              class="live-toggle"
              [class.is-on]="keypadOpen()"
              (click)="toggleKeypad(); composerMoreOpen.set(false)"
              [attr.aria-pressed]="keypadOpen()"
              aria-label="Teclas de navegação (setas/Enter/Esc)"
              title="Mostrar/ocultar teclas de navegação"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <rect x="2" y="6" width="20" height="12" rx="2" />
                <path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10" />
              </svg>
            </button>
            <button
              type="button"
              class="live-toggle"
              [class.is-on]="liveMode()"
              (click)="toggleLive(); composerMoreOpen.set(false)"
              [attr.aria-pressed]="liveMode()"
              aria-label="Modo ao vivo (encaminha o que você digita pro terminal)"
              title="Modo ao vivo: mostra o autocomplete do CLI enquanto digita"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="m7 8-4 4 4 4M17 8l4 4-4 4M14 4l-4 16" />
              </svg>
            </button>
            <button
              type="button"
              class="attach"
              [class.is-busy]="attaching()"
              (click)="pickFile(); composerMoreOpen.set(false)"
              aria-label="Anexar arquivo ou imagem"
              title="Anexar arquivo/imagem para o agente"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="m21.4 11.05-9.19 9.2a5 5 0 0 1-7.07-7.08l9.2-9.19a3.33 3.33 0 0 1 4.71 4.71l-9.2 9.2a1.67 1.67 0 0 1-2.36-2.36l8.49-8.49" />
              </svg>
            </button>
            @if (canScreenshot) {
              <button
                type="button"
                class="attach"
                (click)="takeShot(); composerMoreOpen.set(false)"
                aria-label="Capturar área da tela e anexar"
                title="Recortar um pedaço da tela e anexar (não vai pra galeria)"
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2" />
                  <rect x="8.5" y="8.5" width="7" height="7" rx="1" />
                </svg>
              </button>
            }
            @if (canCamera) {
              <button
                type="button"
                class="attach"
                (click)="openCamera(); composerMoreOpen.set(false)"
                aria-label="Tirar foto com a câmera e anexar"
                title="Tirar foto com a câmera para dar contexto ao agente"
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" />
                  <circle cx="12" cy="13" r="4" />
                </svg>
              </button>
            }
          </div>
          @if (hostSupportsTranscription()) {
            <sf-audio-recorder
              class="mic"
              [sessionId]="id()"
              (transcribing)="onAudioTranscribing($event)"
              (uploaded)="onAudioUploaded()"
            ></sf-audio-recorder>
          }
        }

        <input
          #msgInput
          class="text-input mono"
          type="text"
          [placeholder]="inputPlaceholder()"
          autocomplete="off"
          [ngModel]="draft()"
          (ngModelChange)="onDraftChange($event)"
          (paste)="onPaste($event)"
          (keydown.enter)="send()"
          (focus)="inputFocused.set(true)"
          (blur)="inputFocused.set(false)"
          [disabled]="sending() || attaching()"
        />

        <button
          type="button"
          class="send"
          [class.is-busy]="attaching()"
          [disabled]="!canSend()"
          (click)="send()"
          aria-label="Enviar"
        >
          @if (attaching()) {
            <span class="staged-spinner" aria-hidden="true"></span>
          } @else {
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
          }
        </button>
        </div>
      </footer>

      <!-- Overlay de RECORTE do screenshot: arraste pra selecionar a área. A
           imagem fica só em memória (nunca vai pra galeria). -->
      @if (shotOpen()) {
        <div class="shot-overlay">
          <div class="shot-canvas">
            <img
              #shotImg
              class="shot-img"
              [src]="shotImgUrl()"
              draggable="false"
              (pointerdown)="shotDown($event)"
              (pointermove)="shotMove($event)"
              (pointerup)="shotUp()"
              alt="Captura da tela"
            />
            @if (shotSel(); as s) {
              <div
                class="shot-rect"
                [style.left.px]="s.x"
                [style.top.px]="s.y"
                [style.width.px]="s.w"
                [style.height.px]="s.h"
              ></div>
            }
          </div>
          <div class="shot-bar">
            <span class="shot-tip">Arraste para recortar, ou anexe tudo</span>
            <span class="shot-acts">
              <button type="button" class="shot-btn" (click)="cancelShot()">Cancelar</button>
              <button type="button" class="shot-btn" (click)="confirmShotFull()">Anexar tudo</button>
              <button type="button" class="shot-btn shot-btn--primary" [disabled]="!shotHasSel()" (click)="confirmShot()">
                Anexar recorte
              </button>
            </span>
          </div>
        </div>
      }

      <!-- Overlay da CÂMERA: preview ao vivo + "Tirar foto". A imagem fica só em
           memória (vira anexo staged) — nunca vai pra galeria do aparelho. -->
      @if (camOpen()) {
        <div class="shot-overlay">
          <div class="cam-stage">
            <video #camVideo class="cam-video" autoplay playsinline muted></video>
            @if (!camReady()) {
              <span class="cam-loading">abrindo câmera…</span>
            }
          </div>
          <div class="shot-bar">
            <span class="shot-tip">Enquadre e toque em “Tirar foto”</span>
            <span class="shot-acts">
              <button type="button" class="shot-btn" (click)="cancelCamera()">Cancelar</button>
              <button type="button" class="shot-btn" (click)="flipCamera()">Trocar câmera</button>
              <button type="button" class="shot-btn shot-btn--primary" [disabled]="!camReady()" (click)="capturePhoto()">
                Tirar foto
              </button>
            </span>
          </div>
        </div>
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
        background: #0e1113;
        color: #f4f5f7;
      }

      /* Modo split: esconde o corpo normal (term/tarefas/composer/etc.) e
         mostra só o header + o .split-view — evita reestruturar o template
         gigante abaixo; a troca é puramente visual via CSS. */
      .overlay.split-active > *:not(.hdr):not(.split-view):not(.split-picker) {
        display: none !important;
      }
      .split-view {
        display: flex;
        flex-direction: row;
        flex: 1;
        min-height: 0;
        background: #20262a;
      }
      .split-view.dragging {
        /* Evita que o texto/tela dos terminais seja "selecionado" durante o
           arrasto (o pointermove passa por cima dos painéis). */
        user-select: none;
      }
      .split-pane {
        display: flex;
        min-width: 0;
        min-height: 0;
        overflow: hidden;
      }
      .split-pane app-session-panel {
        flex: 1;
        min-width: 0;
        height: 100%;
      }
      /* Divisor arrastável entre os dois painéis (col-resize no desktop,
         row-resize quando empilha no celular). Área de toque maior que a
         linha visível pra ser fácil de agarrar no dedo. */
      .split-divider {
        flex: none;
        width: 14px;
        background: #14171a;
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: col-resize;
        touch-action: none;
      }
      .split-grip {
        width: 3px;
        height: 36px;
        border-radius: 2px;
        background: #3a4046;
      }
      .split-divider:hover .split-grip,
      .split-view.dragging .split-grip {
        background: #5b6470;
      }
      @media (max-width: 700px) {
        .split-view {
          flex-direction: column;
        }
        .split-divider {
          width: auto;
          height: 14px;
          cursor: row-resize;
        }
        .split-grip {
          width: 36px;
          height: 3px;
        }
      }

      .split-picker {
        flex: none;
        border-bottom: 1px solid #20262a;
        max-height: 40vh;
        overflow: auto;
      }
      .split-picker-hdr {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 10px 16px;
        font-size: 13px;
        color: #9aa0a6;
      }
      .split-picker-hdr .close-btn {
        background: none;
        border: none;
        color: #9aa0a6;
        cursor: pointer;
        font-size: 14px;
      }
      .split-picker-search {
        display: block;
        width: calc(100% - 32px);
        margin: 0 16px 8px;
        padding: 8px 10px;
        background: rgba(255, 255, 255, 0.06);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 8px;
        color: #f4f5f7;
        font-size: 14px;
      }
      .split-picker-search::placeholder {
        color: #6b7280;
      }
      .split-picker-empty {
        padding: 12px 16px;
        color: #6b7280;
        font-size: 13px;
      }
      .split-picker-item {
        display: flex;
        align-items: center;
        gap: 8px;
        width: 100%;
        padding: 10px 16px;
        background: none;
        border: none;
        border-top: 1px solid #16191b;
        color: #f4f5f7;
        font-size: 14px;
        text-align: left;
        cursor: pointer;
      }
      .split-picker-item:hover {
        background: #16191b;
      }
      .split-picker-item .dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        flex: none;
      }
      .split-picker-item .name {
        flex: 1;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .split-picker-item .status {
        font-size: 11px;
        color: #9aa0a6;
      }
      .mono {
        /* Fontes de SÍMBOLOS no fim da pilha: quando a mono não tem o glyph
           (ex.: ▶▶ do "bypass permissions" do Claude Code), o navegador
           substitui só aquele caractere em vez de mostrar tofu (▯). */
        font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, 'SF Mono',
          Menlo, Consolas, 'Noto Sans Symbols', 'Noto Sans Symbols 2',
          'Segoe UI Symbol', 'Apple Color Emoji', 'Noto Color Emoji', monospace;
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
        flex-wrap: wrap;
        row-gap: 10px;
      }
      /* Grupo status + botões + badge: à direita na mesma linha (tela larga);
         quebra pra baixo como bloco em tela estreita. */
      .hdr-controls {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
        margin-left: auto;
      }
      /* Mobile: com o status na LINHA DO TÍTULO, a linha de baixo é só dos
         botões — uma linha única, à direita, sem quebra nem corte. */
      @media (max-width: 700px) {
        .hdr-controls {
          flex-basis: 100%;
          margin-left: 0;
          flex-wrap: nowrap;
        }
        .status-actions {
          flex: none;
          margin-left: auto;
          gap: 6px;
        }
        .act {
          padding: 6px 8px;
        }
        /* CC (tipo do agente) escondido no celular — o espaço é dos botões. */
        .hdr-controls .agent-badge {
          display: none;
        }
        /* Pill compacta na linha do título (trunca o rótulo se apertar). */
        .hdr-title .status-pill {
          padding: 3px 8px;
          font-size: 11px;
        }
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
        flex: 1 1 200px;
        min-width: 0;
      }
      .hdr-title {
        display: flex;
        align-items: center;
        gap: 8px;
        min-width: 0;
        font-size: 17px;
        font-weight: 700;
        color: #f4f5f7;
      }
      .hdr-name {
        min-width: 0;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .hdr-rename {
        flex: none;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 4px;
        background: transparent;
        border: 1px solid #283230;
        border-radius: 8px;
        color: #8a90a0;
        cursor: pointer;
      }
      .hdr-rename:hover {
        color: #e7eae9;
        border-color: #37464f;
      }
      /* Painel Renomear */
      .rename {
        flex: none;
        display: flex;
        flex-direction: column;
        gap: 12px;
        padding: 14px 16px;
        border-bottom: 1px solid #20262a;
        background: #121614;
      }
      .rename-field {
        display: flex;
        flex-direction: column;
        gap: 5px;
      }
      .rename-lbl {
        font-size: 12px;
        font-weight: 700;
        color: #c9cdd6;
      }
      .rename-input {
        appearance: none;
        background: #0e1113;
        border: 1px solid #283230;
        border-radius: 10px;
        color: #f4f5f7;
        font-size: 14px;
        padding: 9px 11px;
        outline: none;
      }
      .rename-input:focus {
        border-color: #2cecc4;
      }
      .rename-hint {
        font-size: 11px;
        color: #7a8090;
      }
      .rename-acts {
        display: flex;
        justify-content: flex-end;
        gap: 8px;
      }
      .rename-btn {
        appearance: none;
        background: transparent;
        border: 1px solid #283230;
        border-radius: 10px;
        color: #c9cdd6;
        font-size: 13px;
        font-weight: 600;
        padding: 8px 16px;
        cursor: pointer;
      }
      .rename-btn--primary {
        background: linear-gradient(150deg, #2cecc4, #00a482);
        border-color: transparent;
        color: #06231d;
      }
      .rename-btn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }
      .rename-btn--danger {
        border-color: #4a2626;
        color: #f87171;
      }

      /* Comandos programados */
      .sched-list {
        max-height: 220px;
        overflow-y: auto;
        margin-bottom: 4px;
      }
      .sched-item {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        padding: 10px 0;
        border-bottom: 1px solid #20262a;
      }
      .sched-item--paused {
        opacity: 0.55;
      }
      .sched-item-main {
        display: flex;
        flex-direction: column;
        gap: 3px;
        min-width: 0;
      }
      .shared-file-main {
        display: flex;
        flex-direction: column;
        gap: 3px;
        min-width: 0;
        text-decoration: none;
      }
      .shared-file-main:hover .sched-text {
        text-decoration: underline;
      }
      .sched-text {
        font-size: 13.5px;
        color: #f4f5f7;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .sched-meta {
        font-size: 11.5px;
        color: #7d8590;
      }
      .sched-error {
        color: #f87171;
      }
      .sched-item-acts {
        display: flex;
        align-items: center;
        gap: 10px;
        flex: none;
      }
      .sched-toggle {
        appearance: none;
        width: 38px;
        height: 22px;
        border-radius: 999px;
        border: none;
        background: #3a4046;
        padding: 2px;
        cursor: pointer;
        flex: none;
      }
      .sched-toggle.on {
        background: linear-gradient(150deg, #2cecc4, #00a482);
      }
      .sched-toggle:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }
      .sched-toggle-knob {
        display: block;
        width: 18px;
        height: 18px;
        border-radius: 50%;
        background: #fff;
        transition: transform 0.15s ease;
      }
      .sched-toggle.on .sched-toggle-knob {
        transform: translateX(16px);
      }
      .sched-interval-row {
        display: flex;
        gap: 8px;
      }
      .sched-interval-num {
        width: 80px;
        flex: none;
      }
      .sched-interval-unit {
        flex: 1;
      }
      /* Trocar provedor (dentro do bloco de métricas) */
      .switch-row {
        display: flex;
        justify-content: flex-end;
        margin-top: 10px;
      }
      .switch-btn {
        appearance: none;
        background: transparent;
        border: 1px solid #283230;
        border-radius: 10px;
        color: #7a8090;
        font-size: 12px;
        font-weight: 600;
        padding: 6px 12px;
        cursor: pointer;
      }
      .switch-btn:hover {
        color: #c9cdd6;
        border-color: #37464f;
      }
      .switch-btn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }
      /* Badge do agente clicável (dono): abre o painel de trocar provedor. */
      .agent-badge--btn {
        cursor: pointer;
        user-select: none;
      }
      /* Overlay de recorte do screenshot */
      .shot-overlay {
        position: fixed;
        inset: 0;
        z-index: 50;
        display: flex;
        flex-direction: column;
        background: rgba(6, 8, 9, 0.92);
      }
      .shot-canvas {
        position: relative;
        flex: 1;
        min-height: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 12px;
        overflow: hidden;
      }
      .shot-img {
        max-width: 100%;
        max-height: 100%;
        object-fit: contain;
        user-select: none;
        touch-action: none;
        cursor: crosshair;
        border: 1px solid #263038;
      }
      .shot-rect {
        position: absolute;
        border: 2px solid #2cecc4;
        background: rgba(44, 236, 196, 0.14);
        pointer-events: none;
      }
      .shot-bar {
        flex: none;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        padding: 12px 16px calc(12px + env(safe-area-inset-bottom, 0px));
        background: #0e1113;
        border-top: 1px solid #20262a;
      }
      .shot-tip {
        font-size: 12.5px;
        color: #9fb0ad;
      }
      .shot-acts {
        display: flex;
        gap: 8px;
      }
      .shot-btn {
        appearance: none;
        background: transparent;
        border: 1px solid #283230;
        border-radius: 10px;
        color: #c9cdd6;
        font-size: 13px;
        font-weight: 600;
        padding: 8px 16px;
        cursor: pointer;
      }
      .shot-btn--primary {
        background: linear-gradient(150deg, #2cecc4, #00a482);
        border-color: transparent;
        color: #06231d;
      }
      .shot-btn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }
      /* Palco do preview da câmera (mesmo overlay do screenshot) */
      .cam-stage {
        position: relative;
        flex: 1;
        min-height: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 12px;
        overflow: hidden;
      }
      .cam-video {
        max-width: 100%;
        max-height: 100%;
        object-fit: contain;
        background: #000;
        border: 1px solid #263038;
        border-radius: 8px;
      }
      .cam-loading {
        position: absolute;
        font-size: 13px;
        color: #9fb0ad;
      }
      .hdr-dir {
        font-size: 12px;
        color: #7a8090;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .hdr-task {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        max-width: 100%;
        margin-top: 6px;
        padding: 3px 9px;
        border-radius: 999px;
        background: #1b2a24;
        color: #34d399;
        font-size: 12px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .hdr-task svg {
        flex: 0 0 auto;
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
        flex-wrap: wrap;
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
        /* Vive na linha do TÍTULO: encolhe/trunca antes de empurrar o nome. */
        flex: 0 1 auto;
        min-width: 0;
        overflow: hidden;
      }
      .status-label {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        min-width: 0;
      }
      .status-dot {
        flex: none;
        width: 7px;
        height: 7px;
        border-radius: 50%;
      }
      /* Host desta sessão (multi-host, AD-011) — mesma pegada visual da
         status-pill, cor neutra pra não competir com o status. */
      .host-pill {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        font-size: 12.5px;
        font-weight: 600;
        padding: 5px 10px;
        border-radius: 9px;
        white-space: nowrap;
        flex: 0 1 auto;
        min-width: 0;
        overflow: hidden;
        color: var(--text-muted);
        background: #ffffff0d;
      }
      .host-label {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        min-width: 0;
      }
      @media (max-width: 700px) {
        .hdr-title .host-pill .host-label {
          display: none;
        }
      }
      .status-actions {
        margin-left: auto;
        display: flex;
        gap: 8px;
      }
      /* Badge "Compartilhado" no lugar do voltar (modo convidado). */
      .guest-badge {
        flex: none;
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 5px 9px;
        border-radius: 999px;
        border: 1px solid #283230;
        background: #14201c;
        color: #00e4b4;
        font-size: 11.5px;
        font-weight: 700;
      }
      /* Painel "Compartilhar" — faixa abaixo do header, estilo das outras. */
      .share {
        flex: none;
        padding: 12px 16px;
        border-bottom: 1px solid #20262a;
        background: #121614;
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .share-row {
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .share-url {
        flex: 1;
        min-width: 0;
        padding: 8px 10px;
        border-radius: 9px;
        border: 1px solid #283230;
        background: #0e1113;
        color: #e7eae9;
        font-size: 12px;
      }
      .share-btn {
        flex: none;
        padding: 8px 12px;
        border-radius: 9px;
        border: 1px solid #283230;
        background: #181c1b;
        color: #c9cdd6;
        font-size: 12.5px;
        font-weight: 700;
        cursor: pointer;
        -webkit-tap-highlight-color: transparent;
      }
      .share-btn--primary {
        background: #00e4b4;
        border-color: transparent;
        color: #06231d;
      }
      .share-btn:disabled {
        opacity: 0.6;
        cursor: default;
      }
      .share-foot {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        flex-wrap: wrap;
      }
      .share-hint {
        font-size: 11.5px;
        color: #7a8090;
        line-height: 1.35;
      }
      .share-acts {
        flex: none;
        display: flex;
        gap: 12px;
      }
      .share-link-btn {
        background: none;
        border: none;
        padding: 0;
        color: #9aa0ae;
        font-size: 12px;
        font-weight: 600;
        cursor: pointer;
        -webkit-tap-highlight-color: transparent;
      }
      .share-link-btn--danger {
        color: #f87171;
      }
      .share-link-btn:disabled {
        opacity: 0.6;
        cursor: default;
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
      .act-badge {
        display: inline-block;
        margin-left: 4px;
        padding: 0 5px;
        border-radius: 999px;
        background: #2cecc4;
        color: #06231d;
        font-size: 10px;
        font-weight: 700;
        line-height: 15px;
        vertical-align: middle;
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
      .act--trash {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 6px 9px;
        color: #f87171;
        border-color: #3a2326;
      }
      .act--trash:hover:not(:disabled) {
        background: #2a1c1c;
      }
      .act--jarvis {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 6px 9px;
        color: #5a6072;
      }
      .act--jarvis.on {
        color: #38bdf8;
        border-color: #1e3a44;
        background: #0e2730;
      }
      /* Estado ATIVO dos botões ghost (foco ligado, painel de compartilhar
         aberto): destaca em verde-água p/ ficar claro que já foi acionado —
         clicar de novo desliga (o ícone também alterna expandir/recolher). */
      .act--ghost.on {
        color: #34d399;
        border-color: #1f3d33;
        background: #0f2620;
      }

      /* Bloco de métricas */
      .metrics {
        flex: none;
        padding: 13px 16px 14px;
        border-bottom: 1px solid #20262a;
        background: #121614;
      }
      /* Modo foco: esconde Modelo e Tarefas → terminal ocupa o espaço. */
      .overlay.focus .metrics,
      .overlay.focus .tasks {
        display: none;
      }
      .metrics-top {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
      }
      .metrics-top--toggle {
        cursor: pointer;
        -webkit-tap-highlight-color: transparent;
      }
      .metrics-chev {
        flex: none;
        transition: transform 0.2s;
      }
      .metrics-chev.open {
        transform: rotate(180deg);
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
        margin-left: auto; /* joga o "Contexto usado" pra direita (junto do chevron) */
        margin-right: 6px;
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
      /* Custo estimado (preço de API) por modelo */
      .cost-card {
        margin-top: 10px;
        background: #181c1b;
        border: 1px solid #283230;
        border-radius: 12px;
        padding: 10px 12px;
      }
      .cost-head {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 10px;
      }
      .cost-total {
        font-size: 15px;
        font-weight: 800;
        color: #f4f5f7;
      }
      .cost-brl {
        font-weight: 700;
        color: #9fb0ad;
      }
      .cost-rows {
        margin-top: 7px;
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .cost-row {
        display: flex;
        align-items: baseline;
        gap: 8px;
        min-width: 0;
      }
      .cost-model {
        font-size: 12px;
        font-weight: 700;
        color: #c9cdd6;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .cost-toks {
        font-size: 10.5px;
        color: #6b7180;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        flex: 1;
        min-width: 0;
      }
      .cost-usd {
        font-size: 12px;
        font-weight: 700;
        color: #9aa0ac;
        white-space: nowrap;
        margin-left: auto;
      }
      .cost-note {
        margin-top: 7px;
        font-size: 10px;
        color: #6b7180;
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
      /* Faixa de largura cheia, consistente com a caixa de métricas (.metrics)
         logo acima — antes era um card recuado, que destoava. */
      .tasks {
        flex: none;
        border-bottom: 1px solid #20262a;
        background: #121614;
      }
      .tasks-head {
        width: 100%;
        display: flex;
        flex-direction: column;
        gap: 6px;
        padding: 13px 16px;
        background: none;
        border: none;
        color: #f4f5f7;
        cursor: pointer;
        text-align: left;
      }
      .tasks-head-top {
        display: flex;
        align-items: center;
        gap: 10px;
      }
      /* Linha de destaque: tarefa em andamento (ou a mais recente) — preenche
         a barra que antes ficava vazia. */
      .tasks-current {
        display: flex;
        align-items: center;
        gap: 7px;
        min-width: 0;
        font-size: 12px;
      }
      .tasks-current-dot {
        flex: none;
        width: 8px;
        height: 8px;
        border-radius: 50%;
      }
      .tasks-current-lead {
        flex: none;
        font-weight: 700;
      }
      .tasks-current-title {
        flex: 1;
        min-width: 0;
        color: #c7ccd6;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
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
        padding: 0 16px 8px;
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
        padding: 0 16px 10px;
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
      .tasks-more {
        list-style: none;
        padding: 8px 0 2px;
        text-align: center;
      }
      .tasks-more-btn {
        background: transparent;
        border: 1px solid #263038;
        color: #9fb0ad;
        font-size: 12px;
        font-weight: 600;
        padding: 6px 14px;
        border-radius: 999px;
        cursor: pointer;
      }
      .tasks-more-btn:hover {
        color: #e7eae9;
        border-color: #37464f;
      }
      /* Barra do terminal: toggle ao vivo/histórico */
      .term-bar {
        flex: none;
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 8px;
        padding: 8px 16px 0;
        background: #0b0e0f;
      }
      .term-scroll {
        display: inline-flex;
        gap: 4px;
      }
      .term-scroll-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 34px;
        height: 28px;
        border-radius: 8px;
        border: 1px solid #283230;
        background: #12181a;
        color: #9aa0ae;
        cursor: pointer;
        -webkit-tap-highlight-color: transparent;
        touch-action: manipulation;
      }
      .term-scroll-btn:active {
        background: #1b2426;
        color: #e7eae9;
      }
      .term-font-btn {
        width: 32px;
        font-size: 12px;
        font-weight: 700;
        font-family: inherit;
      }
      /* Botão "⧉ artifact": abre o último artifact visto na sessão. */
      .term-artifact {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 4px 10px;
        border-radius: 999px;
        border: 1px solid #1e3a44;
        background: #0e2730;
        color: #38bdf8;
        font-size: 11.5px;
        font-weight: 700;
        text-decoration: none;
        white-space: nowrap;
      }
      .term-artifact:hover {
        color: #7dd3fc;
        border-color: #2b5261;
      }
      .term-artifact-group {
        display: inline-flex;
        align-items: center;
        gap: 3px;
      }
      .term-artifact-more {
        font-family: inherit;
        cursor: pointer;
        padding: 4px 8px;
      }
      /* Menu do histórico de artifacts */
      .artifact-menu {
        flex: none;
        display: flex;
        flex-direction: column;
        gap: 4px;
        padding: 8px 16px;
        background: #10191c;
        border-bottom: 1px solid #20262a;
      }
      .artifact-menu-item {
        color: #38bdf8;
        font-size: 12.5px;
        font-weight: 600;
        text-decoration: none;
        padding: 5px 8px;
        border-radius: 8px;
      }
      .artifact-menu-item:hover {
        background: #0e2730;
        color: #7dd3fc;
      }
      .term {
        position: relative;
        flex: 1;
        min-height: 0;
        overflow-y: auto;
        background: #0b0e0f;
        padding: 14px 16px;
        font-size: 12.5px;
        line-height: 1.7;
      }
      /* Toast "Copiado" — canto inferior ESQUERDO (não colide com a live-pill). */
      .buf-loading {
        position: sticky;
        top: 6px;
        z-index: 6;
        display: block;
        width: fit-content;
        margin: 0 auto;
        padding: 4px 12px;
        border-radius: 999px;
        background: rgba(20, 26, 24, 0.92);
        border: 1px solid #263038;
        color: #9fb0ad;
        font-size: 11.5px;
        font-weight: 600;
        font-family: inherit;
        pointer-events: none;
      }
      .term-copied {
        position: sticky;
        bottom: 12px;
        float: left;
        margin-left: 2px;
        z-index: 5;
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 5px 11px;
        border-radius: 999px;
        background: linear-gradient(150deg, #2cecc4, #00a482);
        color: #06231d;
        font-size: 11.5px;
        font-weight: 700;
        font-family: inherit;
        box-shadow: 0 4px 14px rgba(0, 0, 0, 0.4);
        pointer-events: none;
        animation: term-copied-in 0.15s ease-out;
      }
      .term-copied svg {
        flex: none;
      }
      @keyframes term-copied-in {
        from {
          opacity: 0;
          transform: translateY(6px);
        }
        to {
          opacity: 1;
          transform: none;
        }
      }
      .live-pill {
        position: sticky;
        bottom: 12px;
        float: right;
        margin-right: 2px;
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 5px 11px;
        border-radius: 999px;
        border: 1px solid transparent;
        background: linear-gradient(150deg, #2cecc4, #00a482);
        color: #06231d;
        font-size: 11.5px;
        font-weight: 700;
        font-family: inherit;
        cursor: pointer;
        box-shadow: 0 4px 14px rgba(0, 0, 0, 0.4);
        -webkit-tap-highlight-color: transparent;
      }
      .live-pill svg {
        flex: none;
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
      /* URLs clicáveis dentro do espelho do terminal */
      .term-screen .term-link {
        color: #60a5fa;
        text-decoration: underline;
        text-underline-offset: 2px;
        cursor: pointer;
      }
      .term-screen .term-link:hover {
        color: #93c5fd;
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
      .kp-anim {
        animation: kp-slide 0.18s ease-out;
      }
      @keyframes kp-slide {
        from {
          opacity: 0;
          transform: translateY(10px);
        }
        to {
          opacity: 1;
          transform: translateY(0);
        }
      }
      @media (prefers-reduced-motion: reduce) {
        .kp-anim {
          animation: none;
        }
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
        position: relative;
        flex: none;
        display: flex;
        flex-direction: column;
        align-items: stretch;
        gap: 8px;
        padding: 12px 14px calc(16px + env(safe-area-inset-bottom, 0px));
        border-top: 1px solid #20262a;
        background: #0e1113;
        transition: background 0.15s, box-shadow 0.15s;
      }
      /* Linha do compositor: botões + input + enviar (a barra em si é coluna,
         pra acomodar o preview do anexo acima). */
      .composer {
        display: flex;
        align-items: center;
        gap: 10px;
      }
      /* Aviso "transcrevendo áudio…" (linha acima do compositor). */
      .transcribing {
        display: flex;
        align-items: center;
        gap: 9px;
        padding: 8px 12px;
        border: 1px solid #1f3a33;
        border-radius: 10px;
        background: #0f1a17;
        color: #00e4b4;
        font-size: 13px;
        font-weight: 600;
      }
      .transcribing-spinner {
        flex: none;
        width: 15px;
        height: 15px;
        border-radius: 50%;
        border: 2px solid rgba(0, 228, 180, 0.3);
        border-top-color: #00e4b4;
        animation: transcribe-spin 0.7s linear infinite;
      }
      @keyframes transcribe-spin {
        to {
          transform: rotate(360deg);
        }
      }
      @media (prefers-reduced-motion: reduce) {
        .transcribing-spinner {
          animation: none;
        }
      }
      /* Preview dos anexos staged (faixa acima do input). */
      .staged-preview {
        display: flex;
        flex-direction: column;
        gap: 6px;
        padding: 6px 8px;
        border: 1px solid #283230;
        border-radius: 10px;
        background: #12181a;
      }
      /* Grade de itens staged: quebra linha e rola se passar da altura. */
      .staged-list {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        max-height: 132px;
        overflow-y: auto;
      }
      .staged-item {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 4px 6px;
        border: 1px solid #283230;
        border-radius: 10px;
        background: #181c1b;
        max-width: 100%;
      }
      .staged-item .staged-meta {
        flex: 0 1 auto;
        max-width: 150px;
      }
      .staged-clear {
        align-self: flex-end;
        background: none;
        border: none;
        padding: 2px 4px;
        color: #9aa0ae;
        font-size: 12px;
        font-weight: 600;
        cursor: pointer;
        -webkit-tap-highlight-color: transparent;
      }
      .staged-clear:hover:not(:disabled) {
        color: #f87171;
      }
      .staged-clear:disabled {
        opacity: 0.5;
        cursor: progress;
      }
      .staged-remove {
        flex: none;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 30px;
        height: 30px;
        border: none;
        border-radius: 8px;
        background: #20262a;
        color: #c7ccd6;
        cursor: pointer;
        -webkit-tap-highlight-color: transparent;
      }
      .staged-remove:disabled {
        opacity: 0.5;
      }
      .staged-remove svg {
        width: 15px;
        height: 15px;
      }
      .inputbar.drag-over {
        background: #0f1a17;
        box-shadow: inset 0 0 0 2px #00e4b4;
      }
      /* Pista visual cobrindo o compositor enquanto arrasta. */
      .drop-hint {
        position: absolute;
        inset: 0;
        z-index: 3;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
        background: rgba(8, 20, 17, 0.82);
        color: #00e4b4;
        font-size: 13.5px;
        font-weight: 700;
        pointer-events: none;
      }
      /* Esconde o <input type=file> sem display:none (Android bloqueia o
         .click() programático quando o input está hidden/display:none). */
      .visually-hidden {
        position: absolute;
        width: 1px;
        height: 1px;
        padding: 0;
        margin: -1px;
        overflow: hidden;
        clip: rect(0, 0, 0, 0);
        white-space: nowrap;
        border: 0;
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
      .attach {
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
      }
      .attach.is-busy {
        opacity: 0.5;
        cursor: progress;
      }
      .live-toggle.is-on {
        color: #06231d;
        background: linear-gradient(150deg, #2cecc4, #00a482);
        border-color: transparent;
      }
      .mic {
        flex: none;
      }
      /* Menu "mais opções" (teclas/ao vivo/anexar/print/câmera): no desktop
         é invisível — os botões vivem inline no compositor, como sempre.
         Vira um popup só no celular (media query abaixo). */
      .composer-more {
        display: contents;
      }
      .composer-more-toggle {
        display: none;
      }
      .composer-more-backdrop {
        display: none;
      }
      /* Celular: some espaço pro input (a queixa era "espaço de escrever
         pequeno") tirando os botões menos usados do compositor e agrupando
         num menu suspenso pra cima, acionado por um botão "+". Mic fica à
         esquerda do input, enviar à direita — só nessa largura; acima disso
         o layout completo de sempre. */
      @media (max-width: 700px) {
        .composer {
          position: relative;
        }
        .composer-more-toggle {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          order: 1;
          flex: none;
          width: 40px;
          height: 40px;
          border-radius: 12px;
          border: 1px solid #283230;
          background: #181c1b;
          color: #8a90a0;
          cursor: pointer;
          touch-action: manipulation;
          -webkit-tap-highlight-color: transparent;
        }
        .composer-more-toggle.is-on {
          color: #06231d;
          background: linear-gradient(150deg, #2cecc4, #00a482);
          border-color: transparent;
        }
        .composer-more-backdrop {
          display: block;
          position: fixed;
          inset: 0;
          z-index: 4;
          background: transparent;
        }
        .composer-more {
          display: none;
          order: 5;
          position: absolute;
          bottom: calc(100% + 8px);
          left: 0;
          z-index: 5;
          flex-wrap: wrap;
          gap: 8px;
          padding: 10px;
          max-width: calc(100% - 20px);
          border: 1px solid #283230;
          border-radius: 14px;
          background: #12181a;
          box-shadow: 0 10px 28px rgba(0, 0, 0, 0.45);
        }
        .composer-more.is-open {
          display: flex;
        }
        .mic {
          order: 2;
        }
        .text-input {
          order: 3;
          height: 48px;
          font-size: 15px;
        }
        .send {
          order: 4;
        }
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
      /* Barra de anexo "staged" — ocupa a linha inteira da inputbar e
         espelha o layout [cancelar][conteúdo][enviar] do recorder de áudio. */
      .staged {
        flex: 1;
        min-width: 0;
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 6px 8px;
        border: 1px solid #283230;
        background: #14191a;
        border-radius: 14px;
      }
      .staged-info {
        flex: 1;
        min-width: 0;
        display: flex;
        align-items: center;
        gap: 10px;
      }
      .staged-thumb {
        flex: none;
        width: 40px;
        height: 40px;
        object-fit: cover;
        border-radius: 10px;
        border: 1px solid #283230;
        display: block;
      }
      .staged-icon {
        flex: none;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 40px;
        height: 40px;
        border-radius: 10px;
        background: #181c1b;
        border: 1px solid #283230;
        color: #8a90a0;
      }
      .staged-icon svg {
        width: 20px;
        height: 20px;
      }
      .staged-meta {
        flex: 1;
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: 2px;
      }
      .staged-name {
        color: #f4f5f7;
        font-size: 14px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .staged-size {
        color: #6b7180;
        font-size: 12px;
      }
      .staged-cancel {
        flex: none;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 40px;
        height: 40px;
        border: none;
        border-radius: var(--radius-full, 999px);
        background: #2a1c1c;
        color: #f87171;
        cursor: pointer;
        touch-action: manipulation;
        -webkit-tap-highlight-color: transparent;
      }
      .staged-cancel:disabled {
        opacity: 0.5;
        cursor: progress;
      }
      .staged-cancel svg {
        width: 16px;
        height: 16px;
      }
      .staged-send {
        flex: none;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 40px;
        height: 40px;
        border: none;
        border-radius: var(--radius-full, 999px);
        background: linear-gradient(150deg, #34d399, #00a482);
        color: #04140f;
        cursor: pointer;
        padding: 0;
        touch-action: manipulation;
        -webkit-tap-highlight-color: transparent;
        transition: opacity 0.15s;
      }
      .staged-send:disabled,
      .staged-send.is-busy {
        opacity: 0.7;
        cursor: progress;
      }
      .staged-send svg {
        width: 18px;
        height: 18px;
      }
      .staged-spinner {
        width: 16px;
        height: 16px;
        border-radius: 50%;
        border: 2px solid rgba(4, 20, 15, 0.35);
        border-top-color: #04140f;
        animation: staged-spin 0.7s linear infinite;
      }
      @keyframes staged-spin {
        to {
          transform: rotate(360deg);
        }
      }
      @media (prefers-reduced-motion: reduce) {
        .staged-spinner {
          animation: none;
        }
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
  private readonly drafts = inject(DraftStore);
  private readonly prefs = inject(SessionPrefsStore);
  private readonly workers = inject(WorkersStore);
  private readonly sanitizer = inject(DomSanitizer);
  private readonly location = inject(Location);
  private readonly shareSvc = inject(ShareSessionService);

  /**
   * MODO CONVIDADO: a sessão foi aberta via link compartilhável (`/s/:id`).
   * Esconde a navegação (voltar/compartilhar) — o convidado só vê/controla
   * ESTA sessão. O escopo real é garantido no backend (token só vale aqui).
   */
  protected readonly guest = signal<boolean>(
    this.route.snapshot.data['guest'] === true,
  );

  // --- Estado do painel "Compartilhar" (só dono) ---
  protected readonly shareOpen = signal<boolean>(false);
  protected readonly shareLink = signal<ShareLink | null>(null);
  protected readonly shareBusy = signal<boolean>(false);
  protected readonly shareCopied = signal<boolean>(false);

  /** Menu "mais opções" do compositor — só existe visualmente no celular
   * (CSS); no desktop os botões continuam sempre inline, sem esconder nada. */
  protected readonly composerMoreOpen = signal<boolean>(false);
  protected toggleComposerMore(): void {
    this.composerMoreOpen.update((v) => !v);
  }

  private readonly termEl = viewChild<ElementRef<HTMLDivElement>>('term');
  private readonly fileInput = viewChild<ElementRef<HTMLInputElement>>('fileInput');
  private readonly msgInput = viewChild<ElementRef<HTMLInputElement>>('msgInput');
  private readonly shotImg = viewChild<ElementRef<HTMLImageElement>>('shotImg');
  private readonly camVideo = viewChild<ElementRef<HTMLVideoElement>>('camVideo');

  /** Session id from the route (`sessao/:id`). */
  protected readonly id = signal<string>(
    this.route.snapshot.paramMap.get('id') ?? '',
  );
  /** Tarefa em foco vinda do clique no Início (query param). */
  protected readonly activeTask = signal<string>(
    this.route.snapshot.queryParamMap.get('task') ?? '',
  );

  /**
   * Modo "dividir tela": mostra esta sessão + outra lado a lado (query param
   * `compare`), cada lado num painel leve (`SessionPanelComponent`) — pensado
   * pra acompanhar/conversar entre duas sessões (ver skill `sf-delegate`,
   * `sf send`). Não some do estado normal por baixo: sair do split volta pra
   * cá exatamente onde estava.
   */
  protected readonly compareId = signal<string | null>(
    this.route.snapshot.queryParamMap.get('compare') || null,
  );
  protected readonly splitPickerOpen = signal<boolean>(false);
  protected readonly splitCandidates = signal<Session[]>([]);
  protected readonly splitSearch = signal<string>('');
  /** Candidatos do picker: filtrados pela busca, ativos primeiro, depois nome. */
  protected readonly splitCandidatesFiltered = computed(() => {
    const q = this.splitSearch().trim().toLowerCase();
    const nameOf = (s: Session) => (s.display_name || s.tmux_name || '').toLowerCase();
    const isActive = (s: Session) =>
      s.status === 'running' || s.status === 'waiting_input';
    return this.splitCandidates()
      .filter((s) => !q || nameOf(s).includes(q))
      .sort((a, b) => {
        const activeDiff = Number(isActive(b)) - Number(isActive(a));
        return activeDiff !== 0 ? activeDiff : nameOf(a).localeCompare(nameOf(b));
      });
  });

  // --- Comandos programados (instrução recorrente pro terminal) ---
  protected readonly schedulesOpen = signal<boolean>(false);
  protected readonly schedules = signal<Schedule[]>([]);
  protected readonly schedulesCreating = signal<boolean>(false);
  /** id do schedule com uma ação (pausar/excluir) em andamento, ou null. */
  protected readonly scheduleBusy = signal<string | null>(null);
  protected readonly newScheduleText = signal<string>('');
  protected readonly newScheduleIntervalValue = signal<number>(1);
  /** Segundos de 1 unidade: '60' (minuto) | '3600' (hora) | '86400' (dia). */
  protected readonly newScheduleIntervalUnit = signal<string>('3600');

  protected toggleSchedules(): void {
    if (this.schedulesOpen()) {
      this.closeSchedules();
      return;
    }
    this.schedulesOpen.set(true);
    this.loadSchedules();
  }

  protected closeSchedules(): void {
    this.schedulesOpen.set(false);
  }

  private loadSchedules(): void {
    const id = this.id();
    if (!id) {
      return;
    }
    this.api
      .listSchedules(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (list) => this.schedules.set(list),
        error: () => this.schedules.set([]),
      });
  }

  protected createSchedule(): void {
    const id = this.id();
    const text = this.newScheduleText().trim();
    const seconds = this.newScheduleIntervalValue() * Number(this.newScheduleIntervalUnit());
    if (!id || !text || !(seconds > 0)) {
      return;
    }
    this.schedulesCreating.set(true);
    this.api
      .createSchedule(id, text, seconds)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (s) => {
          this.schedules.update((list) => [...list, s]);
          this.newScheduleText.set('');
          this.schedulesCreating.set(false);
        },
        error: () => this.schedulesCreating.set(false),
      });
  }

  protected toggleScheduleEnabled(s: Schedule): void {
    this.scheduleBusy.set(s.id);
    this.api
      .patchSchedule(s.id, { enabled: !s.enabled })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (updated) => {
          this.schedules.update((list) =>
            list.map((x) => (x.id === updated.id ? updated : x)),
          );
          this.scheduleBusy.set(null);
        },
        error: () => this.scheduleBusy.set(null),
      });
  }

  protected deleteSchedule(s: Schedule): void {
    this.scheduleBusy.set(s.id);
    this.api
      .deleteSchedule(s.id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.schedules.update((list) => list.filter((x) => x.id !== s.id));
          this.scheduleBusy.set(null);
        },
        error: () => this.scheduleBusy.set(null),
      });
  }

  /** "3600" → "1 hora"; pluraliza e escolhe a maior unidade exata (d/h/min). */
  protected formatInterval(seconds: number): string {
    const units: [number, string, string][] = [
      [86400, 'dia', 'dias'],
      [3600, 'hora', 'horas'],
      [60, 'minuto', 'minutos'],
    ];
    for (const [unitSeconds, singular, plural] of units) {
      if (seconds % unitSeconds === 0) {
        const n = seconds / unitSeconds;
        return `${n} ${n === 1 ? singular : plural}`;
      }
    }
    return `${seconds}s`;
  }

  /** ISO futuro → "12min"/"3h" (aproximado, pro item da lista). */
  protected formatRelative(isoDate: string): string {
    const diffMs = new Date(isoDate).getTime() - Date.now();
    if (diffMs <= 0) {
      return 'agora';
    }
    const mins = Math.round(diffMs / 60000);
    if (mins < 60) {
      return `${mins}min`;
    }
    const hours = Math.round(mins / 60);
    return hours < 24 ? `${hours}h` : `${Math.round(hours / 24)}d`;
  }

  // --- Arquivos compartilhados pelo agente (ver `tools/sf share`) ---
  protected readonly sharedFilesOpen = signal<boolean>(false);
  protected readonly sharedFiles = signal<SharedFile[]>([]);
  protected readonly sharedFileBusy = signal<string | null>(null);

  protected toggleSharedFiles(): void {
    if (this.sharedFilesOpen()) {
      this.closeSharedFiles();
      return;
    }
    this.sharedFilesOpen.set(true);
    this.loadSharedFiles();
  }

  protected closeSharedFiles(): void {
    this.sharedFilesOpen.set(false);
  }

  private loadSharedFiles(): void {
    const id = this.id();
    if (!id) {
      return;
    }
    this.api
      .listSharedFiles(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (list) => this.sharedFiles.set(list),
        error: () => this.sharedFiles.set([]),
      });
  }

  protected deleteSharedFile(f: SharedFile): void {
    this.sharedFileBusy.set(f.id);
    this.api
      .deleteSharedFile(f.id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.sharedFiles.update((list) => list.filter((x) => x.id !== f.id));
          this.sharedFileBusy.set(null);
        },
        error: () => this.sharedFileBusy.set(null),
      });
  }

  protected sharedFileUrl(id: string): string {
    return this.api.sharedFileUrl(id);
  }

  /** Bytes → "412KB"/"3.1MB" (sem casas decimais abaixo de 10). */
  protected formatFileSize(bytes: number): string {
    if (bytes < 1024) {
      return `${bytes}B`;
    }
    const kb = bytes / 1024;
    if (kb < 1024) {
      return `${kb < 10 ? kb.toFixed(1) : Math.round(kb)}KB`;
    }
    const mb = kb / 1024;
    return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)}MB`;
  }

  /** ISO passado → "há 3min"/"há 2h" (pro item da lista de arquivos). */
  protected formatRelativePast(isoDate: string | null): string {
    if (!isoDate) {
      return '';
    }
    const diffMs = Date.now() - new Date(isoDate).getTime();
    const mins = Math.round(diffMs / 60000);
    if (mins < 1) {
      return 'agora';
    }
    if (mins < 60) {
      return `há ${mins}min`;
    }
    const hours = Math.round(mins / 60);
    return hours < 24 ? `há ${hours}h` : `há ${Math.round(hours / 24)}d`;
  }

  /** Proporção do painel A (0.2–0.8) no split — arrastável, persistida. */
  protected readonly splitRatio = signal<number>(readSplitRatio());
  protected readonly splitDragging = signal<boolean>(false);
  protected readonly splitFlexA = computed(() => `${this.splitRatio()} 0 0%`);
  protected readonly splitFlexB = computed(() => `${1 - this.splitRatio()} 0 0%`);
  private readonly splitViewEl = viewChild<ElementRef<HTMLDivElement>>('splitViewEl');
  private splitDragMove?: (ev: PointerEvent) => void;
  private splitDragUp?: (ev: PointerEvent) => void;

  protected readonly session = signal<Session | null>(null);

  /**
   * Gate de capabilities (multi-host, AD-011): esconde botões de features
   * que o HOST desta sessão não suporta (TTS/JARVIS, "abrir no Mac",
   * transcrição de áudio). Fail-open (`WorkersStore.supports`) — sessão sem
   * host_id ou lista de workers ainda não carregada não escondem nada.
   */
  protected readonly hostSupportsTts = computed(() =>
    this.workers.supports(this.session()?.host_id, 'tts'),
  );
  protected readonly hostSupportsOpenTerminal = computed(() =>
    this.workers.supports(this.session()?.host_id, 'open_terminal'),
  );
  protected readonly hostSupportsTranscription = computed(() =>
    this.workers.supports(this.session()?.host_id, 'transcription'),
  );

  /**
   * Host desta sessão (multi-host, AD-011) — só quando há MAIS DE 1 host
   * ativo, mesmo critério usado no badge da Home.
   */
  protected readonly hostBadge = computed(() => {
    if (!this.workers.hasMultipleHosts()) {
      return null;
    }
    return this.workers.hostname(this.session()?.host_id);
  });
  protected readonly hostEmoji = computed(() =>
    this.workers.emoji(this.session()?.host_id),
  );

  /** Tarefas/marcos desta sessão (painel recolhível). */
  protected readonly tasks = signal<Task[]>([]);
  protected readonly tasksOpen = signal<boolean>(false);
  /** Keypad recolhido por padrão (libera espaço do terminal); ⌨ expande. */
  protected readonly keypadOpen = signal<boolean>(false);
  /** Métricas recolhidas por padrão (topo resume modelo + ctx%); toque expande. */
  protected readonly metricsOpen = signal<boolean>(false);
  /** Filtro de status do painel de tarefas da sessão. */
  protected readonly taskStatusFilter = signal<
    'all' | 'todo' | 'doing' | 'done' | 'blocked'
  >('all');
  protected readonly taskFilters: {
    key: 'todo' | 'doing' | 'done' | 'blocked';
    label: string;
  }[] = [
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
  /** Quantas tarefas mostrar (infinite scroll): começa com um punhado e cresce. */
  private static readonly TASKS_PAGE = 6;
  protected readonly taskLimit = signal<number>(DetalheComponent.TASKS_PAGE);
  /** Fatia visível da lista filtrada (o resto entra ao rolar / "Ver mais"). */
  protected readonly visibleTasks = computed(() =>
    this.filteredTasks().slice(0, this.taskLimit()),
  );
  /** Quantas ainda faltam além das visíveis (0 = tudo à mostra). */
  protected readonly remainingTasks = computed(() =>
    Math.max(0, this.filteredTasks().length - this.visibleTasks().length),
  );

  /** Carrega o próximo "lote" de tarefas (botão "Ver mais"). */
  protected showMoreTasks(): void {
    this.taskLimit.update((n) => n + DetalheComponent.TASKS_PAGE);
  }

  /** Ao rolar a lista perto do fim, carrega mais (infinite scroll). */
  protected onTasksScroll(ev: Event): void {
    if (this.remainingTasks() === 0) {
      return;
    }
    const el = ev.target as HTMLElement;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 48) {
      this.showMoreTasks();
    }
  }
  /** Limites recolhidos por padrão (só %); clique expande barra + reset. */
  protected readonly limitsExpanded = signal<boolean>(false);
  protected readonly tasksDoneCount = computed(
    () => this.tasks().filter((t) => t.state === 'done').length,
  );
  /**
   * Tarefa em destaque no cabeçalho (preenche a barra que ficava vazia):
   * a EM ANDAMENTO; senão a BLOQUEADA; senão a mais recente (a lista já vem
   * ordenada por updated_at desc do backend). `null` se não há tarefas.
   */
  protected readonly currentTask = computed<Task | null>(() => {
    const list = this.tasks();
    if (!list.length) {
      return null;
    }
    return (
      list.find((t) => t.state === 'doing') ??
      list.find((t) => t.state === 'blocked') ??
      list[0]
    );
  });
  /** Prefixo do destaque ("Em andamento" / "Bloqueada" / "Última"…). */
  protected readonly currentTaskLead = computed<string>(() => {
    const t = this.currentTask();
    if (!t) {
      return '';
    }
    if (t.state === 'doing') {
      return 'Em andamento';
    }
    if (t.state === 'blocked') {
      return 'Bloqueada';
    }
    if (t.state === 'done') {
      return 'Última concluída';
    }
    return 'A fazer';
  });
  protected readonly acting = signal<boolean>(false);
  protected readonly sending = signal<boolean>(false);
  protected readonly attaching = signal<boolean>(false);
  /** Arquivo/imagem sendo arrastado sobre o compositor (realça a área de drop). */
  protected readonly dragOver = signal<boolean>(false);
  /**
   * Rótulo do feedback de "ação em trânsito" (ou null = nada). Cobre o "gap"
   * entre enviar algo (texto / anexo / áudio) e o efeito aparecer no terminal,
   * pra o usuário saber que está indo. Limpo quando a tela muda (conteúdo
   * chegou) ou por timeout de segurança.
   */
  protected readonly actionHint = signal<string | null>(null);
  private hintTimer: ReturnType<typeof setTimeout> | null = null;
  /** Tamanho da fonte do terminal (px), ajustável por A−/A+ e persistido. */
  protected readonly termFont = signal<number>(readTermFont());
  /** Modo foco: recolhe Modelo + Tarefas p/ o terminal ocupar mais espaço. */
  protected readonly focusMode = signal<boolean>(readFocusMode());

  /** Liga/desliga o modo foco (persistido) e reajusta o tamanho do pane. */
  protected toggleFocus(): void {
    const next = !this.focusMode();
    this.focusMode.set(next);
    try {
      localStorage.setItem('sf.focus', next ? '1' : '0');
    } catch {
      /* storage indisponível — silencioso */
    }
    // Mudou a altura do terminal → reajusta linhas/colunas do pane.
    this.scheduleTermResize();
  }

  /** Abre o picker de "dividir tela" — carrega as outras sessões ativas. */
  protected openSplitPicker(): void {
    this.splitPickerOpen.set(true);
    this.splitSearch.set('');
    this.api
      .listSessions()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (list) =>
          this.splitCandidates.set(list.filter((s) => s.id !== this.id())),
        error: () => this.splitCandidates.set([]),
      });
  }

  protected closeSplitPicker(): void {
    this.splitPickerOpen.set(false);
    this.splitSearch.set('');
  }

  /**
   * Escolhe a 2ª sessão do split — vira query param `compare` (linkável).
   * `replaceUrl: true`: liga/desliga o split é um estado de EXIBIÇÃO, não uma
   * navegação — sem isso, cada toggle empilhava uma entrada no histórico do
   * navegador e o botão "voltar" ficava alternando o split ligado/desligado
   * antes de finalmente sair da sessão (em vez de sair direto).
   */
  protected pickSplit(otherId: string): void {
    this.splitPickerOpen.set(false);
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { compare: otherId },
      queryParamsHandling: 'merge',
      replaceUrl: true,
    });
  }

  /** Sai do modo split, voltando à tela normal desta sessão (ver `pickSplit`). */
  protected exitSplit(): void {
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { compare: null },
      queryParamsHandling: 'merge',
      replaceUrl: true,
    });
  }

  /**
   * Início do arrasto do divisor do split. Funciona em pointer (mouse/touch)
   * e detecta o eixo NA HORA (horizontal quando os painéis estão lado a lado,
   * vertical quando o CSS empilha em telas estreitas) medindo o próprio
   * container — não depende de replicar o breakpoint do media query em JS.
   */
  protected onSplitDividerDown(ev: PointerEvent): void {
    const el = this.splitViewEl()?.nativeElement;
    if (!el) {
      return;
    }
    ev.preventDefault();
    this.splitDragging.set(true);
    const rect = () => el.getBoundingClientRect();
    // Colunas lado a lado quando a largura do container manda mais que a
    // altura visível de cada painel — na prática: usa o CSS real (a mesma
    // media query já decide flex-direction); lemos via computedStyle.
    const vertical = getComputedStyle(el).flexDirection === 'column';

    const move = (e: PointerEvent) => {
      const r = rect();
      const ratio = vertical
        ? (e.clientY - r.top) / r.height
        : (e.clientX - r.left) / r.width;
      this.splitRatio.set(Math.min(0.8, Math.max(0.2, ratio)));
    };
    const up = () => {
      this.splitDragging.set(false);
      try {
        localStorage.setItem('sf.split.ratio', String(this.splitRatio()));
      } catch {
        /* storage indisponível — silencioso */
      }
      if (this.splitDragMove) {
        window.removeEventListener('pointermove', this.splitDragMove);
      }
      if (this.splitDragUp) {
        window.removeEventListener('pointerup', this.splitDragUp);
        window.removeEventListener('pointercancel', this.splitDragUp);
      }
    };
    this.splitDragMove = move;
    this.splitDragUp = up;
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', up);
  }

  /** Duplo-toque/clique no divisor: volta pro 50/50. */
  protected resetSplitRatio(): void {
    this.splitRatio.set(0.5);
    try {
      localStorage.setItem('sf.split.ratio', '0.5');
    } catch {
      /* storage indisponível — silencioso */
    }
  }

  /** Throttle do scroll-pro-agente (roda do mouse / toque) (ms). */
  private lastWheelAt = 0;
  /** Y inicial do toque no terminal (p/ medir arrasto no limite — tablet). */
  private touchStartY = 0;
  /** Toast "Copiado" ao selecionar texto do terminal. */
  protected readonly copied = signal<boolean>(false);
  private copyTimer: ReturnType<typeof setTimeout> | null = null;
  /** Últimas dimensões (cols×linhas) enviadas ao tmux — evita reenvio igual. */
  private lastCols = 0;
  private lastRows = 0;
  private resizeTimer: ReturnType<typeof setTimeout> | null = null;
  /**
   * Anexos aguardando confirmação (staged, ainda não enviados). Cada item
   * carrega o File + o object URL do thumbnail (imagens; revogado ao
   * remover/limpar/enviar/destruir) + uma chave estável p/ o @for.
   */
  protected readonly pendingItems = signal<
    { file: File; url: string | null; key: number }[]
  >([]);
  /** Sequência p/ as chaves dos itens staged (estável mesmo removendo do meio). */
  private stagedSeq = 0;
  /** Máx. de anexos por envio (o excedente é avisado e ignorado). */
  private static readonly MAX_ATTACH = 8;
  /** Tamanho máx. por arquivo (~10MB). */
  private static readonly MAX_ATTACH_BYTES = 10 * 1024 * 1024;

  /** Tamanho de um arquivo em KB (mín. 1), p/ exibir no preview. */
  protected sizeKb(f: File): number {
    return Math.max(1, Math.round(f.size / 1024));
  }

  /** Placeholder do input: reflete anexos staged / modo ao vivo. */
  protected readonly inputPlaceholder = computed(() => {
    const n = this.pendingItems().length;
    if (n === 1) {
      return 'Escreva algo sobre o anexo (opcional)…';
    }
    if (n > 1) {
      return `Escreva algo sobre os ${n} anexos (opcional)…`;
    }
    return this.liveMode()
      ? 'Digite — ao vivo no terminal…'
      : 'Enviar comando ao terminal…';
  });
  /** Input focado → esconde os botões de ação (mais espaço pra digitar). */
  protected readonly inputFocused = signal<boolean>(false);
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
    this.sanitizer.bypassSecurityTrustHtml(
      ansiToHtml(trimBlankEdges(this.screen())),
    ),
  );

  /** Pill "↓ ao vivo": visível quando o usuário rolou o agente p/ cima. */
  protected readonly showLivePill = signal<boolean>(false);

  /** Último artifact visto no espelho ENQUANTO esta tela está aberta. */
  private readonly seenArtifactUrl = signal<string | null>(null);
  /**
   * Artifacts da sessão (mais recente primeiro): o histórico persistido pelo
   * WORKER (sobrevive a rolagem/reload) + o que o espelho mostrou agora. O
   * rodapé "⧉ <nome>" do Claude Code não expõe URL (clique é tratado no Mac).
   */
  protected readonly artifactList = computed<string[]>(() => {
    const s = this.session();
    const list = [...(s?.artifact_urls ?? [])];
    if (list.length === 0 && s?.last_artifact_url) {
      list.push(s.last_artifact_url); // docs antigos (só o campo single)
    }
    const seen = this.seenArtifactUrl();
    if (seen) {
      const i = list.indexOf(seen);
      if (i >= 0) {
        list.splice(i, 1);
      }
      list.unshift(seen);
    }
    return list;
  });
  /** URL principal do botão (o artifact mais recente). */
  protected readonly artifactUrl = computed<string | null>(
    () => this.artifactList()[0] ?? null,
  );
  /** Menu com o histórico de artifacts (aberto pelo chevron do botão). */
  protected readonly artifactMenuOpen = signal<boolean>(false);

  /** Rótulo curto de um artifact (uuid abreviado) p/ o menu. */
  protected artifactLabel(url: string, i: number): string {
    const id = url.split('/').pop() ?? '';
    return `${i === 0 ? 'mais recente' : 'artifact ' + (i + 1)} · ${id.slice(0, 8)}`;
  }

  /** Painel de renomear (nome técnico do tmux + nome falado/exibição). */
  protected readonly renameOpen = signal<boolean>(false);
  protected readonly renameTech = signal<string>('');
  protected readonly renameDisp = signal<string>('');
  protected readonly renaming = signal<boolean>(false);

  /** Painel "Trocar provedor" (no bloco de métricas): troca o agente da MESMA
   *  sessão com handoff de contexto (o worker orquestra em background). */
  protected readonly switchOpen = signal<boolean>(false);
  protected readonly switchAgentSel = signal<AgentType>('claude');
  protected readonly switchModel = signal<string>('');
  protected readonly switching = signal<boolean>(false);
  protected readonly switchProviders: AgentType[] = [
    'claude',
    'codex',
    'gemini',
    'opencode',
  ];

  /** Captura de tela (só onde o navegador suporta — desktop/Mac). */
  protected readonly canScreenshot =
    typeof navigator !== 'undefined' &&
    !!navigator.mediaDevices?.getDisplayMedia;
  protected readonly shotOpen = signal<boolean>(false);
  protected readonly shotImgUrl = signal<string>('');
  protected readonly shotSel = signal<{ x: number; y: number; w: number; h: number } | null>(null);
  protected readonly shotHasSel = computed(() => {
    const s = this.shotSel();
    return !!s && s.w >= 4 && s.h >= 4;
  });
  private shotCanvas: HTMLCanvasElement | null = null;
  private shotStart: { x: number; y: number } | null = null;

  /**
   * Câmera (foto ao vivo) — como o screenshot, mas usando a câmera do aparelho
   * (getUserMedia) pra dar contexto físico ao agente (foto de tela, papel, etc.).
   * Só onde o navegador suporta E em contexto seguro (https/localhost). A foto
   * fica só em memória (vira anexo staged) — nunca vai pra galeria do aparelho.
   */
  protected readonly canCamera =
    typeof navigator !== 'undefined' &&
    !!navigator.mediaDevices?.getUserMedia;
  protected readonly camOpen = signal<boolean>(false);
  /** True quando o preview já tem frame (habilita o botão "Tirar foto"). */
  protected readonly camReady = signal<boolean>(false);
  private camStream: MediaStream | null = null;
  /** Câmera preferida: traseira por padrão (melhor p/ fotografar algo). */
  private camFacing: 'environment' | 'user' = 'environment';

  /**
   * Modo BUFFER: rolagem LISA do histórico. Como os TUIs alt-screen (Claude
   * Code) guardam o scrollback dentro do próprio agente (não no tmux), montamos
   * um buffer COSTURANDO os frames capturados: a cada "página" pra cima pedimos
   * o redraw ao agente, capturamos e prependamos só as linhas novas (detectando
   * o overlap). O usuário rola nativo dentro do buffer (sem round-trip por
   * linha); só ao chegar no topo buscamos mais. Sai pela pill "↓ ao vivo".
   */
  protected readonly bufMode = signal<boolean>(false);
  /** Texto acumulado (colorido) do buffer de scrollback. */
  protected readonly bufText = signal<string>('');
  protected readonly bufHtml = computed<SafeHtml>(() =>
    this.sanitizer.bypassSecurityTrustHtml(
      ansiToHtml(trimBlankEdges(this.bufText())),
    ),
  );
  /** True enquanto busca a próxima leva de histórico (mostra "carregando…"). */
  protected readonly bufLoading = signal<boolean>(false);
  /** Nº de linhas no buffer (diagnóstico visível no indicador). */
  protected readonly bufCount = signal<number>(0);
  /** Espelho de {@link bufExhausted} p/ o template (topo do histórico atingido). */
  protected readonly bufExhaustedUi = signal<boolean>(false);
  /** Linhas acumuladas (mais antigas no topo). */
  private bufLines: string[] = [];
  /** Guard: uma busca de "mais histórico" em andamento. */
  private bufBusy = false;
  /** True quando não há mais histórico a revelar (chegou no topo). */
  private bufExhausted = false;
  /** Frames vazios seguidos (redraw lento vs. topo real) — esgota após 2. */
  private bufEmptyStreak = 0;
  /** True enquanto ajustamos scrollTop programaticamente (ignora onTermScroll). */
  private bufAdjusting = false;
  /** Teto de linhas no buffer (evita crescer sem limite). */
  private static readonly BUF_MAX_LINES = 5000;

  protected readonly canSend = computed(
    () =>
      (this.draft().trim().length > 0 || this.pendingItems().length > 0) &&
      !this.sending() &&
      !this.attaching() &&
      !!this.id(),
  );

  /** Texto da última tela renderizada, para disparar o auto-scroll. */
  private lastRenderedScreen = '';

  /**
   * ``at`` do último frame de SSE JÁ aplicado ao espelho. O effect do push só
   * aplica um frame quando esse timestamp MUDA (push novo do worker) — nunca por
   * o texto do SSE simplesmente diferir de {@link screen}. Sem isso, um frame de
   * SSE defasado (ex.: reconexão/backoff) brigava com o poll de 4s: o poll trazia
   * a tela nova do doc e o effect a revertia pro frame velho, congelando o "ao
   * vivo" até o próximo push. Agora o poll sempre vence a frescura e o SSE só
   * adiciona baixa latência em pushes de fato novos.
   */
  private lastScreenPushAt = '';

  /**
   * Auto-scroll só "gruda no fim" quando o usuário JÁ está no fim. Se ele rolou
   * para cima (ex.: acompanhando o highlight de um picker TUI ao navegar com as
   * setas), NÃO arrastamos a viewport — senão a opção em foco some de vista.
   */
  private stickToBottom = true;

  constructor() {
    // MODO CONVIDADO: pega o token do link (?k=) e registra ANTES de conectar o
    // SSE/chamar a API — assim toda chamada já vai escopada por ele. (Fora do
    // modo guest, fica null e tudo segue com o JWT normal.)
    if (this.guest()) {
      this.shareSvc.set(this.route.snapshot.queryParamMap.get('k'));
      this.destroyRef.onDestroy(() => this.shareSvc.clear());
    }

    this.sse.connect(); // idempotente — garante o canal p/ o push do espelho

    // Ao trocar o filtro de tarefas, recomeça a paginação do topo (senão o
    // "Ver mais 3" de um filtro vaza pro outro, mostrando itens demais/de menos).
    effect(() => {
      this.taskStatusFilter();
      this.taskLimit.set(DetalheComponent.TASKS_PAGE);
    });

    // Guarda o ÚLTIMO artifact (claude.ai) que passou pelo espelho — o rodapé
    // "⧉ <nome>" do Claude Code não expõe a URL, então oferecemos a última vista.
    effect(() => {
      const raw = this.screen();
      if (!raw) {
        return;
      }
      // eslint-disable-next-line no-control-regex
      const text = raw.replace(/\x1b\[[0-9;]*m|\x1b\]8;[^\x07\x1b]*(?:\x07|\x1b\\)/g, '');
      const m = text.match(/https:\/\/claude\.ai\/code\/artifact\/[0-9a-f-]+/gi);
      if (m && m.length > 0) {
        this.seenArtifactUrl.set(m[m.length - 1]);
      }
    });

    // id REATIVO: ao navegar /sessao/A -> /sessao/B o Angular REUSA este mesmo
    // componente, então ler só o snapshot deixa o id PRESO na sessão anterior —
    // a tela, o upload e o input passam a mirar a sessão errada (bug "mostra o
    // sessionflow dentro da secretaria"). Assinamos o paramMap e, a cada id
    // novo, resetamos o estado por-sessão e recarregamos do zero.
    this.route.paramMap
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((pm) => {
        const sid = pm.get('id') ?? '';
        if (sid === this.id() && this.session()) {
          return; // mesma sessão já carregada
        }
        this.id.set(sid);
        // Limpa o estado da sessão anterior (senão "vaza" pra esta).
        this.session.set(null);
        this.screen.set('');
        this.tasks.set([]);
        this.seenArtifactUrl.set(null);
        this.bufMode.set(false);
        this.bufLines = [];
        this.bufText.set('');
        this.lastScreenPushAt = ''; // sessão nova → não herda o gate do push anterior
        // Restaura as OPÇÕES escolhidas antes nesta sessão (SessionPrefsStore);
        // campos ausentes caem no default. Persistidas nos toggles/bumpFont.
        const prefs = this.prefs.get(sid);
        this.liveMode.set(prefs.liveMode ?? false);
        this.keypadOpen.set(prefs.keypadOpen ?? false);
        this.termFont.set(prefs.termFont ?? readTermFont());
        this.draft.set(this.drafts.get(sid));
        this.loadSession();
        this.refreshScreen();
        // Ao abrir a sessão, foca o campo de mensagem (pronto pra digitar). Em
        // celular o teclado só abre com gesto — aqui é best-effort e não incomoda.
        this.focusMessageInput();
        // Ao ABRIR a sessão, instrui (1x) a trabalhar em tarefas/marcos. O
        // server é idempotente e respeita o toggle global.
        if (sid) {
          this.api
            .instructMilestones(sid)
            .pipe(takeUntilDestroyed(this.destroyRef))
            .subscribe({ error: () => {} });
        }
      });

    // Modo split (query param `compare`): reativo p/ back/forward do navegador
    // e pra refletir o que o picker escreve na URL.
    this.route.queryParamMap
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((qp) => {
        this.compareId.set(qp.get('compare') || null);
      });

    // Espelho PUSHADO: o worker empurra a tela (SSE) assim que muda. Aplicamos
    // o último frame da NOSSA sessão (casado por tmux_name) — feedback quase
    // imediato, sem esperar poll. Atualiza scroll-stick antes de trocar.
    effect(() => {
      const tn = this.session()?.tmux_name;
      if (!tn) {
        return;
      }
      const scr = this.sse.screens()[tn];
      // No modo buffer o terminal fica congelado — não aplicamos frames novos
      // (mas continuamos lendo o signal p/ não desinscrever o effect).
      if (this.bufMode()) {
        return;
      }
      // Só aplica quando o worker EMPURROU um frame novo (``at`` mudou). Comparar
      // por texto != screen() faria o effect brigar com o poll de 4s e reverter a
      // tela nova do doc para um frame de SSE defasado — o que congelava o "ao
      // vivo". Ao gatear por ``at``, o poll nunca é revertido e o SSE só antecipa
      // pushes realmente novos.
      const at = scr?.at ?? '';
      if (scr && at !== this.lastScreenPushAt) {
        this.lastScreenPushAt = at;
        if (scr.text !== this.screen()) {
          this.stickToBottom = this.isAtBottom();
          this.screen.set(scr.text);
          // A tela mudou → o conteúdo enviado (texto/anexo/áudio) chegou: tira o aviso.
          this.clearHint();
        }
      }
    });

    // Fallback: se o SSE cair, um poll lento (4s) garante que a tela não trava.
    // Aproveita p/ atualizar as tarefas (worker sincroniza ~6s).
    const poll = setInterval(() => {
      // No modo buffer o terminal está congelado — só atualiza tarefas.
      if (!this.bufMode()) {
        this.refreshScreen();
      }
      this.loadTasks();
      // Re-busca o DOC da sessão também (status/métricas/artifacts mudam no
      // worker sem evento) — antes só atualizava em ações pontuais.
      this.loadSession();
    }, 4000);

    // Responsivo: ajusta o pane do tmux p/ caber na área do terminal (o agente
    // reflui e usa a largura toda — monitor grande etc.). Inicial + a cada
    // resize de janela (debounced em scheduleTermResize).
    const onWinResize = () => this.scheduleTermResize();
    window.addEventListener('resize', onWinResize);
    this.scheduleTermResize();
    // Ao VOLTAR o app pra frente (ex.: depois de usar "abrir no Mac", que deixa a
    // janela do tmux grande), reassume o tamanho mobile. Zera lastCols p/ furar o
    // guard de "não mudou" (o tamanho real pode ter sido alterado por fora).
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        this.lastCols = 0;
        this.lastRows = 0;
        this.scheduleTermResize();
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    // A 'JetBrains Mono' vem do Google Fonts (async). Se medirmos ANTES de ela
    // carregar, o char-width sai com a métrica da fonte de fallback → o nº de
    // colunas erra: às vezes o pane fica mais largo que a tela (barra de status
    // do tmux quebra) ou mais estreito (espaço sem aproveitar à direita). Ao
    // terminar de carregar as fontes, re-medimos com a métrica final.
    const fonts = (document as unknown as { fonts?: { ready?: Promise<unknown> } }).fonts;
    fonts?.ready?.then(() => this.scheduleTermResize()).catch(() => {});

    this.destroyRef.onDestroy(() => {
      clearInterval(poll);
      window.removeEventListener('resize', onWinResize);
      document.removeEventListener('visibilitychange', onVisible);
      if (this.liveTimer) {
        clearTimeout(this.liveTimer);
      }
      if (this.hintTimer) {
        clearTimeout(this.hintTimer);
      }
      if (this.resizeTimer) {
        clearTimeout(this.resizeTimer);
      }
      if (this.copyTimer) {
        clearTimeout(this.copyTimer);
      }
      // Evita vazar o object URL do preview do anexo staged.
      this.revokePendingUrl();
      // Garante que a câmera não fica ligada ao sair da tela.
      this.stopCamStream();
    });
  }

  ngAfterViewChecked(): void {
    if (this.bufMode()) {
      return; // buffer controla o próprio scroll (não gruda no fim)
    }
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

  /** Custo estimado (preço de API) por modelo, ou null se ausente. */
  protected readonly cost = computed(() => this.metrics()?.cost ?? null);

  /**
   * Quebra por modelo do custo: exibida quando há 2+ modelos OU quando o
   * único modelo tem preço conhecido (usd != null). Único sem preço não
   * ganha quebra (o total já mostra "—").
   */
  protected readonly costRows = computed(() => {
    const rows = this.cost()?.by_model ?? [];
    if (rows.length >= 2) {
      return rows;
    }
    if (rows.length === 1 && rows[0].usd != null) {
      return rows;
    }
    return [];
  });

  /** Formata USD: 2 casas; valores <0,01 ganham mais precisão (0.003). */
  protected fmtUsd(n: number): string {
    if (n > 0 && n < 0.01) {
      return n.toFixed(3);
    }
    return n.toFixed(2);
  }

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
  /**
   * Modelo lido da STATUSLINE do terminal (o que está REALMENTE selecionado,
   * ex. "Opus 4.8"). O modelo das métricas vem do transcript (última resposta)
   * e fica defasado quando se troca via `/model`; a statusline é a verdade do
   * que o usuário vê. Vazio se não der pra extrair.
   */
  protected readonly terminalModel = computed<string>(() => {
    const scr = this.screen();
    if (!scr) {
      return '';
    }
    const stripAnsi = (s: string) => s.replace(/\[[0-9;]*m/g, '');
    const lines = scr.split('\n').map(stripAnsi);
    const line = [...lines]
      .reverse()
      .find(
        (l) =>
          l.includes('│') && /Opus|Sonnet|Haiku|Gemini|GPT|claude-/i.test(l),
      );
    if (!line) {
      return '';
    }
    const seg = (line.split('│').pop() ?? '').replace(/\([^)]*\)/g, '').trim();
    return seg;
  });

  protected readonly modelLabel = computed<string>(
    () =>
      this.terminalModel() ||
      this.metrics()?.model ||
      this.session()?.model ||
      '—',
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

  /** Exposto pro template (picker de split) — status de uma sessão QUALQUER. */
  protected readonly STATUS_META = STATUS_META;

  /** Sessão ativa (rodando ou aguardando) → botão vira "Parar"; senão "Retomar". */
  protected isRunning(): boolean {
    const st = this.session()?.status;
    return st === 'running' || st === 'waiting_input';
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

  /** Abre o painel de renomear, pré-preenchendo com os nomes atuais. */
  protected openRename(): void {
    const s = this.session();
    this.renameTech.set(s?.tmux_name ?? '');
    this.renameDisp.set(s?.display_name ?? '');
    this.renameOpen.set(true);
  }

  protected closeRename(): void {
    this.renameOpen.set(false);
  }

  /**
   * Salva os nomes: display_name (app+TTS, direto) e/ou o nome técnico do tmux
   * (via worker). Recarrega o doc depois (o rename técnico é assíncrono).
   */
  protected saveRename(): void {
    const id = this.id();
    const s = this.session();
    if (!id || !s || this.renaming()) {
      return;
    }
    const tech = this.renameTech().trim();
    const disp = this.renameDisp().trim();
    const techChanged = !!tech && tech !== (s.tmux_name ?? '');
    const dispChanged = disp !== (s.display_name ?? '');
    if (!techChanged && !dispChanged) {
      this.renameOpen.set(false);
      return;
    }
    this.renaming.set(true);
    const finish = () => {
      this.renaming.set(false);
      this.renameOpen.set(false);
      this.loadSession();
      setTimeout(() => this.loadSession(), 1500); // pega o rename do worker (fila)
    };
    const doTech = () => {
      if (!techChanged) {
        finish();
        return;
      }
      this.api
        .renameSession(id, tech)
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe({ next: finish, error: () => this.renaming.set(false) });
    };
    if (dispChanged) {
      this.api
        .setDisplayName(id, disp)
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe({ next: doTech, error: () => this.renaming.set(false) });
    } else {
      doTech();
    }
  }

  /** Abre o painel de trocar provedor pré-selecionando o agente atual. */
  protected openSwitch(): void {
    if (this.guest()) {
      return;
    }
    const cur = this.session()?.agent_type;
    if (cur && this.switchProviders.includes(cur)) {
      this.switchAgentSel.set(cur);
    }
    this.switchModel.set('');
    this.switchOpen.set(true);
  }

  /** Badge do agente no topo: alterna o painel de trocar provedor. */
  protected toggleSwitchFromHeader(): void {
    if (this.switchOpen()) {
      this.switchOpen.set(false);
      return;
    }
    this.openSwitch();
  }

  /**
   * Confirma a troca de provedor: 202 aceito — o worker pede o handoff ao
   * agente atual, derruba-o e sobe o novo em background. Recarrega o doc e a
   * tela em 2 tempos (como o resume), e o card mostra "Trocando provedor…"
   * (activity setada pelo worker) enquanto roda.
   */
  protected confirmSwitch(): void {
    const id = this.id();
    if (!id || this.switching()) {
      return;
    }
    this.switching.set(true);
    const model = this.switchModel().trim() || null;
    this.api
      .switchAgent(id, this.switchAgentSel(), model)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.switching.set(false);
          this.switchOpen.set(false);
          this.showHint('Trocando provedor…');
          setTimeout(() => {
            this.loadSession();
            this.refreshScreen(true);
          }, 3000);
          setTimeout(() => {
            this.loadSession();
            this.refreshScreen(true);
          }, 10000);
        },
        error: () => {
          this.switching.set(false);
          this.warnHint('Falha ao trocar o provedor.');
        },
      });
  }

  protected resume(): void {
    if (this.acting() || !this.id()) {
      return;
    }
    this.acting.set(true);
    // Otimista: MANTÉM a sessão atual (com o display_name!) e só marca "rodando".
    // NÃO usamos a resposta do /resume — ela é um {command_id, status:"accepted"},
    // não um Session; setá-la zerava display_name/tmux_name e o cabeçalho caía no
    // id (a "hash", exigindo voltar ao Início e reentrar). O worker recria a tmux
    // em background; recarregamos o doc real e a tela do agente quando reaparece.
    this.markWorkingLocal();
    this.api
      .resumeSession(this.id())
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.acting.set(false);
          this.stickToBottom = true;
          this.refreshScreen(true);
          // A sessão volta em background (recreate + agente subir): re-sincroniza
          // status/métricas e a tela em 2 tempos, sem precisar sair da tela.
          setTimeout(() => {
            this.loadSession();
            this.refreshScreen(true);
          }, 1500);
          setTimeout(() => {
            this.loadSession();
            this.refreshScreen(true);
          }, 4000);
        },
        error: () => this.acting.set(false),
      });
  }

  /** Abre esta sessão num Terminal do Mac (tmux attach, lado a lado). */
  protected openInMac(): void {
    const id = this.id();
    if (!id) {
      return;
    }
    this.api
      .openTerminal(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        error: () => {
          /* best-effort — abre no Mac; falha silenciosa */
        },
      });
  }

  // --- Link compartilhável (só dono) ---

  /** Abre/fecha o painel "Compartilhar"; ao abrir, busca o estado atual do link. */
  protected toggleShare(): void {
    const open = !this.shareOpen();
    this.shareOpen.set(open);
    this.shareCopied.set(false);
    if (open && this.shareLink() === null) {
      const id = this.id();
      if (id) {
        this.api
          .getShareLink(id)
          .pipe(takeUntilDestroyed(this.destroyRef))
          .subscribe({
            next: (l) => this.shareLink.set(l),
            error: () => this.shareLink.set({ active: false }),
          });
      }
    }
  }

  /** Gera (ou rotaciona) o link — vale 24h e morre se a sessão parar/sumir. */
  protected generateShareLink(): void {
    const id = this.id();
    if (!id || this.shareBusy()) {
      return;
    }
    this.shareBusy.set(true);
    this.shareCopied.set(false);
    this.api
      .createShareLink(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (l) => {
          this.shareLink.set(l);
          this.shareBusy.set(false);
        },
        error: () => this.shareBusy.set(false),
      });
  }

  /** Revoga o link na hora (invalida mesmo com a sessão viva). */
  protected revokeShareLink(): void {
    const id = this.id();
    if (!id || this.shareBusy()) {
      return;
    }
    this.shareBusy.set(true);
    this.api
      .revokeShareLink(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (l) => {
          this.shareLink.set(l);
          this.shareBusy.set(false);
          this.shareCopied.set(false);
        },
        error: () => this.shareBusy.set(false),
      });
  }

  /** Copia a URL do link pro clipboard (feedback efêmero "copiado"). */
  protected copyShareLink(): void {
    const url = this.shareLink()?.url;
    if (!url || typeof navigator === 'undefined' || !navigator.clipboard) {
      return;
    }
    void navigator.clipboard
      .writeText(url)
      .then(() => {
        this.shareCopied.set(true);
        setTimeout(() => this.shareCopied.set(false), 2000);
      })
      .catch(() => {});
  }

  protected end(): void {
    // "Encerrar" = PARA a sessão (encerra o tmux/agente no host) mas MANTÉM o
    // registro — vira "Parada" e pode ser Retomada (claude --continue). Só a
    // 🗑️ (eliminar) apaga de vez. Otimista: marca parada na hora e volta.
    const id = this.id();
    if (this.acting() || !id) {
      return;
    }
    const s = this.session();
    if (s) {
      this.session.set({ ...s, status: 'stopped' });
    }
    this.acting.set(true);
    this.api
      .deleteSession(id) // comando "kill": encerra o tmux, preserva o registro
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.acting.set(false);
          if (!this.guest()) {
            void this.router.navigate(['/sessoes']);
          }
        },
        error: () => {
          this.acting.set(false);
          if (!this.guest()) {
            void this.router.navigate(['/sessoes']);
          }
        },
      });
  }

  /** Elimina a sessão de vez (mata no host + remove daqui). Confirma antes. */
  protected eliminate(): void {
    if (this.acting() || !this.id()) {
      return;
    }
    const nm = this.displayName();
    if (!confirm(`Eliminar a sessão "${nm}"? Isso encerra no Mac e remove ela daqui — não dá pra desfazer.`)) {
      return;
    }
    this.acting.set(true);
    this.api
      .purgeSession(this.id())
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.acting.set(false);
          if (!this.guest()) {
            void this.router.navigate(['/sessoes']);
          }
        },
        error: () => this.acting.set(false),
      });
  }

  /** Liga/desliga o JARVIS (resumo falado) desta sessão. Otimista + rollback. */
  protected toggleJarvis(): void {
    const s = this.session();
    const id = this.id();
    if (!s || !id) {
      return;
    }
    const next = !s.jarvis;
    this.session.set({ ...s, jarvis: next }); // otimista
    this.api
      .setJarvis(id, next)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        error: () => {
          const cur = this.session();
          if (cur) {
            this.session.set({ ...cur, jarvis: !next }); // reverte
          }
        },
      });
  }

  protected send(): void {
    const id = this.id();
    if (!id) {
      return;
    }
    // Anexos staged → envia os ARQUIVOS (com o texto digitado como legenda) num
    // fluxo só, pra imagens + texto chegarem JUNTOS no agente.
    if (this.pendingItems().length > 0) {
      this.sendFile();
      return;
    }
    // Modo ao vivo: o texto JÁ está no pane (foi encaminhado enquanto digitava)
    // → submeter é só um Enter. Limpa o estado local + buffer.
    if (this.liveMode()) {
      this.flushForward(); // garante que o último diff foi enviado
      this.showHint('Enviando…');
      this.markWorkingLocal();
      this.api
        .sendKey(id, 'enter')
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe({ next: () => this.refreshBurst(), error: () => this.clearHint() });
      this.draft.set('');
      this.drafts.set(id, '');
      this.paneBuffer = '';
      return;
    }
    // Caixa vazia (sem anexo, sem modo ao vivo): Enviar == apertar Enter no
    // terminal — aceita prompts tipo "Press Enter to continue"/confirma
    // seleção de menu, em vez de não fazer nada (o botão fica desabilitado
    // aqui, mas o Enter físico no campo chega em send() do mesmo jeito).
    if (!this.draft().trim() && !this.sending() && !this.attaching()) {
      this.showHint('Enviando…');
      this.markWorkingLocal();
      this.api
        .sendKey(id, 'enter')
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe({ next: () => this.refreshBurst(), error: () => this.clearHint() });
      return;
    }
    if (!this.canSend()) {
      return;
    }
    const text = this.draft().trim();
    // Sem eco local: o espelho de tela mostra o que foi digitado na caixa do
    // agente no próximo poll (~1.2s).
    this.sending.set(true);
    this.showHint('Enviando…');
    this.markWorkingLocal();
    this.api
      .sendInput(this.id(), text)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.draft.set('');
          this.drafts.set(id, '');
          this.sending.set(false);
          this.refreshBurst(); // pega o eco no pane assim que o worker injeta
        },
        error: () => {
          this.sending.set(false);
          this.clearHint(); // falhou → tira o aviso na hora
        },
      });
  }

  /** Atualiza o draft e, no modo ao vivo, agenda o encaminhamento do diff. */
  protected onDraftChange(value: string): void {
    this.draft.set(value);
    this.drafts.set(this.id(), value); // persiste por sessão
    // Com anexo staged, o texto é LEGENDA dos arquivos — não encaminha ao vivo.
    if (this.liveMode() && this.pendingItems().length === 0) {
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
    this.prefs.patch(id ?? '', { liveMode: turningOn }); // lembra por sessão
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
  /** Abre o seletor de arquivo para anexar à sessão. */
  protected pickFile(): void {
    this.fileInput()?.nativeElement.click();
  }

  /**
   * Apenas "staged": guarda o arquivo escolhido e gera preview (sem enviar).
   * O envio só ocorre quando o usuário confirma em {@link sendFile}.
   */
  protected onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const files = Array.from(input.files ?? []);
    input.value = ''; // permite reanexar o mesmo arquivo
    this.stageFiles(files);
  }

  /** Põe UM arquivo em "staged" (reusado pelo screenshot). Acrescenta à lista. */
  private stageFile(file: File | null | undefined): void {
    if (file) {
      this.stageFiles([file]);
    }
  }

  /**
   * ACRESCENTA arquivos à lista de staged + gera preview (imagens). Reusado
   * por seletor, colar e drop. Aplica os limites (máx {@link MAX_ATTACH}
   * anexos por envio, ~10MB por arquivo) avisando pelo hint existente.
   */
  private stageFiles(files: (File | null | undefined)[]): void {
    const incoming = files.filter((f): f is File => !!f);
    if (!incoming.length) {
      return;
    }
    const small = incoming.filter(
      (f) => f.size <= DetalheComponent.MAX_ATTACH_BYTES,
    );
    const tooBig = incoming.length - small.length;
    const room = Math.max(
      0,
      DetalheComponent.MAX_ATTACH - this.pendingItems().length,
    );
    const accepted = small.slice(0, room);
    const overflow = small.length - accepted.length;
    if (tooBig > 0) {
      this.warnHint(
        tooBig === 1
          ? 'Arquivo acima de 10MB ignorado.'
          : `${tooBig} arquivos acima de 10MB ignorados.`,
      );
    } else if (overflow > 0) {
      this.warnHint(
        `Máximo de ${DetalheComponent.MAX_ATTACH} anexos por envio — ${overflow} de fora.`,
      );
    }
    if (!accepted.length) {
      return;
    }
    const added = accepted.map((file) => ({
      file,
      url: file.type.startsWith('image/') ? URL.createObjectURL(file) : null,
      key: ++this.stagedSeq,
    }));
    this.pendingItems.update((cur) => [...cur, ...added]);
  }

  /**
   * Colar (Cmd/Ctrl+V) com IMAGENS na área de transferência → anexa TODAS como
   * se fossem arrastadas (preview + você adiciona uma legenda e envia). Colar
   * de novo ACRESCENTA à lista. Ignora colagem de texto (comportamento normal
   * do input).
   */
  protected onPaste(event: ClipboardEvent): void {
    const items = event.clipboardData?.items;
    if (!items) {
      return;
    }
    const ts = Date.now();
    const images: File[] = [];
    for (const it of Array.from(items)) {
      if (it.kind !== 'file' || !it.type.startsWith('image/')) {
        continue;
      }
      const file = it.getAsFile();
      if (!file) {
        continue;
      }
      // Clipboard costuma vir sem nome → dá um p/ o upload/legenda.
      images.push(
        file.name
          ? file
          : new File([file], `colado-${ts}-${images.length + 1}.png`, {
              type: file.type,
            }),
      );
    }
    if (images.length) {
      event.preventDefault();
      this.stageFiles(images);
    }
  }

  /**
   * Captura a tela (getDisplayMedia), tira UM frame e abre o overlay de recorte.
   * A imagem fica só em memória — nunca vai pra galeria do aparelho. Desktop/Mac.
   */
  protected async takeShot(): Promise<void> {
    const md = navigator.mediaDevices;
    if (!md?.getDisplayMedia) {
      return;
    }
    let stream: MediaStream | null = null;
    try {
      stream = await md.getDisplayMedia({ video: true, audio: false });
      const video = document.createElement('video');
      video.srcObject = stream;
      video.muted = true;
      await video.play();
      // Espera as dimensões reais do vídeo chegarem.
      for (let i = 0; i < 20 && !video.videoWidth; i++) {
        await new Promise((r) => setTimeout(r, 50));
      }
      const w = video.videoWidth || 1280;
      const h = video.videoHeight || 720;
      const canvas = document.createElement('canvas');
      canvas.width = w;
      canvas.height = h;
      canvas.getContext('2d')?.drawImage(video, 0, 0, w, h);
      this.shotCanvas = canvas;
      this.shotImgUrl.set(canvas.toDataURL('image/png'));
      this.shotSel.set(null);
      this.shotStart = null;
      this.shotOpen.set(true);
    } catch {
      /* usuário cancelou o compartilhamento ou negou — silencioso */
    } finally {
      stream?.getTracks().forEach((t) => t.stop()); // encerra a captura já no 1º frame
    }
  }

  /** Início do arrasto de seleção sobre a captura. */
  protected shotDown(ev: PointerEvent): void {
    const img = this.shotImg()?.nativeElement;
    if (!img) {
      return;
    }
    const r = img.getBoundingClientRect();
    this.shotStart = { x: ev.clientX - r.left, y: ev.clientY - r.top };
    this.shotSel.set({ x: this.shotStart.x, y: this.shotStart.y, w: 0, h: 0 });
    try {
      img.setPointerCapture(ev.pointerId);
    } catch {
      /* sem captura de ponteiro — segue */
    }
  }

  /** Atualiza o retângulo de seleção conforme arrasta. */
  protected shotMove(ev: PointerEvent): void {
    if (!this.shotStart) {
      return;
    }
    const img = this.shotImg()?.nativeElement;
    if (!img) {
      return;
    }
    const r = img.getBoundingClientRect();
    const cx = Math.max(0, Math.min(ev.clientX - r.left, r.width));
    const cy = Math.max(0, Math.min(ev.clientY - r.top, r.height));
    this.shotSel.set({
      x: Math.min(this.shotStart.x, cx),
      y: Math.min(this.shotStart.y, cy),
      w: Math.abs(cx - this.shotStart.x),
      h: Math.abs(cy - this.shotStart.y),
    });
  }

  /** Fim do arrasto de seleção. */
  protected shotUp(): void {
    this.shotStart = null;
  }

  /** Recorta a área selecionada → File PNG → anexa (staged). */
  protected confirmShot(): void {
    const sel = this.shotSel();
    const src = this.shotCanvas;
    const img = this.shotImg()?.nativeElement;
    if (!sel || !src || !img || sel.w < 4 || sel.h < 4) {
      return;
    }
    // Mapeia coords exibidas → coords reais da imagem capturada.
    const scaleX = src.width / img.clientWidth;
    const scaleY = src.height / img.clientHeight;
    const out = document.createElement('canvas');
    out.width = Math.max(1, Math.round(sel.w * scaleX));
    out.height = Math.max(1, Math.round(sel.h * scaleY));
    out
      .getContext('2d')
      ?.drawImage(
        src,
        sel.x * scaleX,
        sel.y * scaleY,
        sel.w * scaleX,
        sel.h * scaleY,
        0,
        0,
        out.width,
        out.height,
      );
    out.toBlob((blob) => {
      if (blob) {
        const file = new File([blob], `recorte-${Date.now()}.png`, {
          type: 'image/png',
        });
        this.stageFile(file);
      }
      this.closeShot();
    }, 'image/png');
  }

  /** Anexa a captura INTEIRA (aba/janela/tela toda), sem exigir recorte. */
  protected confirmShotFull(): void {
    const src = this.shotCanvas;
    if (!src) {
      return;
    }
    src.toBlob((blob) => {
      if (blob) {
        this.stageFile(
          new File([blob], `captura-${Date.now()}.png`, { type: 'image/png' }),
        );
      }
      this.closeShot();
    }, 'image/png');
  }

  protected cancelShot(): void {
    this.closeShot();
  }

  private closeShot(): void {
    this.shotOpen.set(false);
    this.shotSel.set(null);
    this.shotStart = null;
    this.shotCanvas = null;
    this.shotImgUrl.set('');
  }

  /**
   * Abre a câmera do aparelho (getUserMedia) e mostra o preview ao vivo. Pede
   * o stream ANTES de abrir o overlay (dispara a permissão), depois anexa ao
   * <video> assim que ele existe no DOM. Traseira por padrão.
   */
  protected async openCamera(): Promise<void> {
    const md = navigator.mediaDevices;
    if (!md?.getUserMedia) {
      return;
    }
    let stream: MediaStream;
    try {
      stream = await md.getUserMedia(this.camConstraints());
    } catch {
      this.warnHint('Não consegui acessar a câmera (permita o acesso).');
      return;
    }
    this.camStream = stream;
    this.camReady.set(false);
    this.camOpen.set(true);
    // O <video> só existe depois que o overlay renderiza.
    setTimeout(() => this.attachCamStream(), 0);
  }

  /** Liga o stream atual ao elemento <video> do preview e toca. */
  private attachCamStream(): void {
    const video = this.camVideo()?.nativeElement;
    if (!video || !this.camStream) {
      return;
    }
    video.srcObject = this.camStream;
    video.muted = true;
    video
      .play()
      .then(() => this.camReady.set(true))
      .catch(() => this.camReady.set(true));
  }

  /** Alterna entre câmera traseira/frontal (celular/tablet) e reabre o stream. */
  protected async flipCamera(): Promise<void> {
    this.camFacing = this.camFacing === 'environment' ? 'user' : 'environment';
    this.stopCamStream();
    this.camReady.set(false);
    try {
      this.camStream = await navigator.mediaDevices.getUserMedia(
        this.camConstraints(),
      );
      this.attachCamStream();
    } catch {
      this.warnHint('Só há uma câmera disponível.');
      this.closeCamera();
    }
  }

  /**
   * Restrições do stream: pede a MAIOR resolução que a câmera oferecer (ideal
   * 4K; ``ideal`` degrada sozinho pro que houver). Sem isso o preview costuma
   * vir baixo, e o frame capturado sai pequeno.
   */
  private camConstraints(): MediaStreamConstraints {
    return {
      video: {
        facingMode: this.camFacing,
        width: { ideal: 3840 },
        height: { ideal: 2160 },
      },
      audio: false,
    };
  }

  /** Tira a MELHOR foto possível → anexa (staged) e fecha. */
  protected async capturePhoto(): Promise<void> {
    const blob = await this.grabPhotoBlob();
    if (blob) {
      const ext = blob.type === 'image/png' ? 'png' : 'jpg';
      this.stageFile(
        new File([blob], `foto-${Date.now()}.${ext}`, {
          type: blob.type || 'image/jpeg',
        }),
      );
    }
    this.closeCamera();
  }

  /**
   * Melhor foto possível. Tenta ``ImageCapture.takePhoto()`` (resolução de FOTO
   * do sensor — geralmente maior que o preview; Chrome/Android). Sem suporte
   * (Safari/iOS), cai pro frame do ``<video>``. Exporta JPEG q0.92: qualidade
   * alta com arquivo pequeno (um PNG em 4K estouraria o limite de 10MB).
   */
  private async grabPhotoBlob(): Promise<Blob | null> {
    const track = this.camStream?.getVideoTracks()[0];
    const ImageCaptureCtor = (
      globalThis as unknown as {
        ImageCapture?: new (t: MediaStreamTrack) => { takePhoto(): Promise<Blob> };
      }
    ).ImageCapture;
    if (track && ImageCaptureCtor) {
      try {
        const still = await new ImageCaptureCtor(track).takePhoto();
        if (still && still.size > 0) {
          return still;
        }
      } catch {
        /* sem suporte real / negado → cai pro frame do vídeo */
      }
    }
    const video = this.camVideo()?.nativeElement;
    if (!video || !video.videoWidth) {
      return null;
    }
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d')?.drawImage(video, 0, 0, canvas.width, canvas.height);
    return new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, 'image/jpeg', 0.92),
    );
  }

  protected cancelCamera(): void {
    this.closeCamera();
  }

  private closeCamera(): void {
    this.stopCamStream();
    this.camOpen.set(false);
    this.camReady.set(false);
  }

  /** Encerra as tracks da câmera (libera o LED/permissão de uso). */
  private stopCamStream(): void {
    this.camStream?.getTracks().forEach((t) => t.stop());
    this.camStream = null;
  }

  /** Arrastou um arquivo sobre o compositor: realça a área e aceita o drop. */
  protected onDragOver(event: DragEvent): void {
    if (!event.dataTransfer?.types?.includes('Files')) {
      return; // arrastando texto/seleção — ignora
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
    if (!this.dragOver()) {
      this.dragOver.set(true);
    }
  }

  /** Saiu da área de drop (só limpa quando sai de fato do compositor). */
  protected onDragLeave(event: DragEvent): void {
    const to = event.relatedTarget as Node | null;
    const host = event.currentTarget as HTMLElement;
    if (to && host.contains(to)) {
      return; // ainda dentro (passou sobre um filho)
    }
    this.dragOver.set(false);
  }

  /** Soltou arquivos: stage de TODOS (espelha o seletor/colar). */
  protected onDrop(event: DragEvent): void {
    const files = event.dataTransfer?.files;
    if (!files || files.length === 0) {
      return;
    }
    event.preventDefault();
    this.dragOver.set(false);
    this.stageFiles(Array.from(files));
  }

  /** Remove UM anexo staged (revoga o preview daquele item). */
  protected removeFile(key: number): void {
    this.pendingItems.update((cur) =>
      cur.filter((it) => {
        if (it.key !== key) {
          return true;
        }
        if (it.url) {
          URL.revokeObjectURL(it.url);
        }
        return false;
      }),
    );
  }

  /** Descarta TODOS os anexos staged (não envia) e revoga os previews. */
  protected cancelFile(): void {
    this.revokePendingUrl();
  }

  /**
   * Faz upload de TODOS os arquivos staged junto com o texto digitado
   * (legenda), numa chamada só — imagens + texto chegam JUNTOS no agente (o
   * worker injeta tudo numa mensagem única). Limpa anexos e draft no sucesso.
   */
  protected sendFile(): void {
    const files = this.pendingItems().map((it) => it.file);
    const id = this.id();
    if (!files.length || !id || this.attaching()) {
      return;
    }
    const caption = this.draft().trim();
    this.attaching.set(true);
    this.showHint(files.length > 1 ? 'Enviando anexos…' : 'Enviando anexo…');
    this.markWorkingLocal();
    this.api
      .uploadFile(id, files, caption)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.attaching.set(false);
          this.revokePendingUrl();
          this.draft.set('');
          this.drafts.set(id, '');
          this.refreshBurst(); // mostra o anexo/legenda no pane assim que injeta
        },
        // Mantém os arquivos staged (e o texto) para o usuário tentar de novo.
        error: () => {
          this.attaching.set(false);
          this.clearHint(); // falhou → tira o aviso na hora
        },
      });
  }

  /** Revoga os object URLs dos previews e esvazia a lista de staged. */
  private revokePendingUrl(): void {
    for (const it of this.pendingItems()) {
      if (it.url) {
        URL.revokeObjectURL(it.url);
      }
    }
    this.pendingItems.set([]);
  }

  /**
   * Otimista: você enviou algo → vira "rodando" no ato. Cobre tanto a sessão
   * que AGUARDAVA você quanto a PARADA (o worker auto-retoma e injeta). O worker
   * confirma no Mongo; isto só tira o atraso visual.
   */
  private markWorkingLocal(): void {
    const s = this.session();
    if (!s || s.status === 'running') {
      return;
    }
    const revivable = [
      'waiting_input',
      'waiting_external',
      'stopped',
      'completed',
      'error',
      'detached',
    ];
    if (revivable.includes(s.status)) {
      this.session.set({ ...s, status: 'running' });
    }
  }

  protected pressKey(key: TerminalKey): void {
    const id = this.id();
    if (!id) {
      return;
    }
    // Scroll não é resposta; as demais teclas (enter/setas num prompt) são.
    if (key !== 'scroll-up' && key !== 'scroll-down') {
      this.markWorkingLocal();
    }
    // Fire-and-forget: NÃO bloqueia os botões durante o round-trip (no celular
    // ele tem centenas de ms via túnel). Bloquear deixava o keypad "morto" e
    // só piscando. Teclas são baratas/idempotentes → pode disparar à vontade.
    this.api
      .sendKey(id, key)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        // Burst escalonado: uma tecla num picker (ex.: escolher opção) redesenha
        // uns ms depois — um refresh único pegaria a tela antiga e o resto só
        // viria no push (~1s), dando a sensação de "não foi".
        next: () => this.refreshBurst(),
        error: () => {
          /* best-effort — ignora falha transitória */
        },
      });
  }

  /**
   * O recorder emite ``true`` ao COMEÇAR o upload. Ligamos o indicador de
   * "transcrevendo" e o mantemos até o texto aparecer na tela (ver effect do
   * espelho) ou um timeout de segurança — assim o usuário não fica no escuro
   * entre enviar o áudio e o texto surgir. O ``false`` (fim do upload) é
   * ignorado de propósito: a transcrição ainda está rolando no worker.
   */
  protected onAudioTranscribing(active: boolean): void {
    if (active) {
      this.showHint('Transcrevendo seu áudio…');
      this.markWorkingLocal(); // áudio enviado é resposta → inverte o fluxo
    }
  }

  /** Upload do áudio concluído — a transcrição segue no worker; mantém o aviso. */
  protected onAudioUploaded(): void {
    this.showHint('Transcrevendo seu áudio…');
    this.markWorkingLocal();
  }

  /** Liga o feedback "em trânsito" com um rótulo, até a tela mudar (ou 40s). */
  private showHint(label: string): void {
    this.actionHint.set(label);
    if (this.hintTimer) {
      clearTimeout(this.hintTimer);
    }
    // Rede de segurança: se a tela não mudar (ex.: nada injetado), some em 40s.
    this.hintTimer = setTimeout(() => this.actionHint.set(null), 40000);
  }

  /** Aviso curto no mesmo balão do hint (some sozinho em ~4s). */
  private warnHint(label: string): void {
    this.actionHint.set(label);
    if (this.hintTimer) {
      clearTimeout(this.hintTimer);
    }
    this.hintTimer = setTimeout(() => this.actionHint.set(null), 4000);
  }

  private clearHint(): void {
    if (this.actionHint() === null) {
      return;
    }
    this.actionHint.set(null);
    if (this.hintTimer) {
      clearTimeout(this.hintTimer);
      this.hintTimer = null;
    }
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
  private refreshScreen(forceBottom = false): void {
    const id = this.id();
    if (!id) {
      return;
    }
    this.api
      .getScreen(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (resp) => {
          // Captura ANTES do update: o usuário estava colado no fim? (forceBottom
          // ignora isso — ex.: ao voltar do histórico, sempre desce pro fim.)
          const next = resp.text ?? '';
          if (next !== this.screen()) {
            this.clearHint(); // conteúdo chegou via poll → some o aviso
          }
          this.stickToBottom = forceBottom || this.isAtBottom();
          this.screen.set(next);
          if (forceBottom) {
            queueMicrotask(() => this.scrollToBottom());
          }
        },
        error: () => {
          /* poll é best-effort; ignora erro transitório */
        },
      });
  }

  /** Abre/fecha o teclado de navegação e lembra por sessão. */
  protected toggleKeypad(): void {
    const open = !this.keypadOpen();
    this.keypadOpen.set(open);
    this.prefs.patch(this.id() ?? '', { keypadOpen: open });
  }

  /** Aumenta/diminui a fonte do terminal (clamp 9–22px) e persiste no aparelho. */
  protected bumpFont(delta: number): void {
    const next = Math.min(22, Math.max(9, Math.round((this.termFont() + delta) * 2) / 2));
    this.termFont.set(next);
    // Global (sf.term.font) = default p/ sessões novas; por-sessão = o que fica.
    this.prefs.patch(this.id() ?? '', { termFont: next });
    try {
      localStorage.setItem('sf.term.font', String(next));
    } catch {
      /* storage indisponível — silencioso */
    }
    // Fonte mudou → muda quantas colunas/linhas cabem → reajusta o pane.
    this.scheduleTermResize();
  }

  /** Agenda (debounce 350ms) o ajuste do tamanho do pane à área do terminal. */
  private scheduleTermResize(): void {
    if (this.resizeTimer) {
      clearTimeout(this.resizeTimer);
    }
    this.resizeTimer = setTimeout(() => this.syncTermSize(), 350);
  }

  /**
   * Mede quantas colunas/linhas cabem na área do terminal (fonte monoespaçada
   * atual) e, se mudou, manda o tmux redimensionar — o agente reflui pra usar a
   * largura toda. Best-effort.
   */
  private syncTermSize(): void {
    const el = this.termEl()?.nativeElement;
    const id = this.id();
    if (!el || !id || this.bufMode()) {
      return;
    }
    const cw = this.measureCharWidth();
    if (!cw) {
      return;
    }
    const lh = this.termFont() * 1.7; // line-height do .term
    // padding do .term: 14px (vert) / 16px (horiz) → 32 / 28.
    // Mínimo baixo (20): num celular com fonte grande, forçar 40 colunas deixa
    // o pane MAIS LARGO que a tela → as linhas re-quebram e a barra de status do
    // tmux fica feia em 2 linhas. Deixar casar com o que realmente cabe evita isso.
    const cols = Math.max(20, Math.floor((el.clientWidth - 32) / cw));
    const rows = Math.max(10, Math.floor((el.clientHeight - 28) / lh));
    if (cols === this.lastCols && rows === this.lastRows) {
      return;
    }
    this.lastCols = cols;
    this.lastRows = rows;
    this.api
      .resizeSession(id, cols, rows)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({ next: () => this.refreshScreen(), error: () => {} });
  }

  /** Largura de UM caractere na fonte monoespaçada atual do terminal (px). */
  private measureCharWidth(): number {
    const host = this.termEl()?.nativeElement;
    if (!host) {
      return 0;
    }
    const probe = document.createElement('span');
    probe.className = 'mono';
    probe.style.cssText =
      'position:absolute;visibility:hidden;white-space:pre;pointer-events:none;';
    probe.style.fontSize = `${this.termFont()}px`;
    probe.textContent = '0'.repeat(100);
    host.appendChild(probe);
    const w = probe.getBoundingClientRect().width / 100;
    probe.remove();
    return w;
  }

  /**
   * Scroll do mouse/touchpad SOBRE o terminal: enquanto o container pode rolar
   * localmente naquela direção, deixa a rolagem nativa. Ao bater no LIMITE
   * (topo/fim), manda o scroll pro AGENTE (igual aos botões ▲▼) — dá a sensação
   * de scroll infinito, como num terminal de verdade. Throttle p/ não floodar.
   */
  protected onTermWheel(ev: WheelEvent): void {
    const el = this.termEl()?.nativeElement;
    if (!el || Math.abs(ev.deltaY) < 1) {
      return;
    }
    const up = ev.deltaY < 0;
    const canScrollUp = el.scrollTop > 0;
    const canScrollDown = el.scrollTop + el.clientHeight < el.scrollHeight - 1;
    if ((up && canScrollUp) || (!up && canScrollDown)) {
      return; // ainda dá pra rolar dentro do container → nativo
    }
    // No limite: manda o scroll pro agente (ele guarda o próprio scrollback).
    this.agentScroll(up ? 'up' : 'down');
  }

  /** Início do toque no terminal — guarda o Y p/ medir o arrasto (tablet). */
  protected onTermTouchStart(ev: TouchEvent): void {
    this.touchStartY = ev.touches[0]?.clientY ?? 0;
  }

  /**
   * Arrasto de toque NO LIMITE do terminal (tablet/celular): no topo arrastando
   * pra baixo → traz conteúdo anterior (igual ▲); no fim arrastando pra cima →
   * mais recente (▼). Como o `wheel` não dispara no toque, replicamos aqui.
   */
  protected onTermTouchMove(ev: TouchEvent): void {
    const el = this.termEl()?.nativeElement;
    if (!el) {
      return;
    }
    const y = ev.touches[0]?.clientY ?? 0;
    const dy = y - this.touchStartY; // >0 = dedo descendo (revela conteúdo acima)
    const atTop = el.scrollTop <= 0;
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 1;
    if (atTop && dy > 24) {
      this.touchStartY = y;
      this.agentScroll('up');
    } else if (atBottom && dy < -24) {
      this.touchStartY = y;
      this.agentScroll('down');
    }
  }

  /** Manda o scroll pro agente (▲/▼) com throttle — compartilhado por wheel e toque. */
  private agentScroll(dir: 'up' | 'down'): void {
    const now = Date.now();
    // Throttle folgado (150ms): a roda dispara MUITOS eventos por gesto; junto do
    // burst de 2 notches por comando no worker, um flickzinho pulava dezenas de
    // linhas no agente. Espaçar mais os comandos dá controle fino do histórico.
    if (now - this.lastWheelAt < 150) {
      return;
    }
    this.lastWheelAt = now;
    if (dir === 'up') {
      this.showLivePill.set(true); // rolou o agente p/ cima → oferece "↓ ao vivo"
    }
    this.scrollTerm(dir);
  }

  /** Manda o agente pular pro FIM (Ctrl+End) — ele guarda o próprio scroll. */
  private jumpAgentToBottom(): void {
    const id = this.id();
    if (!id) {
      return;
    }
    this.api
      .sendKey(id, 'scroll-bottom')
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({ next: () => this.refreshScreen(true), error: () => {} });
  }

  /**
   * Selecionou texto no terminal → copia pro clipboard e avisa ("Copiado").
   * Disparado no fim da seleção (mouseup/touchend). Ignora seleção vazia ou
   * fora do terminal. Best-effort (clipboard exige contexto seguro + gesto).
   */
  protected onTermSelect(): void {
    const sel = typeof window !== 'undefined' ? window.getSelection?.() : null;
    const text = sel?.toString() ?? '';
    if (!text.trim()) {
      return;
    }
    const el = this.termEl()?.nativeElement;
    if (el && sel?.anchorNode && !el.contains(sel.anchorNode)) {
      return; // seleção começou fora do terminal
    }
    navigator.clipboard
      ?.writeText(text)
      .then(() => this.flashCopied())
      .catch(() => {
        /* clipboard indisponível — silencioso */
      });
  }

  /** Mostra o toast "Copiado" e agenda o sumiço. */
  private flashCopied(): void {
    this.copied.set(true);
    if (this.copyTimer) {
      clearTimeout(this.copyTimer);
    }
    this.copyTimer = setTimeout(() => this.copied.set(false), 1500);
  }

  /**
   * Botões ▲/▼: mandam evento de RODA DO MOUSE pro AGENTE, que redesenha o
   * próprio histórico interno — igual ao touchpad no Mac. (TUIs de tela
   * alternada, ex.: Claude Code, guardam o scrollback dentro de si, não no
   * tmux; por isso não dá pra rolar via tmux, só mandando o evento direto.)
   */
  protected scrollTerm(dir: 'up' | 'down'): void {
    const id = this.id();
    if (!id) {
      return;
    }
    this.api
      .sendKey(id, dir === 'up' ? 'scroll-up' : 'scroll-down')
      // O agente NÃO redesenha na hora que o comando chega — leva algumas dezenas
      // de ms. Um único refresh imediato pega a tela ANTIGA, e o próximo update só
      // viria no push do worker (a cada ~1s), dando a sensação de "travado". Por
      // isso disparamos um BURST curto de refreshes pra capturar o redraw cedo.
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({ next: () => this.refreshBurst(), error: () => {} });
  }

  /** Timers do burst de refresh pós-scroll — cancelados a cada novo scroll. */
  private refreshBurstTimers: ReturnType<typeof setTimeout>[] = [];

  /**
   * Dispara vários {@link refreshScreen} escalonados após um scroll no agente,
   * pra pegar o redraw assim que ele acontece (sem esperar o push de ~1s). Cancela
   * o burst anterior — se o usuário rola em sequência, só o último importa.
   */
  private refreshBurst(): void {
    for (const t of this.refreshBurstTimers) {
      clearTimeout(t);
    }
    this.refreshBurstTimers = [];
    this.refreshScreen(); // já: pode pegar a tela nova se o agente foi rápido
    // O comando de scroll vai por fila e o espelho só é regravado no ciclo do
    // worker (~0,6s). Cobrimos até ~2,5s p/ o conteúdo novo aparecer com certeza.
    for (const delay of [250, 600, 1000, 1600, 2400]) {
      this.refreshBurstTimers.push(setTimeout(() => this.refreshScreen(), delay));
    }
  }

  /** Snap pro fim e retoma o "grudar no fim" do modo ao vivo (pill ↓). */
  protected snapToLive(): void {
    if (this.bufMode()) {
      this.exitBuffer(); // sai do buffer de scrollback e volta ao espelho ao vivo
      return;
    }
    this.stickToBottom = true;
    this.scrollToBottom();
    this.showLivePill.set(false);
    this.jumpAgentToBottom(); // o agente pode estar rolado p/ cima → traz pro fim
  }

  /**
   * ENTRA no buffer de scrollback: semeia com a tela ao vivo, congela e busca a
   * primeira página pra cima. A partir daí a rolagem é NATIVA (lisa) e só pede
   * mais história ao chegar no topo. Sai pela pill "↓ ao vivo" ({@link snapToLive}).
   */
  private enterBuffer(): void {
    const cur = this.screen();
    if (!cur || this.bufMode()) {
      return;
    }
    this.bufLines = cur.replace(/\s+$/, '').split('\n');
    this.bufText.set(this.bufLines.join('\n'));
    this.bufCount.set(this.bufLines.length);
    this.bufExhausted = false;
    this.bufExhaustedUi.set(false);
    this.bufEmptyStreak = 0;
    this.bufBusy = false;
    this.bufMode.set(true);
    this.showLivePill.set(true);
    // Desce pro fim (contínuo com o ao vivo) e já traz uma página de história.
    this.bufAdjusting = true;
    queueMicrotask(() => {
      this.scrollToBottom();
      this.bufAdjusting = false;
      this.loadMoreUp(true); // 1ª leva revela o topo (mostra o histórico surgindo)
    });
  }

  /**
   * Busca a próxima "página" de história: pede o scroll pro agente e PREPENDA só
   * as linhas novas (costura por overlap), preservando a posição de leitura.
   *
   * IMPORTANTE: `getScreen` NÃO captura na hora — lê o espelho que o worker grava
   * no ciclo dele (~1s). Além disso o `scroll-up` vai por fila (assíncrono). Por
   * isso, após mandar o scroll, fazemos POLL do `getScreen` até a tela refletir o
   * novo conteúdo (ou desistir). É o que faltava — antes capturávamos cedo demais
   * e o frame vinha igual (nada a prepender).
   */
  private loadMoreUp(revealTop = false): void {
    const id = this.id();
    if (this.bufBusy || this.bufExhausted || !this.bufMode() || !id) {
      return;
    }
    if (this.bufLines.length >= DetalheComponent.BUF_MAX_LINES) {
      this.bufExhausted = true;
      return;
    }
    this.bufBusy = true;
    this.bufLoading.set(true);
    const el = this.termEl()?.nativeElement;
    const beforeH = el?.scrollHeight ?? 0;
    const beforeTop = el?.scrollTop ?? 0;
    this.api
      .sendKey(id, 'scroll-up')
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => this.captureMoreUp(id, beforeH, beforeTop, 0, revealTop),
        error: () => {
          this.bufBusy = false;
          this.bufLoading.set(false);
        },
      });
  }

  /** Nº de tentativas de captura pós-scroll (worker grava a cada ~0,6-1s +
   * latência da fila; ~10×500ms ≈ 5s de janela p/ o espelho refletir). */
  private static readonly BUF_CAPTURE_TRIES = 10;

  /**
   * Poll do espelho após um scroll-up: tenta costurar; se o frame ainda não
   * mudou (worker não gravou ainda), espera e tenta de novo até
   * {@link BUF_CAPTURE_TRIES}. Só então conta como "vazio".
   */
  private captureMoreUp(
    id: string,
    beforeH: number,
    beforeTop: number,
    attempt: number,
    revealTop: boolean,
  ): void {
    if (!this.bufMode()) {
      this.bufBusy = false;
      this.bufLoading.set(false);
      return;
    }
    setTimeout(() => {
      this.api
        .getScreen(id)
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe({
          next: (resp) => {
            const frame = (resp.text ?? '').split('\n');
            const added = stitchScrollback(this.bufLines, frame);
            if (added && added.length > 0) {
              this.bufEmptyStreak = 0;
              this.bufLines = [...added, ...this.bufLines];
              if (this.bufLines.length > DetalheComponent.BUF_MAX_LINES) {
                this.bufLines = this.bufLines.slice(
                  0,
                  DetalheComponent.BUF_MAX_LINES,
                );
              }
              this.bufText.set(this.bufLines.join('\n'));
              this.bufCount.set(this.bufLines.length);
              this.bufAdjusting = true;
              queueMicrotask(() => {
                const el2 = this.termEl()?.nativeElement;
                if (el2) {
                  // 1ª leva (ao ENTRAR): revela o topo p/ o usuário VER o conteúdo
                  // antigo aparecer (senão carrega acima da dobra e parece travado).
                  // Demais levas: preserva a posição de leitura (cresceu no topo).
                  el2.scrollTop = revealTop
                    ? 0
                    : beforeTop + (el2.scrollHeight - beforeH);
                }
                this.bufAdjusting = false;
              });
              this.bufBusy = false;
              this.bufLoading.set(false);
            } else if (attempt + 1 < DetalheComponent.BUF_CAPTURE_TRIES) {
              // Tela ainda não refletiu o scroll — espera o próximo ciclo do worker.
              this.captureMoreUp(id, beforeH, beforeTop, attempt + 1, revealTop);
            } else {
              // Topo real ou sem mudança após ~5s: conta vazio (esgota em 2).
              this.bufEmptyStreak++;
              if (this.bufEmptyStreak >= 2) {
                this.bufExhausted = true;
                this.bufExhaustedUi.set(true);
              }
              this.bufBusy = false;
              this.bufLoading.set(false);
            }
          },
          error: () => {
            this.bufBusy = false;
            this.bufLoading.set(false);
          },
        });
    }, 500);
  }

  /** SAI do buffer: volta ao espelho ao vivo e manda o agente pro fim. */
  private exitBuffer(): void {
    this.bufMode.set(false);
    this.bufLines = [];
    this.bufText.set('');
    this.bufCount.set(0);
    this.bufExhausted = false;
    this.bufExhaustedUi.set(false);
    this.bufEmptyStreak = 0;
    this.bufBusy = false;
    this.bufLoading.set(false);
    this.showLivePill.set(false);
    this.stickToBottom = true;
    this.jumpAgentToBottom(); // Ctrl+End no agente + refreshScreen(true)
  }

  /** Atualiza a pill ↓ ao vivo conforme o usuário rola (só no modo ao vivo). */
  protected onTermScroll(): void {
    if (this.bufMode()) {
      // Rolagem nativa dentro do buffer: perto do topo, busca mais histórico.
      if (this.bufAdjusting) {
        return; // ajuste programático de scrollTop — não dispara load
      }
      const el = this.termEl()?.nativeElement;
      if (el && el.scrollTop <= 40) {
        this.loadMoreUp();
      }
      return;
    }
    this.showLivePill.set(!this.isAtBottom());
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

  /**
   * Foca o campo de mensagem ao abrir a sessão (pronto pra digitar). Tenta em
   * 2 tempos porque o input pode não estar renderizado no instante da navegação.
   * Best-effort: em celular o teclado só sobe com gesto do usuário — não força
   * nada nem incomoda; no desktop/tablet já deixa o cursor lá.
   */
  private focusMessageInput(): void {
    const tryFocus = () => {
      const el = this.msgInput()?.nativeElement;
      if (el && !el.disabled) {
        el.focus();
        return true;
      }
      return false;
    };
    queueMicrotask(() => {
      if (!tryFocus()) {
        setTimeout(tryFocus, 250);
      }
    });
  }
}

/**
 * Remove linhas EM BRANCO do topo e do fim do espelho — o capture-pane vem com
 * linhas vazias preenchendo a altura do pane (o agente desenha o conteúdo só
 * numa parte), o que deixava um vazio grande no terminal. Considera "branca" a
 * linha vazia depois de tirar os códigos ANSI. As linhas internas são mantidas.
 */
/**
 * Costura um frame recém-capturado (após rolar o agente pra cima) no topo do
 * buffer de scrollback, retornando SÓ as linhas NOVAS (mais antigas) a prepender.
 *
 * Após um scroll-up, o frame contém algumas linhas novas no topo seguidas de um
 * trecho que COINCIDE com o começo atual do buffer. Achamos esse overlap (um
 * "run" de ≥4 linhas iguais, com ≥2 não-vazias, p/ não casar em linhas em branco)
 * e devolvemos o prefixo do frame antes dele. Sem overlap (topo atingido ou tela
 * mudou) → `null`, e quem chama marca esgotado (evita duplicar conteúdo).
 *
 * O casamento ignora cor (SGR) e espaços à direita — o buffer guarda a versão
 * colorida original.
 */
function stitchScrollback(buffer: string[], frame: string[]): string[] | null {
  // eslint-disable-next-line no-control-regex
  const sgr = /\x1b\[[0-9;]*m/g;
  const norm = (s: string): string => s.replace(sgr, '').replace(/\s+$/, '');
  const bn = buffer.map(norm);
  const fn = frame.map(norm);
  for (let k = 0; k < fn.length; k++) {
    let match = 0;
    let substantial = 0; // linhas casadas "distintivas" (≥6 chars) — âncora forte
    while (
      k + match < fn.length &&
      match < bn.length &&
      fn[k + match] === bn[match]
    ) {
      if (fn[k + match].length >= 6) {
        substantial++;
      }
      match++;
    }
    // Aceita overlap MENOR (panes de celular são baixos, o passo do scroll pode
    // deixar poucas linhas em comum), desde que ancore em algo distintivo:
    // ≥3 linhas casadas com ≥1 substancial, OU ≥2 substanciais (bem seguro).
    if ((match >= 3 && substantial >= 1) || substantial >= 2) {
      return frame.slice(0, k); // linhas novas (mais antigas) a prepender
    }
  }
  return null; // sem overlap
}

/** Lê a preferência de modo foco (default desligado). */
function readFocusMode(): boolean {
  try {
    return localStorage.getItem('sf.focus') === '1';
  } catch {
    return false;
  }
}

/** Lê a proporção salva do split (painel A), com fallback 0.5 e clamp 0.2–0.8. */
function readSplitRatio(): number {
  try {
    const v = Number(localStorage.getItem('sf.split.ratio'));
    return v >= 0.2 && v <= 0.8 ? v : 0.5;
  } catch {
    return 0.5;
  }
}

/** Lê a fonte do terminal salva (px), com fallback 12.5 e clamp 9–22. */
function readTermFont(): number {
  try {
    const v = Number(localStorage.getItem('sf.term.font'));
    return v >= 9 && v <= 22 ? v : 12.5;
  } catch {
    return 12.5;
  }
}
