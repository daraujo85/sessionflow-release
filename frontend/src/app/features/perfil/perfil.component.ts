import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  OnDestroy,
  OnInit,
  computed,
  effect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { Router } from '@angular/router';
import { ApiService } from '../../core/api.service';
import { SseService } from '../../core/sse.service';
import { AuthService } from '../../core/auth.service';
import { PwaInstallService } from '../../core/pwa-install.service';
import { NotifyService } from '../../core/notify.service';
import { EventCuesService, CueMode } from '../../core/event-cues.service';
import { JarvisAudioService } from '../../core/jarvis-audio.service';
import { WorkersStore } from '../../core/workers-store';
import {
  Session,
  UsageInfo,
  WorkerStatus,
} from '../../core/models';
import {
  FLEET_ACTIVE_STATUSES,
  FLEET_FILTER_BANDS,
  FleetFilter,
  LoadBand,
  activeSessionsByHost,
  countBands,
  cpuPct,
  fleetView,
  loadBand,
  memUsedPct,
  roundPct,
} from '../../core/fleet';

/** Lado máximo (px) para onde a foto é redimensionada antes de enviar. */
const PHOTO_MAX_SIDE = 256;


const BAND_LABELS: Record<LoadBand, string> = {
  free: 'livre',
  busy: 'pegada',
  overloaded: 'sobrecarregada',
  offline: 'offline',
  unknown: 'sem métricas',
};

/** Cor da barra/chip por faixa de carga. */
const BAND_COLORS: Record<LoadBand, string> = {
  free: '#34D399',
  busy: '#FBBF24',
  overloaded: '#F87171',
  offline: '#7A8090',
  unknown: '#7A8090',
};

/**
 * Profile screen ("Perfil"). Pixel-for-pixel with the mockup (showPerfil).
 * The Worker status card is honestly derived from the live SSE connection
 * (there is no dedicated Worker endpoint yet); host/uptime stay "—" rather
 * than being fabricated. Stats come from listSessions. The "Sair" action is
 * a placeholder for now.
 */
@Component({
  selector: 'sf-perfil',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="sf-perfil">
      <div class="sf-title-row">
        <span class="sf-title">Perfil</span>
      </div>

      <!-- Identity -->
      <div class="sf-identity">
        <button
          type="button"
          class="sf-avatar"
          (click)="pickPhoto()"
          [attr.aria-label]="photo() ? 'Trocar foto de perfil' : 'Adicionar foto de perfil'"
        >
          @if (photo()) {
            <img class="sf-avatar-img" [src]="photo()" alt="" />
          } @else {
            <span aria-hidden="true">{{ displayName().charAt(0) || '?' }}</span>
          }
          <span class="sf-avatar-cam" aria-hidden="true">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" />
              <circle cx="12" cy="13" r="4" />
            </svg>
          </span>
        </button>
        <input
          #fileInput
          type="file"
          accept="image/*"
          hidden
          (change)="onPhotoSelected($event)"
        />
        <div class="sf-identity-body">
          <div class="sf-name">{{ displayName() }}</div>
          <div class="sf-role">Operador · {{ email() || 'sessionflow.local' }}</div>
          @if (photo()) {
            <button type="button" class="sf-photo-remove" (click)="removePhoto()">
              Remover foto
            </button>
          }
        </div>
      </div>

      <!-- Total somado de todos os hosts online (só com >1 host). -->
      @if (fleetTotals(); as t) {
        <div class="sf-fleet-total">
          <div class="sf-fleet-title">Total · {{ t.online }}/{{ t.hosts }} hosts online</div>
          <div class="sf-hw-stats">
            <span class="sf-hw-stat">🧩 {{ t.cores }} cores</span>
            <span class="sf-hw-stat">💾 {{ t.ramTotal.toFixed(1) }}GB RAM</span>
            @if (t.ramAvail !== null) {
              <span class="sf-hw-stat">🧠 {{ t.ramAvail.toFixed(1) }}GB livre</span>
            }
            @if (t.cpuAvg !== null) {
              <span class="sf-hw-stat">⚡ CPU média {{ t.cpuAvg }}%</span>
            }
            @if (t.gpus) {
              <span class="sf-hw-stat">🎮 {{ t.gpus }} GPU</span>
            }
            <span class="sf-hw-stat">💽 {{ t.disk >= 1000 ? (t.disk / 1000).toFixed(1) + 'TB' : t.disk + 'GB' }}</span>
          </div>
        </div>
      }

      <!-- Painel de máquinas: chips de filtro por faixa de CPU (só com >1 host). -->
      @if (fleet().length > 1) {
        <div class="sf-fleet-chips" role="group" aria-label="Filtrar máquinas por carga">
          @for (b of filterBands; track b) {
            <button
              type="button"
              class="sf-fleet-chip"
              [class.sf-on]="fleetFilter() === b"
              [style.--band]="bandColor(b)"
              [attr.aria-pressed]="fleetFilter() === b"
              (click)="toggleFleetFilter(b)"
            >
              <span class="sf-chip-dot"></span>{{ bandLabel(b) }} · {{ bandCounts()[b] }}
            </button>
          }
        </div>
      }

      @for (w of fleetHosts(); track w.host_id ?? (w.hostname ?? '') + $index) {
        <div
          class="sf-worker sf-host"
          [class.sf-host-primary]="isPrimary(w)"
          [style.--band]="bandColor(band(w))"
        >
          <span
            class="sf-worker-dot"
            [class.sf-pulse]="isUp(w)"
            [style.background]="isUp(w) ? '#34D399' : '#7A8090'"
          ></span>
          <div class="sf-worker-body">
            @if (editingHostId() === w.host_id && w.host_id) {
              <span class="sf-worker-edit-row">
                <input
                  #editEmoji
                  class="sf-worker-edit-emoji mono"
                  [value]="editEmojiValue()"
                  (input)="editEmojiValue.set(editEmoji.value)"
                  (keydown.enter)="saveEditName(w.host_id!)"
                  (keydown.escape)="cancelEditName()"
                  placeholder="🍎"
                  maxlength="8"
                />
                <input
                  #editInput
                  class="sf-worker-edit-input mono"
                  [value]="editNameValue()"
                  (input)="editNameValue.set(editInput.value)"
                  (keydown.enter)="saveEditName(w.host_id!)"
                  (keydown.escape)="cancelEditName()"
                  placeholder="Nome deste host"
                  autofocus
                />
              </span>
              <span class="sf-worker-edit-acts">
                <button type="button" (click)="saveEditName(w.host_id!)">Salvar</button>
                <button type="button" (click)="cancelEditName()">Cancelar</button>
              </span>
            } @else {
              <div class="sf-worker-label">
                <span class="sf-worker-name">
                  {{ w.emoji ? w.emoji + ' ' : '' }}{{ w.display_name || w.hostname || '—' }}
                </span>
                @if (isPrimary(w)) {
                  <span class="sf-primary-tag" title="Host principal (worker deste painel)">principal</span>
                }
                @if (w.host_id) {
                  <button
                    type="button"
                    class="sf-worker-edit-btn"
                    (click)="startEditName(w.host_id, w.display_name ?? null, w.emoji ?? null)"
                    aria-label="Renomear/trocar emoji deste host"
                    title="Renomear/trocar emoji deste host"
                  >✎</button>
                }
                @if (isColab(w) && w.host_id) {
                  <span class="sf-colab-actions">
                    @if (!w.online) {
                      <button
                        type="button"
                        class="sf-colab-btn sf-colab-play"
                        [disabled]="actionLoadingHostId() === w.host_id"
                        (click)="startColab(w, $event)"
                        title="Ligar máquina no Google Colab"
                      >
                        {{ actionLoadingHostId() === w.host_id ? '⏳' : '▶' }} Ligar
                      </button>
                    } @else {
                      <button
                        type="button"
                        class="sf-colab-btn sf-colab-stop"
                        [disabled]="actionLoadingHostId() === w.host_id"
                        (click)="stopColab(w, $event)"
                        title="Desligar máquina Google Colab"
                      >
                        {{ actionLoadingHostId() === w.host_id ? '⏳' : '⏹' }} Desligar
                      </button>
                    }
                    <a
                      class="sf-colab-link"
                      [href]="colabNotebookUrl(w)"
                      target="_blank"
                      rel="noopener noreferrer"
                      (click)="$event.stopPropagation()"
                      title="Abrir notebook no Google Colab"
                    >↗ Colab</a>
                  </span>
                }
              </div>
            }

            <!-- Card fechado: só barras + chip de sessões; o resto no expand. -->
            <button
              type="button"
              class="sf-host-toggle"
              [disabled]="!w.host_id"
              (click)="toggleHardware(w.host_id ?? null)"
              [attr.aria-expanded]="w.host_id ? expandedHostId() === w.host_id : null"
              aria-label="Ver detalhes desta máquina"
            >
              @if (w.online && cpu(w) != null) {
                <span class="sf-bar-row">
                  <span class="sf-bar-label">CPU</span>
                  <span class="sf-bar"><span class="sf-bar-fill" [style.width.%]="cpu(w)"></span></span>
                  <span class="sf-bar-val">{{ cpu(w) }}%</span>
                </span>
                @if (memPct(w) != null) {
                  <span class="sf-bar-row sf-bar-ram">
                    <span class="sf-bar-label">RAM</span>
                    <span class="sf-bar"><span class="sf-bar-fill" [style.width.%]="memPct(w)"></span></span>
                    <span class="sf-bar-val">{{ memPct(w) }}%</span>
                  </span>
                }
              } @else {
                <span class="sf-host-nometrics">{{ w.online ? 'sem métricas' : 'sem heartbeat' }}</span>
              }
              <span class="sf-host-foot">
                <span class="sf-sess-chip" [class.sf-zero]="!sessionsOf(w).length">
                  {{ sessionsOf(w).length }} {{ sessionsOf(w).length === 1 ? 'sessão' : 'sessões' }}
                </span>
                @if (w.host_id) {
                  <span class="sf-hw-caret">{{ expandedHostId() === w.host_id ? '▲' : '▼' }}</span>
                }
              </span>
            </button>

            @if (expandedHostId() === w.host_id && w.host_id) {
              <div class="sf-hw-detail">
                <div>{{ w.platform ?? '—' }} · uptime {{ formatUptimeFor(w) }}</div>
                @if (sessionsOf(w).length) {
                  <div class="sf-ollama-title">Sessões ativas</div>
                  <div class="sf-sess-names">
                    @for (n of sessionsOf(w).slice(0, maxSessionNames); track $index) {
                      <span>{{ n }}</span>
                    }
                    @if (sessionsOf(w).length > maxSessionNames) {
                      <span class="sf-more">+{{ sessionsOf(w).length - maxSessionNames }}</span>
                    }
                  </div>
                }
                @if (agentsOf(w).length) {
                  <div class="sf-ollama-title">Agents</div>
                  <div>{{ agentsOf(w).join(', ') }}</div>
                }
                @if (w.metrics?.mem_avail_gb != null) {
                  <div>RAM livre: {{ w.metrics?.mem_avail_gb?.toFixed(1) }}{{ w.metrics?.mem_total_gb ? ' / ' + w.metrics?.mem_total_gb?.toFixed(1) : '' }} GB</div>
                }
                @if (w.hardware; as hw) {
                  <div class="sf-ollama-title">Hardware</div>
                  <div>CPU: {{ hw.cpu_model || '—' }}{{ hw.cpu_cores ? ' (' + hw.cpu_cores + ' núcleos)' : '' }}</div>
                  <div>RAM: {{ hw.ram_total_gb ? hw.ram_total_gb + ' GB' : '—' }}</div>
                  <div>GPU: {{ hw.gpu || 'não detectada' }}</div>
                  <div>
                    SO: {{ hw.os_detail?.distro || '—' }}
                    @if (hw.os_detail?.host_os) {
                      — rodando em {{ hw.os_detail?.host_os }}
                    }
                  </div>
                  @for (d of hw.disks || []; track d.mount) {
                    <div>Disco {{ d.mount }}: {{ d.used_gb }} / {{ d.total_gb }} GB usados</div>
                  }
                }
                @if (w.ollama_models?.length) {
                  <div class="sf-ollama-title">Ollama</div>
                  @for (m of w.ollama_models; track m.name) {
                    <div class="sf-ollama-model">
                      @if (m.loaded) {
                        <span class="sf-ollama-dot" title="Carregado agora"></span>
                      }
                      {{ m.name }} · {{ m.size_gb }} GB
                    </div>
                  }
                }
              </div>
            }
          </div>
          <span
            class="sf-worker-pill"
            [style.color]="isUp(w) ? '#34D399' : '#8A90A0'"
            [style.background]="isUp(w) ? 'rgba(52,211,153,.14)' : 'rgba(138,144,160,.14)'"
          >
            {{ isUp(w) ? 'online' : 'offline' }}
          </span>
        </div>
      } @empty {
        @if (fleetFilter()) {
          <div class="sf-fleet-empty">Nenhuma máquina nessa faixa.</div>
        } @else {
          <div class="sf-worker">
            <span class="sf-worker-dot" [style.background]="connected() ? '#34D399' : '#7A8090'"></span>
            <div class="sf-worker-body">
              <div class="sf-worker-label"><span class="sf-worker-name">{{ workerTitle() }}</span></div>
              <div class="sf-worker-meta">{{ workerMeta() }}</div>
            </div>
          </div>
        }
      }
      <!-- Stats -->
      <div class="sf-stats">
        <div class="sf-stat">
          <div class="sf-stat-value">{{ sessionsToday() }}</div>
          <div class="sf-stat-label">Sessões hoje</div>
        </div>
        <div class="sf-stat">
          <div class="sf-stat-value sf-stat-accent">{{ activeNow() }}</div>
          <div class="sf-stat-label">Ativas agora</div>
        </div>
      </div>

      <!-- Limites de uso (reais) -->
      <div class="sf-limits">
        <div class="sf-limits-head">Limites de uso</div>
        @if (claudeLimits(); as cl) {
          <div class="sf-limit">
            <div class="sf-limit-row">
              <span class="sf-limit-name">Claude · sessão (5h)</span>
              <span class="sf-limit-pct">{{ fmtPct(cl.session_pct) }}</span>
            </div>
            <div class="sf-limit-bar">
              <span [style.width.%]="cl.session_pct ?? 0"></span>
            </div>
          </div>
          <div class="sf-limit">
            <div class="sf-limit-row">
              <span class="sf-limit-name">Claude · semana</span>
              <span class="sf-limit-pct">{{ fmtPct(cl.week_pct) }}</span>
            </div>
            <div class="sf-limit-bar">
              <span [style.width.%]="cl.week_pct ?? 0"></span>
            </div>
          </div>
        } @else {
          <div class="sf-limit-empty">Sem dados de uso ainda.</div>
        }
        <div class="sf-limit-note">
          Gemini, Codex e OpenCode não expõem uso — sem dados disponíveis.
        </div>
      </div>

      <!-- Áudio do JARVIS: volume (local do aparelho) + modo de voz/efeito
           por host (Perfil > Áudio). Cada linha de host: nome em cima
           (truncando se precisar), controles embaixo (select + toggle +
           testar) — evita amontoar tudo numa linha só e quebrar feio. -->
      <div class="sf-audio">
        <div class="sf-audio-head">Áudio (JARVIS)</div>
        <label class="sf-audio-volume">
          <span>Volume</span>
          <input
            type="range"
            min="0"
            max="100"
            [value]="jarvisAudio.volume()"
            (input)="onVolumeInput($event)"
          />
          <span class="sf-audio-volume-val">{{ jarvisAudio.volume() }}%</span>
        </label>

        <!-- Liga/desliga o JARVIS (voz) globalmente. Os 2 toggles ficam aqui
             (na seção de Áudio) em vez de duplicar com o grupo "Áudio (JARVIS)"
             da lista geral — volume + engine + comportamento de voz, tudo junto. -->
        <label class="sf-audio-toggle">
          <input
            type="checkbox"
            [checked]="jarvisAll()"
            (change)="onJarvisAllToggle($event)"
          />
          <span class="sf-audio-toggle-text">
            <strong>JARVIS — resumo falado</strong>
            <small>Falar resumo do que a sessão fez, em TODAS as sessões.</small>
          </span>
        </label>
        <label class="sf-audio-toggle">
          <input
            type="checkbox"
            [checked]="jarvisFullAll()"
            (change)="onJarvisFullAllToggle($event)"
          />
          <span class="sf-audio-toggle-text">
            <strong>JARVIS — modo completo</strong>
            <small>Detecta picker de escolha e pede resposta por voz (escutar microfone em toda sessão).</small>
          </span>
        </label>

        @if (primaryHostId() && hostSupportsTts(primaryHostId())) {
          <div class="sf-audio-host">
            <span class="sf-audio-host-name">{{ worker()?.display_name || worker()?.hostname || 'este host' }}</span>
            @if (installingHostId() === primaryHostId()) {
              <div class="sf-audio-progress">
                <div class="sf-audio-progress-bar"><span></span></div>
                <span class="sf-audio-progress-label">Instalando motor de voz…</span>
              </div>
            } @else {
              <select
                class="sf-audio-select"
                (change)="onTtsModeChange(primaryHostId()!, worker()?.voice_effect, worker()?.tts_engines, $event)"
              >
                @for (opt of engineOptions(worker()?.tts_engines); track opt.value) {
                  <option [value]="opt.value" [selected]="opt.value === (worker()?.tts_mode || '')">{{ opt.label }}</option>
                }
              </select>
            }
            <div class="sf-audio-controls">
              <label class="sf-audio-effect">
                <input
                  type="checkbox"
                  [checked]="worker()?.voice_effect !== false"
                  (change)="onVoiceEffectChange(primaryHostId()!, worker()?.tts_mode, $event)"
                />
                Efeito robótico
              </label>
              <button
                type="button"
                class="sf-audio-test"
                [disabled]="!!testingVoice() || installingHostId() === primaryHostId()"
                (click)="testVoice(primaryHostId()!)"
              >
                {{ testingVoice() === primaryHostId() ? 'Tocando…' : '🔊 Testar' }}
              </button>
            </div>
            @if (testVoiceErrorHostId() === primaryHostId()) {
              <div class="sf-audio-error">⚠️ {{ testVoiceErrorMsg() }}</div>
            }
            @if (installErrorHostId() === primaryHostId()) {
              <div class="sf-audio-error">⚠️ {{ installErrorMsg() }}</div>
            }
          </div>
        }

        @for (w of otherWorkers(); track w.host_id) {
          @if (w.host_id && hostSupportsTts(w.host_id)) {
            <div class="sf-audio-host">
              <span class="sf-audio-host-name">{{ w.display_name || w.hostname || 'host' }}</span>
              @if (installingHostId() === w.host_id) {
                <div class="sf-audio-progress">
                  <div class="sf-audio-progress-bar"><span></span></div>
                  <span class="sf-audio-progress-label">Instalando motor de voz…</span>
                </div>
              } @else {
                <select
                  class="sf-audio-select"
                  (change)="onTtsModeChange(w.host_id!, w.voice_effect, w.tts_engines, $event)"
                >
                  @for (opt of engineOptions(w.tts_engines); track opt.value) {
                    <option [value]="opt.value" [selected]="opt.value === (w.tts_mode || '')">{{ opt.label }}</option>
                  }
                </select>
              }
              <div class="sf-audio-controls">
                <label class="sf-audio-effect">
                  <input
                    type="checkbox"
                    [checked]="w.voice_effect !== false"
                    (change)="onVoiceEffectChange(w.host_id!, w.tts_mode, $event)"
                  />
                  Efeito robótico
                </label>
                <button
                  type="button"
                  class="sf-audio-test"
                  [disabled]="!!testingVoice() || installingHostId() === w.host_id"
                  (click)="testVoice(w.host_id!)"
                >
                  {{ testingVoice() === w.host_id ? 'Tocando…' : '🔊 Testar' }}
                </button>
              </div>
              @if (installErrorHostId() === w.host_id) {
                <div class="sf-audio-error">⚠️ {{ installErrorMsg() }}</div>
              }
              @if (testVoiceErrorHostId() === w.host_id) {
                <div class="sf-audio-error">⚠️ {{ testVoiceErrorMsg() }}</div>
              }
            </div>
          }
        }
      </div>

      <!-- Settings -->
      <div class="sf-settings">
        @for (s of settings(); track s.key; let first = $first; let last = $last; let i = $index) {
          @if (i === 0 || settings()[i - 1].group !== s.group) {
            <div class="sf-group-header">{{ groupLabel(s.group) }}</div>
          }
          <div
            class="sf-setting"
            [class.sf-divider]="!first"
            [class.sf-clickable]="s.kind !== 'toggle' || !s.disabled"
            (click)="onRowClick(s)"
          >
            <span class="sf-setting-icon" aria-hidden="true">
              @switch (s.key) {
                @case ('push') {
                  <svg
                    width="17"
                    height="17"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
                    <path d="M13.7 21a2 2 0 0 1-3.4 0" />
                  </svg>
                }
                @case ('realtime') {
                  <svg
                    width="17"
                    height="17"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M13 2 3 14h7l-1 8 10-12h-7l1-8z" />
                  </svg>
                }
                @case ('dark') {
                  <svg
                    width="17"
                    height="17"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z" />
                  </svg>
                }
                @case ('milestones') {
                  <svg
                    width="17"
                    height="17"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M9 6h11M9 12h11M9 18h11M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2" />
                  </svg>
                }
                @case ('jarvis') {
                  <svg
                    width="17"
                    height="17"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M12 8V4H8" />
                    <rect width="16" height="12" x="4" y="8" rx="2" />
                    <path d="M2 14h2M20 14h2M15 13v2M9 13v2" />
                  </svg>
                }
                @case ('jarvis_full') {
                  <svg
                    width="17"
                    height="17"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
                    <path d="M19 10v1a7 7 0 0 1-14 0v-1M12 18v4M8 22h8" />
                  </svg>
                }
                @case ('voice_dedup') {
                  <svg
                    width="17"
                    height="17"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <circle cx="6" cy="6" r="3" />
                    <path d="M8.12 8.12 12 12" />
                    <path d="M20 4 8.12 15.88" />
                    <circle cx="6" cy="18" r="3" />
                    <path d="M14.8 14.8 20 20" />
                  </svg>
                }
                @case ('text_dedup') {
                  <svg
                    width="17"
                    height="17"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M3 6h18M3 12h13M3 18h13" />
                    <circle cx="20" cy="6" r="2" />
                  </svg>
                }
                @case ('keep_warm') {
                  <svg
                    width="17"
                    height="17"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <polygon points="13 2 4 14 12 14 11 22 20 10 12 10 13 2" />
                  </svg>
                }
                @case ('cues') {
                  <svg
                    width="17"
                    height="17"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M11 5 6 9H2v6h4l5 4z" />
                    <path d="M15.5 8.5a5 5 0 0 1 0 7M19 5a9 9 0 0 1 0 14" />
                  </svg>
                }
                @case ('lang') {
                  <svg
                    width="17"
                    height="17"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <circle cx="12" cy="12" r="9" />
                    <path
                      d="M3 12h18M12 3a14 14 0 0 1 0 18 14 14 0 0 1 0-18z"
                    />
                  </svg>
                }
              }
            </span>
            <span class="sf-setting-label">
              <span class="sf-setting-title">
                {{ s.title }}
                @if (s.soon) {
                  <span class="sf-tag">em breve</span>
                }
              </span>
              @if (s.sub) {
                <span class="sf-setting-sub">{{ s.sub }}</span>
              }
            </span>

            @if (s.kind === 'toggle') {
              <div class="sf-setting-actions">
                @if (s.key === 'voice_dedup' && !s.disabled && s.value) {
                  @if (installingOllama()) {
                    <span class="sf-setting-status">Instalando...</span>
                  } @else if (ollamaStatus() && !ollamaStatus()!.model_installed) {
                    <button class="sf-btn-action" (click)="installDedupModel($event)">Instalar modelo</button>
                  } @else if (ollamaStatus() && !ollamaStatus()!.ollama_running) {
                    <span class="sf-setting-status sf-setting-error">Ollama offline</span>
                  }
                }
                <button
                  type="button"
                  role="switch"
                  class="sf-switch"
                  [class.sf-switch-on]="s.value"
                  [disabled]="s.disabled"
                  [attr.aria-checked]="s.value"
                  [attr.aria-label]="s.title"
                  (click)="$event.stopPropagation(); toggle(s.key)"
                >
                  <span class="sf-knob"></span>
                </button>
              </div>
            } @else {
              <span class="sf-setting-value">{{ s.display }}</span>
            }
          </div>
          @if (s.key === 'keep_warm') {
            <div class="sf-setting sf-divider sf-glossary">
              <span class="sf-setting-label">
                <span class="sf-setting-title">Vocabulário técnico</span>
                <span class="sf-setting-sub">
                  Termos que a transcrição de áudio erra com frequência (siglas, nomes
                  próprios). Separe por vírgula ou quebra de linha.
                </span>
              </span>
              @if (glossaryChips().length) {
                <div class="sf-chip-list">
                  @for (term of glossaryChips(); track term) {
                    <span class="sf-chip">
                      {{ term }}
                      <button
                        type="button"
                        class="sf-chip-x"
                        (click)="removeGlossaryTerm(term)"
                        [attr.aria-label]="'Remover ' + term"
                      >×</button>
                    </span>
                  }
                </div>
              }
              <input
                type="text"
                class="sf-glossary-input"
                placeholder="Cole ou digite; vírgula/quebra de linha vira chip"
                [value]="glossaryDraft()"
                (input)="onGlossaryDraftInput($event)"
                (keydown.enter)="onGlossaryDraftEnter($event)"
              />
            </div>
          }
        }
      </div>

      <!-- Testar notificação do sistema (confirma se aparece no device) -->
      @if (notify.permission() === 'granted') {
        <button type="button" class="sf-test-notif" (click)="testNotify()">
          Testar notificação
        </button>
      }
      <button type="button" class="sf-test-notif" (click)="testVibrate()">
        Testar vibração 📳
      </button>
      @if (vibeMsg()) {
        <p class="sf-vibe-msg">{{ vibeMsg() }}</p>
      }

      <!-- Instalar como app (PWA) -->
      @if (canInstall()) {
        <button type="button" class="sf-install" (click)="installApp()">
          <span class="sf-install-icon" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 3v12M7 10l5 5 5-5" />
              <path d="M5 21h14" />
            </svg>
          </span>
          <span class="sf-install-body">
            <span class="sf-install-title">Instalar como app</span>
            <span class="sf-install-sub">Abre em tela cheia, igual a um app nativo</span>
          </span>
        </button>
        @if (showIosHelp()) {
          <div class="sf-ios-help">
            No Safari, toque em <strong>Compartilhar</strong>
            <span class="sf-ios-share" aria-hidden="true">⬆️</span> e depois em
            <strong>Adicionar à Tela de Início</strong>.
          </div>
        }
      }

      <!-- Recarregar / limpar cache (pull-to-refresh está desabilitado no app) -->
      <button type="button" class="sf-install" (click)="reloadApp()" [disabled]="reloading()">
        <span class="sf-install-icon" aria-hidden="true">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5" />
          </svg>
        </span>
        <span class="sf-install-body">
          <span class="sf-install-title">{{ reloading() ? 'Atualizando…' : 'Recarregar app' }}</span>
          <span class="sf-install-sub">Limpa o cache e baixa a versão mais nova</span>
        </span>
      </button>

      <!-- Logout -->
      <div class="sf-logout" (click)="logout()">Sair</div>

      <!-- Versão deployada (SHA curto do commit) -->
      @if (appVersion()) {
        <div class="sf-version">v{{ appVersion() }}</div>
      }
    </section>
  `,
  styles: [
    `
      :host {
        display: block;
      }

      .sf-perfil {
        padding: 6px 20px 120px;
      }

      .sf-title-row {
        padding: 10px 0 22px;
      }
      .sf-title {
        font-size: 28px;
        font-weight: 700;
        color: #f4f5f7;
        letter-spacing: -0.6px;
      }

      /* Identity */
      .sf-identity {
        display: flex;
        align-items: center;
        gap: 14px;
        margin-bottom: 22px;
      }
      .sf-avatar {
        position: relative;
        width: 58px;
        height: 58px;
        border-radius: 18px;
        flex: none;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 24px;
        font-weight: 800;
        color: #06231d;
        background: linear-gradient(150deg, #2cecc4, #00a482);
        border: none;
        padding: 0;
        cursor: pointer;
        overflow: visible;
      }
      .sf-avatar-img {
        width: 100%;
        height: 100%;
        border-radius: 18px;
        object-fit: cover;
      }
      .sf-avatar-cam {
        position: absolute;
        right: -4px;
        bottom: -4px;
        width: 24px;
        height: 24px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        color: #f4f5f7;
        background: #22272a;
        border: 2px solid #0e1113;
      }
      .sf-photo-remove {
        margin-top: 4px;
        padding: 0;
        background: none;
        border: none;
        color: #8a90a0;
        font-size: 12.5px;
        cursor: pointer;
        text-decoration: underline;
      }
      .sf-photo-remove:hover {
        color: #f87171;
      }
      .sf-name {
        font-size: 19px;
        font-weight: 700;
        color: #f4f5f7;
      }
      .sf-role {
        font-size: 13.5px;
        color: #8a90a0;
      }

      /* Worker */
      .sf-worker {
        background: #181c1b;
        border: 1px solid #283230;
        border-radius: 18px;
        padding: 16px;
        margin-bottom: 18px;
        display: flex;
        /* flex-start (não center): com o detalhe de hardware expandido o
           corpo do card fica bem mais alto — center jogava a bolinha/pill
           pro MEIO do card em vez de ficarem alinhados com o nome do host. */
        align-items: flex-start;
        gap: 13px;
      }
      .sf-worker-dot {
        width: 11px;
        height: 11px;
        border-radius: 50%;
        flex: none;
        margin-top: 4px;
      }
      .sf-pulse {
        animation: sf-pulse-green 2.4s infinite;
      }
      @keyframes sf-pulse-green {
        0% {
          box-shadow: 0 0 0 0 rgba(0, 228, 180, 0.5);
        }
        70% {
          box-shadow: 0 0 0 7px rgba(0, 228, 180, 0);
        }
        100% {
          box-shadow: 0 0 0 0 rgba(0, 228, 180, 0);
        }
      }
      @media (prefers-reduced-motion: reduce) {
        .sf-pulse {
          animation: none;
        }
      }
      .sf-worker-body {
        flex: 1;
        min-width: 0;
      }
      .sf-worker-label {
        font-size: 15px;
        font-weight: 600;
        color: #f4f5f7;
        display: flex;
        align-items: center;
        gap: 6px;
        min-width: 0;
        /* Permite os filhos (nome longo, ações do Colab) quebrarem pra outra
           linha quando o card é estreito (mobile). Sem isso, o nome de 2
           linhas aperta o .sf-colab-actions e o botão interno fica com
           largura insuficiente — ícone ⏹ empilha em cima de "Desligar" e o
           card vira aquela caixinha vermelha apertada. */
        flex-wrap: wrap;
      }
      /* Deixa QUEBRAR em vez de truncar com reticências — nome do host
         cortado no meio (ex.: "Duck Ser…") era pior que ocupar 2 linhas,
         já que o card cresce mesmo (tem o resumo de hardware embaixo). */
      .sf-worker-name {
        min-width: 0;
        word-break: break-word;
      }
      .sf-worker-meta {
        font-size: 12.5px;
        color: #7a8090;
        font-family: 'JetBrains Mono', monospace;
        margin-top: 2px;
      }
      /* Cada estatística no seu PRÓPRIO span — o wrap acontece ENTRE elas
         (nunca no meio de uma, ex.: ícone numa linha e o valor sozinho na
         próxima), diferente de antes (tudo um texto só). */
      .sf-hw-stats {
        display: flex;
        flex-wrap: wrap;
        gap: 3px 10px;
        flex: 1;
        min-width: 0;
      }
      .sf-hw-stat {
        white-space: nowrap;
      }
      .sf-fleet-total {
        margin: 0 0 10px;
        padding: 10px 12px;
        border-radius: 10px;
        background: rgba(52, 211, 153, 0.06);
        border: 1px solid rgba(52, 211, 153, 0.18);
        font-size: 12px;
      }
      .sf-fleet-title {
        font-weight: 600;
        margin-bottom: 4px;
        color: #34d399;
      }
      .sf-hw-caret {
        flex: none;
        color: #4a5058;
        font-size: 9px;
        margin-top: 2px;
      }
      .sf-hw-detail {
        margin-top: 6px;
        padding: 8px 10px;
        background: #0e1113;
        border: 1px solid #20262a;
        border-radius: 10px;
        font-size: 11.5px;
        line-height: 1.6;
        color: #9aa0ae;
        font-family: 'JetBrains Mono', monospace;
      }
      .sf-ollama-title {
        margin-top: 6px;
        padding-top: 6px;
        border-top: 1px solid #20262a;
        color: #6a7078;
        font-size: 10.5px;
        text-transform: uppercase;
        letter-spacing: 0.04em;
      }
      .sf-ollama-model {
        display: flex;
        align-items: center;
        gap: 6px;
      }
      .sf-ollama-dot {
        flex: none;
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: #34d399;
      }
      /* Painel de máquinas: chips de filtro por faixa de CPU. */
      .sf-fleet-chips {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin: 0 0 10px;
      }
      .sf-fleet-chip {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 5px 10px;
        border-radius: 999px;
        border: 1px solid #283230;
        background: #181c1b;
        color: #9aa0ae;
        font-size: 12px;
        cursor: pointer;
      }
      .sf-fleet-chip.sf-on {
        color: #f4f5f7;
        border-color: var(--band);
        background: color-mix(in srgb, var(--band) 16%, transparent);
      }
      .sf-chip-dot {
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background: var(--band);
      }
      .sf-fleet-empty {
        font-size: 12.5px;
        color: #7a8090;
        margin: 0 0 18px;
      }
      .sf-host {
        margin-bottom: 10px;
        padding: 12px 14px;
      }
      /* Área clicável do card fechado (barras + sessões) — abre o detalhe. */
      .sf-host-toggle {
        display: flex;
        flex-direction: column;
        gap: 5px;
        width: 100%;
        margin-top: 8px;
        padding: 0;
        background: none;
        border: none;
        color: inherit;
        cursor: pointer;
        text-align: left;
      }
      .sf-bar-row {
        display: flex;
        align-items: center;
        gap: 8px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 11px;
        color: #8a9099;
      }
      .sf-bar-label {
        flex: none;
        width: 28px;
      }
      .sf-bar-val {
        flex: none;
        width: 34px;
        text-align: right;
      }
      .sf-bar {
        flex: 1;
        min-width: 0;
        height: 8px;
        border-radius: 4px;
        background: #0e1113;
        overflow: hidden;
      }
      .sf-bar-fill {
        display: block;
        height: 100%;
        border-radius: 4px;
        background: var(--band);
        transition: width 0.4s ease;
      }
      /* RAM = barra secundária: mais fina e neutra. */
      .sf-bar-ram .sf-bar {
        height: 5px;
      }
      .sf-bar-ram .sf-bar-fill {
        background: #6b8cae;
      }
      .sf-host-toggle:disabled {
        cursor: default;
      }
      /* Host principal (o do card de cima antes do painel): borda de destaque. */
      .sf-host-primary {
        border-color: rgba(0, 228, 180, 0.35);
      }
      .sf-primary-tag {
        flex: none;
        font-size: 10px;
        font-weight: 600;
        padding: 1px 6px;
        border-radius: 6px;
        color: #00e4b4;
        background: rgba(0, 228, 180, 0.1);
      }
      .sf-host-nometrics {
        font-size: 11.5px;
        color: #7a8090;
      }
      .sf-host-foot {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
      }
      .sf-sess-chip {
        font-size: 11px;
        padding: 2px 8px;
        border-radius: 999px;
        color: #00e4b4;
        background: rgba(0, 228, 180, 0.1);
      }
      .sf-sess-chip.sf-zero {
        color: #7a8090;
        background: rgba(138, 144, 160, 0.1);
      }
      .sf-sess-names {
        display: flex;
        flex-wrap: wrap;
        gap: 2px 10px;
        word-break: break-word;
      }
      .sf-more {
        color: #6a7078;
      }
      .sf-worker-pill {
        font-size: 11px;
        font-weight: 700;
        padding: 4px 9px;
        border-radius: 8px;
        flex: none;
        margin-top: 2px;
      }
      /* Renomear host (multi-host, AD-011) */
      .sf-worker-edit-btn {
        appearance: none;
        background: none;
        border: none;
        color: #7a8090;
        font-size: 13px;
        cursor: pointer;
        padding: 0 2px;
        line-height: 1;
      }
      .sf-worker-edit-btn:hover {
        color: #00e4b4;
      }
      /* Colab Play / Stop actions */
      .sf-colab-actions {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        margin-left: 8px;
        /* Mantém largura natural (botão + link lado a lado) mesmo quando o
           nome do host ocupa 2 linhas e empurra o parent pra quebrar. Sem
           isso, o flex parent espreme o botão e o ícone + texto quebram
           feio dentro dele. */
        flex-shrink: 0;
      }
      .sf-colab-btn {
        appearance: none;
        border: 1px solid #283230;
        background: #192020;
        border-radius: 6px;
        padding: 2px 7px;
        font-size: 11px;
        font-weight: 600;
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        gap: 4px;
        transition: all 0.15s ease;
        /* Defesa: mesmo se algum container apertar a largura do botão, o
           texto fica numa linha só (corta horizontal, não empilha ícone
           em cima do label). */
        white-space: nowrap;
      }
      .sf-colab-btn:disabled {
        opacity: 0.6;
        cursor: not-allowed;
      }
      .sf-colab-play {
        color: #34d399;
        border-color: rgba(52, 211, 153, 0.3);
      }
      .sf-colab-play:hover:not(:disabled) {
        background: rgba(52, 211, 153, 0.15);
        border-color: #34d399;
      }
      .sf-colab-stop {
        color: #f87171;
        border-color: rgba(248, 113, 113, 0.3);
      }
      .sf-colab-stop:hover:not(:disabled) {
        background: rgba(248, 113, 113, 0.15);
        border-color: #f87171;
      }
      .sf-colab-link {
        color: #7a8090;
        font-size: 11px;
        text-decoration: none;
        padding: 2px 4px;
      }
      .sf-colab-link:hover {
        color: #00e4b4;
        text-decoration: underline;
      }
      .sf-worker-edit-row {
        display: flex;
        gap: 6px;
        margin-bottom: 4px;
      }
      .sf-worker-edit-emoji {
        width: 44px;
        flex: none;
        text-align: center;
        background: #14191a;
        border: 1px solid #283230;
        border-radius: 8px;
        color: #f4f5f7;
        font-size: 16px;
        padding: 5px 4px;
      }
      .sf-worker-edit-input {
        width: 100%;
        max-width: 260px;
        background: #14191a;
        border: 1px solid #283230;
        border-radius: 8px;
        color: #f4f5f7;
        font-size: 14px;
        padding: 5px 9px;
      }
      .sf-worker-edit-acts {
        display: flex;
        gap: 8px;
      }
      .sf-worker-edit-acts button {
        appearance: none;
        background: none;
        border: none;
        color: #00e4b4;
        font-size: 12px;
        font-weight: 700;
        cursor: pointer;
        padding: 0;
      }
      .sf-worker-edit-acts button:last-child {
        color: #7a8090;
      }

      /* Stats */
      .sf-stats {
        display: flex;
        gap: 12px;
        margin-bottom: 18px;
      }
      .sf-stat {
        flex: 1;
        background: #181c1b;
        border: 1px solid #283230;
        border-radius: 16px;
        padding: 15px;
      }
      .sf-stat-value {
        font-size: 24px;
        font-weight: 800;
        color: #f4f5f7;
      }
      .sf-stat-accent {
        color: #00e4b4;
      }
      .sf-stat-label {
        font-size: 12.5px;
        color: #7a8090;
        margin-top: 2px;
      }

      /* Limites de uso */
      .sf-limits {
        background: #181c1b;
        border: 1px solid #283230;
        border-radius: 18px;
        padding: 16px;
        margin-bottom: 18px;
      }
      .sf-limits-head {
        font-size: 13px;
        font-weight: 700;
        letter-spacing: 0.3px;
        text-transform: uppercase;
        color: #7a8090;
        margin-bottom: 12px;
      }
      .sf-limit {
        margin-bottom: 12px;
      }
      .sf-limit-row {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        margin-bottom: 6px;
      }
      .sf-limit-name {
        font-size: 13.5px;
        color: #f4f5f7;
      }
      .sf-limit-pct {
        font-size: 13.5px;
        font-weight: 700;
        color: #00e4b4;
        font-family: 'JetBrains Mono', monospace;
      }
      .sf-limit-bar {
        height: 6px;
        border-radius: 999px;
        background: #2a3130;
        overflow: hidden;
      }
      .sf-limit-bar > span {
        display: block;
        height: 100%;
        border-radius: 999px;
        background: linear-gradient(90deg, #2cecc4, #00a482);
        transition: width 0.3s ease;
      }
      .sf-limit-empty {
        font-size: 13px;
        color: #7a8090;
      }
      .sf-limit-note {
        margin-top: 8px;
        font-size: 11.5px;
        color: #6b7280;
      }

      /* Áudio do JARVIS (volume local + modo de voz/efeito por host) */
      .sf-audio {
        background: #181c1b;
        border: 1px solid #283230;
        border-radius: 18px;
        padding: 14px 16px;
        display: flex;
        flex-direction: column;
        gap: 12px;
        /* Faltava — todo outro card da tela (.sf-worker/.sf-limits/etc.) tem
           18px de respiro; sem isso o card de Áudio ficava colado direto no
           próximo bloco (Configurações), sem separação nenhuma. */
        margin-bottom: 18px;
      }
      .sf-audio-head {
        font-size: 13px;
        font-weight: 700;
        color: #c9cdd6;
      }
      .sf-audio-volume {
        display: flex;
        align-items: center;
        gap: 10px;
        font-size: 12.5px;
        color: #9aa0ae;
      }
      .sf-audio-volume input[type='range'] {
        flex: 1;
        accent-color: #2cecc4;
      }
      .sf-audio-volume-val {
        min-width: 34px;
        text-align: right;
        font-variant-numeric: tabular-nums;
      }
      /* Toggles inline do JARVIS dentro do card de Áudio: checkbox à esquerda,
         texto à direita (label em cima, descrição embaixo). Sem isso, o <strong>
         cola no <small> e o checkbox cola no texto — fica ilegível. */
      .sf-audio-toggle {
        display: flex;
        align-items: flex-start;
        gap: 10px;
        padding: 8px 0;
        cursor: pointer;
      }
      .sf-audio-toggle + .sf-audio-toggle {
        border-top: 1px solid #20262a;
        margin-top: 4px;
      }
      .sf-audio-toggle input[type='checkbox'] {
        flex: none;
        margin-top: 2px;
        accent-color: #2cecc4;
      }
      .sf-audio-toggle-text {
        display: flex;
        flex-direction: column;
        gap: 3px;
      }
      .sf-audio-toggle-text strong {
        font-size: 13.5px;
        font-weight: 600;
        color: #f4f5f7;
      }
      .sf-audio-toggle-text small {
        font-size: 11.5px;
        color: #7a8090;
        line-height: 1.4;
      }
      /* Cada host: nome numa linha (com a bolinha do worker acima, se quiser
         associar visualmente), controles NA LINHA DE BAIXO — evita amontoar
         nome+select+checkbox+botão numa linha só, que quebrava feio em
         telas estreitas. */
      .sf-audio-host {
        display: flex;
        flex-direction: column;
        gap: 8px;
        padding-top: 10px;
        border-top: 1px solid #20262a;
      }
      .sf-audio-host-name {
        min-width: 0;
        font-size: 12.5px;
        font-weight: 600;
        color: #c9cdd6;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      /* Select ocupa a LARGURA TOTA (linha própria) — "Alta qualidade (XTTS)"
         não cabia dividindo espaço com checkbox+botão sem apertar tudo.
         Controles (efeito + testar) ficam na linha de baixo, nas pontas. */
      .sf-audio-select {
        width: 100%;
        background: #0e1113;
        border: 1px solid #283230;
        border-radius: 8px;
        color: #c9cdd6;
        font-size: 12px;
        padding: 6px 8px;
      }
      .sf-audio-controls {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
      }
      .sf-audio-effect {
        display: flex;
        align-items: center;
        gap: 5px;
        font-size: 12px;
        color: #9aa0ae;
        white-space: nowrap;
      }
      .sf-audio-test {
        appearance: none;
        background: transparent;
        border: 1px solid #283230;
        border-radius: 8px;
        color: #2cecc4;
        font-size: 12px;
        font-weight: 600;
        padding: 5px 10px;
        cursor: pointer;
        white-space: nowrap;
      }
      .sf-audio-error {
        font-size: 11.5px;
        color: #f0b429;
        line-height: 1.4;
      }
      /* Barra indeterminada (sem % real — o download não expõe progresso
         byte a byte de forma simples) — ainda assim deixa claro que ALGO
         está acontecendo em vez do select sumir sem explicação. */
      .sf-audio-progress {
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .sf-audio-progress-bar {
        width: 100%;
        height: 6px;
        border-radius: 999px;
        background: #0e1113;
        overflow: hidden;
      }
      .sf-audio-progress-bar span {
        display: block;
        width: 40%;
        height: 100%;
        border-radius: 999px;
        background: linear-gradient(90deg, #2cecc4, #00a482);
        animation: sf-audio-progress-slide 1.2s ease-in-out infinite;
      }
      @keyframes sf-audio-progress-slide {
        0% {
          transform: translateX(-100%);
        }
        100% {
          transform: translateX(250%);
        }
      }
      .sf-audio-progress-label {
        font-size: 11.5px;
        color: #7a8090;
      }
      .sf-audio-test:disabled {
        opacity: 0.5;
        cursor: default;
      }

      /* Settings list */
      .sf-settings {
        background: #181c1b;
        border: 1px solid #283230;
        border-radius: 18px;
        overflow: hidden;
      }
      /* Section header entre grupos (Notificações / Agente / Aparência) —
         rótulo pequeno e espaçado, sem compete com os toggles. */
      .sf-group-header {
        padding: 14px 18px 6px;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: #7a90ad;
        background: rgba(255, 255, 255, 0.02);
      }
      .sf-setting:first-of-type {
        border-top: none;
      }
      .sf-setting {
        display: flex;
        align-items: center;
        gap: 13px;
        padding: 15px 16px;
      }
      .sf-clickable {
        cursor: pointer;
      }
      .sf-divider {
        border-top: 1px solid #23262f;
      }
      .sf-setting-icon {
        width: 30px;
        height: 30px;
        border-radius: 9px;
        flex: none;
        display: flex;
        align-items: center;
        justify-content: center;
        color: #9aa0ae;
        background: #22272a;
      }
      .sf-setting-label {
        flex: 1;
        min-width: 0;
        font-size: 15px;
        font-weight: 500;
        color: #f4f5f7;
        display: flex;
        flex-direction: column;
        gap: 3px;
      }
      .sf-setting-title {
        display: inline-flex;
        align-items: center;
        gap: 8px;
      }
      .sf-setting-sub {
        font-size: 12px;
        font-weight: 400;
        color: #7a8090;
        line-height: 1.35;
      }
      .sf-tag {
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 0.4px;
        text-transform: uppercase;
        color: #7a8090;
        background: #22272a;
        border: 1px solid #283230;
        padding: 2px 7px;
        border-radius: 6px;
      }
      .sf-setting-value {
        font-size: 13.5px;
        color: #7a8090;
        flex: none;
      }
      /* Glossário técnico: textarea em linha própria abaixo do label
         (.sf-setting normal é flex-row lado a lado, não serve p/ multi-linha). */
      .sf-glossary {
        flex-direction: column;
        align-items: stretch;
      }
      .sf-glossary-input {
        width: 100%;
        background: #0e1113;
        border: 1px solid #283230;
        border-radius: 8px;
        color: #c9cdd6;
        font-size: 12.5px;
        font-family: inherit;
        padding: 8px 10px;
        box-sizing: border-box;
      }
      .sf-chip-list {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin-bottom: 8px;
      }
      .sf-chip {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        background: #22272a;
        border: 1px solid #283230;
        border-radius: 999px;
        padding: 4px 6px 4px 10px;
        font-size: 12px;
        color: #c9cdd6;
      }
      .sf-chip-x {
        appearance: none;
        background: transparent;
        border: none;
        color: #7a8090;
        font-size: 14px;
        line-height: 1;
        cursor: pointer;
        padding: 2px 4px;
      }
      .sf-chip-x:hover {
        color: #f87171;
      }

      /* Switch — 44x26 radius 13px, on = #00A482 */
      .sf-switch {
        width: 44px;
        height: 26px;
        border-radius: 13px;
        flex: none;
        padding: 3px;
        box-sizing: border-box;
        background: #2a3130;
        border: none;
        cursor: pointer;
        display: flex;
        justify-content: flex-start;
        transition: background 0.2s;
      }
      .sf-switch-on {
        background: #00a482;
        justify-content: flex-end;
      }
      .sf-switch:disabled {
        opacity: 0.45;
        cursor: not-allowed;
      }
      .sf-knob {
        width: 20px;
        height: 20px;
        border-radius: 50%;
        background: #fff;
      }

      /* Testar notificação */
      .sf-test-notif {
        width: 100%;
        margin-top: 12px;
        padding: 12px;
        background: none;
        border: 1px dashed #283230;
        border-radius: 14px;
        color: #8a90a0;
        font-size: 13.5px;
        font-weight: 600;
        cursor: pointer;
      }
      .sf-vibe-msg {
        margin: 8px 2px 0;
        font-size: 12.5px;
        line-height: 1.45;
        color: #9fb0ad;
      }

      /* Instalar como app */
      .sf-install {
        width: 100%;
        margin-top: 18px;
        display: flex;
        align-items: center;
        gap: 13px;
        padding: 15px 16px;
        text-align: left;
        background: #181c1b;
        border: 1px solid #283230;
        border-radius: 18px;
        cursor: pointer;
      }
      .sf-install-icon {
        width: 34px;
        height: 34px;
        border-radius: 10px;
        flex: none;
        display: flex;
        align-items: center;
        justify-content: center;
        color: #06231d;
        background: linear-gradient(150deg, #2cecc4, #00a482);
      }
      .sf-install-body {
        flex: 1;
        min-width: 0;
        display: flex;
        flex-direction: column;
      }
      .sf-install-title {
        font-size: 15px;
        font-weight: 600;
        color: #f4f5f7;
      }
      .sf-install-sub {
        font-size: 12.5px;
        color: #7a8090;
        margin-top: 2px;
      }
      .sf-ios-help {
        margin-top: 10px;
        padding: 13px 15px;
        background: #181c1b;
        border: 1px solid #283230;
        border-radius: 14px;
        font-size: 13px;
        line-height: 1.5;
        color: #b9bfca;
      }
      .sf-ios-help strong {
        color: #f4f5f7;
        font-weight: 600;
      }
      .sf-ios-share {
        font-style: normal;
      }

      /* Logout */
      .sf-logout {
        margin-top: 18px;
        text-align: center;
        padding: 15px;
        border-radius: 14px;
        border: 1px solid #3a2326;
        color: #f87171;
        font-size: 15px;
        font-weight: 600;
        cursor: pointer;
      }
      .sf-version {
        margin-top: 10px;
        text-align: center;
        font-size: 12px;
        opacity: 0.4;
        font-family: monospace;
      }
    `,
  ],
})
export class PerfilComponent implements OnInit, OnDestroy {
  private readonly api = inject(ApiService);
  private readonly sse = inject(SseService);
  protected readonly workers = inject(WorkersStore);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly pwa = inject(PwaInstallService);
  protected readonly notify = inject(NotifyService);
  private readonly cues = inject(EventCuesService);
  protected readonly jarvisAudio = inject(JarvisAudioService);

  /** Input de arquivo escondido, disparado pelo clique no avatar. */
  private readonly fileInput = viewChild<ElementRef<HTMLInputElement>>('fileInput');

  /** Foto de perfil (data URL) persistida no cliente — null = inicial "D". */
  readonly photo = signal<string | null>(null);
  /** SHA curto do commit deployado nesta instância (rodapé) — `null` até carregar. */
  readonly appVersion = signal<string | null>(null);

  /**
   * Mostra a opção de instalar (prompt nativo ou instruções iOS). Computed para
   * reagir quando `beforeinstallprompt` chega depois da construção do componente.
   */
  readonly canInstall = computed(() => this.pwa.shouldOffer());
  /** Quando true, exibe as instruções manuais do iOS/Safari. */
  readonly showIosHelp = signal(false);

  /** All sessions loaded from the API, refreshed on SSE activity. */
  private readonly sessions = signal<Session[]>([]);

  /** Status REAL do Worker (heartbeat do host) — null antes de carregar. */
  protected readonly worker = signal<WorkerStatus | null>(null);
  /** Limites de uso reais (hoje só Claude). */
  private readonly usage = signal<UsageInfo | null>(null);

  /** Online = heartbeat recente do worker; SSE como reforço. */
  readonly connected = computed(
    () => this.worker()?.online === true || this.sse.connected(),
  );

  readonly workerTitle = computed(() => {
    const w = this.worker();
    const host = w?.display_name || w?.hostname;
    const prefix = w?.emoji ? `${w.emoji} ` : '';
    if (!this.connected()) {
      return 'Worker desconectado';
    }
    return host ? `${prefix}Worker · ${host}` : 'Worker conectado';
  });

  /** Host · uptime em mono — tempo real do worker, "—" quando desconhecido. */
  readonly workerMeta = computed(() => {
    const w = this.worker();
    const host = w?.display_name || w?.hostname || '—';
    const up = w?.online ? formatUptime(w.uptime_seconds) : '—';
    return `${host} · uptime ${up}`;
  });

  /** host_id do worker principal (card de cima) — pra habilitar a edição de nome. */
  protected readonly primaryHostId = computed(() => this.worker()?.host_id ?? null);

  /**
   * Outros hosts conhecidos (multi-host, AD-011), além do exibido no card
   * principal acima — só quando há MAIS DE 1 host ativo no total.
   */
  readonly otherWorkers = computed(() => {
    if (!this.workers.hasMultipleHosts()) {
      return [];
    }
    const primaryHost = this.worker()?.hostname;
    return this.workers.workers().filter((w) => w.hostname !== primaryHost);
  });

  /** Uptime formatado de um worker da lista `otherWorkers` (não o principal). */
  protected formatUptimeFor(w: WorkerStatus): string {
    return w.online ? formatUptime(w.uptime_seconds) : '—';
  }

  // ── Hardware/SO por host (resumo em ícones + expandir pra ver detalhe) ──
  /** host_id com o detalhe de hardware ABERTO agora, ou null (nenhum). */
  protected readonly expandedHostId = signal<string | null>(null);

  protected toggleHardware(hostId: string | null): void {
    if (!hostId) {
      return;
    }
    this.expandedHostId.update((cur) => (cur === hostId ? null : hostId));
  }

  /** Soma do hardware dos hosts ONLINE (Perfil > card "Total"). RAM livre e
   * CPU média só contam hosts com métricas (worker novo); `null` se nenhum.
   * Disco = soma do 1º disco de cada host (o mesmo do resumo do card). */
  readonly fleetTotals = computed(() => {
    const all = this.workers.workers();
    if (all.length < 2) return null;
    const online = all.filter((w) => w.online);
    let cores = 0, ramTotal = 0, gpus = 0, disk = 0;
    let ramAvail: number | null = null;
    const cpus: number[] = [];
    for (const w of online) {
      const hw = w.hardware;
      cores += hw?.cpu_cores ?? 0;
      ramTotal += hw?.ram_total_gb ?? w.metrics?.mem_total_gb ?? 0;
      if (hw?.gpu) gpus++;
      disk += Math.round(hw?.disks?.[0]?.total_gb ?? 0);
      if (w.metrics?.mem_avail_gb != null) ramAvail = (ramAvail ?? 0) + w.metrics.mem_avail_gb;
      if (w.metrics?.cpu_pct != null) cpus.push(w.metrics.cpu_pct);
    }
    const cpuAvg = cpus.length ? Math.round(cpus.reduce((a, b) => a + b, 0) / cpus.length) : null;
    return { hosts: all.length, online: online.length, cores, ramTotal, ramAvail, cpuAvg, gpus, disk };
  });

  // ── Painel de máquinas (ordenado por CPU, filtro por faixa) ──────────
  protected readonly filterBands = FLEET_FILTER_BANDS;
  /** Quantos nomes de sessão listar no expand antes do "+N". */
  protected readonly maxSessionNames = 5;
  /** Faixa filtrada agora, ou null (todas). */
  protected readonly fleetFilter = signal<FleetFilter | null>(null);

  /** Todos os hosts conhecidos; enquanto o WorkersStore não carregou, usa o
   * worker principal pra não piscar a tela vazia. */
  protected readonly fleet = computed(() => {
    const all = this.workers.workers();
    if (all.length) return all;
    const w = this.worker();
    return w ? [w] : [];
  });
  protected readonly bandCounts = computed(() => countBands(this.fleet()));
  /** Filtro só vale com os chips visíveis (>1 host) — senão a tela ficaria
   * presa em "Nenhuma máquina" sem chip pra limpar. */
  protected readonly fleetHosts = computed(() => {
    const all = this.fleet();
    return fleetView(all, all.length > 1 ? this.fleetFilter() : null);
  });
  private readonly sessionsByHost = computed(() => activeSessionsByHost(this.sessions()));

  /** Clicar no chip filtra; clicar de novo no mesmo limpa. */
  protected toggleFleetFilter(b: FleetFilter): void {
    this.fleetFilter.update((cur) => (cur === b ? null : b));
  }

  protected band(w: WorkerStatus): LoadBand {
    return loadBand(w);
  }

  protected bandLabel(b: LoadBand): string {
    return BAND_LABELS[b];
  }

  protected bandColor(b: LoadBand): string {
    return BAND_COLORS[b];
  }

  /** CPU% arredondada — o mesmo valor que decide a cor da faixa. */
  protected cpu(w: WorkerStatus): number | null {
    return cpuPct(w);
  }

  protected memPct(w: WorkerStatus): number | null {
    return roundPct(memUsedPct(w));
  }

  /** É o host do card principal (o `worker` travado no reloadWorker)? */
  protected isPrimary(w: WorkerStatus): boolean {
    const p = this.worker();
    if (!p) return false;
    return p.host_id ? w.host_id === p.host_id : w.hostname === p.hostname;
  }

  /** Principal segue `connected()` (heartbeat OU SSE), como antes; os demais
   * só o heartbeat da API. */
  protected isUp(w: WorkerStatus): boolean {
    return this.isPrimary(w) ? this.connected() : w.online;
  }

  protected sessionsOf(w: WorkerStatus): string[] {
    return (w.host_id && this.sessionsByHost().get(w.host_id)) || [];
  }

  protected agentsOf(w: WorkerStatus): string[] {
    return Object.entries(w.agents ?? {})
      .filter(([, ok]) => ok)
      .map(([k]) => k);
  }

  // ── Editar nome/emoji de exibição do host (multi-host, AD-011) ──────────
  /** host_id sendo editado agora, ou null (nenhum campo de edição aberto). */
  protected readonly editingHostId = signal<string | null>(null);
  protected readonly editNameValue = signal('');
  /** Emoji do host em edição — ex. "🦆" pro Windows, "🍎" pro Mac. */
  protected readonly editEmojiValue = signal('');

  /** Abre o campo de edição pra este host, pré-preenchido com nome/emoji atuais. */
  protected startEditName(
    hostId: string | null,
    currentDisplay: string | null,
    currentEmoji: string | null,
  ): void {
    if (!hostId) {
      return;
    }
    this.editingHostId.set(hostId);
    this.editNameValue.set(currentDisplay ?? '');
    this.editEmojiValue.set(currentEmoji ?? '');
  }

  protected cancelEditName(): void {
    this.editingHostId.set(null);
  }

  /** Salva nome + emoji (vazio em cada um limpa, volta ao default). */
  protected saveEditName(hostId: string): void {
    const name = this.editNameValue().trim();
    const emoji = this.editEmojiValue().trim();
    this.api.setWorkerDisplayName(hostId, name || null, emoji || null).subscribe({
      next: () => {
        this.editingHostId.set(null);
        this.workers.refresh();
        // O card principal usa o signal `worker` (não o WorkersStore) — se
        // for ele que editamos, refaz o fetch pra refletir na hora.
        if (this.worker()?.host_id === hostId) {
          this.reloadWorker();
        }
      },
      error: () => {
        /* mantém o campo aberto pro usuário tentar de novo */
      },
    });
  }

  // ── Controle de Máquinas Colab (Play / Stop) ─────────────────────────────
  protected readonly actionLoadingHostId = signal<string | null>(null);

  protected isColab(w: WorkerStatus): boolean {
    const s = `${w.display_name ?? ''} ${w.hostname ?? ''} ${w.host_id ?? ''}`.toLowerCase();
    return s.includes('colab');
  }

  /**
   * Fallback do link "↗ Colab" no card do host. Em 99% dos casos o backend
   * já resolve a URL certa e abre ela via `startColab()` → `res.colab_url`.
   * Esse método só é usado pra pré-popular o href do link "↗ Colab"
   * exibido ao lado do botão Ligar/Desligar — e cai na heurística
   * legada (notebook padrão + 0/1 conforme "colab 2"/"02151543") quando o
   * `worker_status` não tem `colab_notebook_id` nem `colab_authuser`.
   */
  protected colabNotebookUrl(w: WorkerStatus): string {
    const notebookId = w.colab_notebook_id ?? '1MLzqvoeSg_JeMywj4HzvyRKTmssieZH5';
    let authuser = w.colab_authuser ?? null;
    if (!authuser) {
      const s = `${w.display_name ?? ''} ${w.hostname ?? ''} ${w.host_id ?? ''}`.toLowerCase();
      authuser = s.includes('colab 2') || s.includes('colab2') || s.includes('02151543') ? '1' : '0';
    }
    return `https://colab.research.google.com/drive/${notebookId}?authuser=${authuser}`;
  }

  protected startColab(w: WorkerStatus, ev: Event): void {
    ev.stopPropagation();
    if (!w.host_id) return;
    this.actionLoadingHostId.set(w.host_id);
    // Backend resolve a URL do Colab (notebook_id + authuser configuráveis
    // via worker_status). Se vier `colab_url` válida, abre; senão, só
    // marca o host como online no Mongo (e.g. ligar worker não-Colab).
    this.api.startWorker(w.host_id).subscribe({
      next: (res) => {
        if (res.colab_url) {
          window.open(res.colab_url, '_blank');
        }
        setTimeout(() => {
          this.actionLoadingHostId.set(null);
          this.workers.refresh();
        }, 1200);
      },
      error: () => {
        this.actionLoadingHostId.set(null);
        this.workers.refresh();
      },
    });
  }

  protected stopColab(w: WorkerStatus, ev: Event): void {
    ev.stopPropagation();
    if (!w.host_id) return;
    this.actionLoadingHostId.set(w.host_id);
    this.api.stopWorker(w.host_id).subscribe({
      next: () => {
        this.actionLoadingHostId.set(null);
        this.workers.refresh();
        if (this.worker()?.host_id === w.host_id) {
          this.reloadWorker();
        }
      },
      error: () => {
        this.actionLoadingHostId.set(null);
        this.workers.refresh();
      },
    });
  }

  // ── Áudio do JARVIS (Perfil > Áudio): modo de voz + efeito por host,
  // volume local do aparelho, botão "Testar voz" ──────────────────────────

  /** Só mostra a config de áudio pra hosts que suportam TTS (AD-011). */
  protected hostSupportsTts(hostId: string | null | undefined): boolean {
    return this.workers.supports(hostId, 'tts');
  }

  /** host_id tocando o teste agora (desabilita o botão até acabar), ou null. */
  protected readonly testingVoice = signal<string | null>(null);
  /** host_id cujo último teste FALHOU (mostra erro inline na linha dele) + a
   * mensagem — limpo assim que um novo teste começa. */
  protected readonly testVoiceErrorHostId = signal<string | null>(null);
  protected readonly testVoiceErrorMsg = signal<string>('');

  /** host_id instalando um motor agora (mostra progresso), ou null. */
  protected readonly installingHostId = signal<string | null>(null);
  protected readonly installErrorHostId = signal<string | null>(null);
  protected readonly installErrorMsg = signal<string>('');

  protected onVolumeInput(ev: Event): void {
    this.jarvisAudio.setVolume(Number((ev.target as HTMLInputElement).value));
  }

  /**
   * Opções do motor de voz PRA ESTE HOST — só oferece o que é compatível
   * (ex.: "say" nem aparece fora do Mac) e, pra motores instaláveis (hoje só
   * o Piper) que ainda não estão presentes, rotula "— instalar" em vez de
   * deixar escolher e falhar calado. Sem `tts_engines` (worker mais antigo,
   * ainda não reiniciou com esse código) cai no comportamento de sempre —
   * mostra todas as opções, sem saber ao certo o que está instalado.
   */
  protected engineOptions(
    engines: WorkerStatus['tts_engines'] | null | undefined,
  ): { value: string; label: string; needsInstall: boolean }[] {
    const opts: { value: string; label: string; needsInstall: boolean }[] = [
      { value: '', label: 'Padrão do host', needsInstall: false },
    ];
    const add = (value: string, label: string, installLabel?: string) => {
      if (!engines) {
        opts.push({ value, label, needsInstall: false });
        return;
      }
      const e = engines[value];
      if (e?.installed) {
        opts.push({ value, label, needsInstall: false });
      } else if (e?.installable && installLabel) {
        opts.push({ value, label: installLabel, needsInstall: true });
      }
    };
    add('say', 'Nativo do SO (say)');
    add('xtts', 'Alta qualidade (XTTS)');
    add('piper', 'Leve (Piper)', 'Leve (Piper) — instalar');
    add('api', 'API hospedada');
    return opts;
  }

  /** Salva o modo de TTS deste host — manda o voice_effect ATUAL junto (evita
   * apagar um pelo outro, mesmo padrão do nome/emoji). Se o motor escolhido
   * ainda não está instalado (mas dá pra instalar sozinho), instala PRIMEIRO
   * — com indicador de progresso — e só então salva a escolha. */
  protected onTtsModeChange(
    hostId: string,
    currentVoiceEffect: boolean | null | undefined,
    engines: WorkerStatus['tts_engines'] | null | undefined,
    ev: Event,
  ): void {
    const mode = (ev.target as HTMLSelectElement).value || null;
    const entry = mode ? engines?.[mode] : null;
    if (mode && entry && !entry.installed && entry.installable) {
      this.installEngineThenSave(hostId, mode, currentVoiceEffect);
      return;
    }
    this.api.setWorkerAudioSettings(hostId, mode, currentVoiceEffect ?? null).subscribe({
      next: () => this.onAudioSettingsSaved(hostId),
      error: () => {
        /* best-effort: valor no <select> volta a refletir o real no próximo refresh */
      },
    });
  }

  /** Pede a instalação (ex.: Piper) e, quando o heartbeat confirmar que
   * terminou, salva o modo escolhido — é só aí que a troca "conta" de
   * verdade (evita salvar um motor que falhou na instalação). */
  private installEngineThenSave(
    hostId: string,
    engine: string,
    currentVoiceEffect: boolean | null | undefined,
  ): void {
    this.installingHostId.set(hostId);
    this.installErrorHostId.set(null);
    this.api.installTtsEngine(hostId, engine).subscribe({
      next: () =>
        this.pollForInstall(hostId, engine, currentVoiceEffect, Date.now() + 60_000),
      error: () => {
        this.installingHostId.set(null);
        this.installErrorHostId.set(hostId);
        this.installErrorMsg.set('Falha ao pedir a instalação (API indisponível?).');
      },
    });
  }

  private pollForInstall(
    hostId: string,
    engine: string,
    currentVoiceEffect: boolean | null | undefined,
    deadline: number,
  ): void {
    this.api.listWorkers().subscribe({
      next: (list) => {
        const match = list.find((w) => w.host_id === hostId);
        if (match?.tts_engines?.[engine]?.installed) {
          this.installingHostId.set(null);
          this.workers.refresh();
          this.api.setWorkerAudioSettings(hostId, engine, currentVoiceEffect ?? null).subscribe({
            next: () => this.onAudioSettingsSaved(hostId),
            error: () => {
              /* best-effort */
            },
          });
          return;
        }
        if (Date.now() > deadline) {
          this.installingHostId.set(null);
          this.installErrorHostId.set(hostId);
          this.installErrorMsg.set('A instalação demorou demais ou falhou.');
          return;
        }
        setTimeout(() => this.pollForInstall(hostId, engine, currentVoiceEffect, deadline), 2000);
      },
      error: () => setTimeout(() => this.pollForInstall(hostId, engine, currentVoiceEffect, deadline), 2000),
    });
  }

  protected onVoiceEffectChange(
    hostId: string,
    currentMode: string | null | undefined,
    ev: Event,
  ): void {
    const checked = (ev.target as HTMLInputElement).checked;
    this.api.setWorkerAudioSettings(hostId, currentMode ?? null, checked).subscribe({
      next: () => this.onAudioSettingsSaved(hostId),
      error: () => {
        /* best-effort */
      },
    });
  }

  private onAudioSettingsSaved(hostId: string): void {
    this.workers.refresh();
    if (this.worker()?.host_id === hostId) {
      this.reloadWorker();
    }
  }

  /**
   * Pede pro worker deste host sintetizar+tocar uma frase de teste.
   *
   * A síntese pode falhar CALADA no worker (ex.: motor selecionado não está
   * de fato instalado nesse host — aconteceu de verdade: "piper" escolhido
   * num Mac sem o binário) — antes disso não dava NENHUM feedback, o botão
   * só ficava "Tocando…" e nada acontecia. Agora observamos de verdade se o
   * áudio COMEÇOU a tocar (`jarvisAudio.speaking()`, o mesmo sinal que already
   * reflete o clipe real chegando via SSE); sem isso em ~7s, mostra erro
   * inline na linha do host em vez de falhar silenciosamente.
   */
  protected testVoice(hostId: string): void {
    if (this.testingVoice()) {
      return;
    }
    this.testingVoice.set(hostId);
    this.testVoiceErrorHostId.set(null);
    this.api.testJarvisVoice(hostId).subscribe({
      next: () => this.pollForTestPlayback(hostId, Date.now() + 7000),
      error: () => {
        this.testingVoice.set(null);
        this.testVoiceErrorHostId.set(hostId);
        this.testVoiceErrorMsg.set('Falha ao pedir o teste (API indisponível?).');
      },
    });
  }

  private pollForTestPlayback(hostId: string, deadline: number): void {
    if (this.jarvisAudio.speaking()) {
      this.testingVoice.set(null); // áudio chegou e começou a tocar — sucesso
      return;
    }
    if (Date.now() > deadline) {
      this.testingVoice.set(null);
      this.testVoiceErrorHostId.set(hostId);
      this.testVoiceErrorMsg.set(
        'Não tocou nada — o motor de voz escolhido pode não estar instalado nesse host.',
      );
      return;
    }
    setTimeout(() => this.pollForTestPlayback(hostId, deadline), 300);
  }

  /** Limites do Claude (barras de % sessão/semana), ou null se sem dados. */
  readonly claudeLimits = computed(() => this.usage()?.claude ?? null);

  /** Active right now = running or waiting_input. */
  readonly activeNow = computed(
    () =>
      this.sessions().filter((s) => FLEET_ACTIVE_STATUSES.includes(s.status)).length,
  );

  /**
   * Sessions "today". The Session model carries no reliable created/updated
   * timestamp, so we cannot filter by date with confidence. We surface the
   * total session count as the closest honest figure rather than fabricating
   * a per-day number.
   */
  readonly sessionsToday = computed(() => this.sessions().length);

  /** Local-only settings state (no persistence/endpoint yet — Fase 2). */
  private readonly realtimeEnabled = signal(true);
  private readonly darkEnabled = signal(true);
  /** Auto-instruir sessões a trabalhar em tarefas/marcos (setting global). */
  private readonly milestonesAuto = signal(true);
  /** JARVIS (voz) ligado para TODAS as sessões (atalho global). */
  protected readonly jarvisAll = signal(false);
  /** JARVIS COMPLETO (picker + resposta por voz) para TODAS as sessões. */
  protected readonly jarvisFullAll = signal(false);
  /** Compressão pós-transcrição via SLM local (Ollama). Default off. */
  private readonly voiceDedupEnabled = signal(false);
  /** Dedup automático do TEXTO digitado (mesmo Ollama/SLM). Roda no send(). */
  private readonly textDedupEnabled = signal(false);
  /** Keep-alive do modelo Ollama (ping a cada 120s para não descarregar). */
  private readonly keepModelWarm = signal(false);
  /** Glossário de termos técnicos (hint do Whisper na transcrição de áudio) —
   * fonte da verdade é a string CSV persistida; a UI mostra/edita como chips. */
  protected readonly transcriptionGlossary = signal('');
  /** Termos já confirmados em chip, derivados da string persistida. */
  protected readonly glossaryChips = computed(() =>
    this.transcriptionGlossary()
      .split(/[,;\n]/)
      .map((t) => t.trim())
      .filter(Boolean),
  );
  /** Texto ainda não convertido em chip (o que o usuário está digitando/colou
   * mas ainda não bateu num separador ou Enter). */
  protected readonly glossaryDraft = signal('');

  protected readonly installingOllama = signal<boolean>(false);
  protected readonly ollamaStatus = computed(() => {
    const w = this.worker();
    return w?.ollama_status || null;
  });

  readonly settings = computed<SettingRow[]>(() => [
    // ── Notificações: como o app te chama atenção ─────────────────────────
    {
      key: 'push',
      group: 'notif',
      kind: 'toggle',
      title: 'Notificações',
      value: this.notify.permission() === 'granted',
      // Sem suporte ou já negada pelo SO → não dá pra ligar pelo app.
      disabled:
        this.notify.permission() === 'unsupported' ||
        this.notify.permission() === 'denied',
    },
    {
      key: 'realtime',
      group: 'notif',
      kind: 'toggle',
      title: 'Tempo real (SSE)',
      sub: 'Atualização da tela sem precisar recarregar.',
      value: this.realtimeEnabled(),
    },
    {
      key: 'cues',
      group: 'notif',
      kind: 'value',
      title: 'Som de notificação',
      sub: 'Toque um bip, Voz uma frase, ou Desligado.',
      display: cueLabel(this.cues.mode()),
    },
    // ── Agente: comportamento das sessões ──────────────────────────────────
    {
      key: 'milestones',
      group: 'agente',
      kind: 'toggle',
      title: 'Trabalhar em tarefas',
      sub: 'Auto-instrui cada sessão a manter um plano de tarefas/marcos.',
      value: this.milestonesAuto(),
    },
    {
      key: 'voice_dedup',
      group: 'economia',
      kind: 'toggle',
      title: 'Economizar tokens no áudio',
      sub: 'Remove repetições da transcrição de voz antes de enviar (SLM local via Ollama — requer qwen3:0.6b ou similar instalado).',
      value: this.voiceDedupEnabled(),
    },
    {
      key: 'text_dedup',
      group: 'economia',
      kind: 'toggle',
      title: 'Economizar tokens no texto',
      sub: 'Compacta o texto digitado no Enviar (mesmo SLM local; preserva comandos/URLs/emojis/quebras).',
      value: this.textDedupEnabled(),
    },
    {
      key: 'keep_warm',
      group: 'economia',
      kind: 'toggle',
      title: 'Manter modelo sempre carregado',
      sub: 'Ping a cada 2 min pra Ollama não descarregar o modelo de dedup após idle (~5min). Evita o load de ~2s no 1º envio depois de parado.',
      value: this.keepModelWarm(),
    },
    // ── Aparência: visual + idioma ────────────────────────────────────────
    {
      key: 'dark',
      group: 'aparencia',
      kind: 'toggle',
      title: 'Tema escuro',
      value: this.darkEnabled(),
    },
    {
      key: 'lang',
      group: 'aparencia',
      kind: 'value',
      title: 'Idioma',
      display: 'Português (BR)',
    },
  ]);

  private lastEventCount = 0;

  constructor() {
    // Live updates: re-fetch sessions whenever the SSE event buffer grows.
    effect(() => {
      const count = this.sse.events().length;
      if (count !== this.lastEventCount) {
        this.lastEventCount = count;
        this.reloadSessions();
      }
    });
  }

  private pollTimer: ReturnType<typeof setInterval> | null = null;

  ngOnInit(): void {
    if (!this.sse.connected()) {
      this.sse.connect();
    }
    this.reloadSessions();
    this.reloadWorker();
    this.reloadUsage();
    // Setting global de auto-instruir tarefas.
    this.api.getSettings().subscribe({
      next: (s) => {
        this.milestonesAuto.set(s.milestones_auto);
        this.jarvisAll.set(!!s.jarvis_all);
        this.jarvisFullAll.set(!!s.jarvis_full_all);
        this.voiceDedupEnabled.set(!!s.voice_dedup_enabled);
        this.textDedupEnabled.set(!!s.text_dedup_enabled);
        // Bug preexistente: faltava carregar estes 2 no load (só setavam no
        // toggle) — o switch/textarea voltava sempre pro default ao reabrir.
        this.keepModelWarm.set(!!s.keep_model_warm);
        this.transcriptionGlossary.set(s.transcription_glossary || '');
      },
      error: () => {
        /* mantém default (on) */
      },
    });
    // Versão deployada (rodapé) — não crítico, falha em silêncio.
    this.api.getVersion().subscribe({
      next: (v) => this.appVersion.set(v.version && v.version !== 'unknown' ? v.version : null),
      error: () => {
        /* instância antiga sem /version — sem rodapé */
      },
    });
    // Foto vem do servidor (vale em qualquer dispositivo).
    this.api.getProfile().subscribe({
      next: (p) => this.photo.set(p.photo ?? null),
      error: () => {
        /* sem foto / offline — mantém vazio */
      },
    });
    // Worker faz heartbeat a cada ~10s; atualizamos status/limites a cada 15s.
    this.pollTimer = setInterval(() => {
      this.reloadWorker();
      this.workers.refresh();
      this.reloadUsage();
    }, 15000);
  }

  ngOnDestroy(): void {
    if (this.pollTimer !== null) {
      clearInterval(this.pollTimer);
    }
  }

  private reloadSessions(): void {
    this.api.listSessions().subscribe({
      next: (list) => this.sessions.set(list ?? []),
      error: () => {
        /* keep last known state */
      },
    });
  }

  /**
   * Recarrega o card do worker "principal" MANTENDO A IDENTIDADE do host.
   *
   * Bug real (multi-host, reportado pelo usuário): `GET /worker` devolve o
   * host de `updated_at` MAIS RECENTE entre TODOS — com 2+ hosts ativos
   * (cada um faz heartbeat a cada ~10s independente), "o mais recente" fica
   * alternando entre eles a cada poll (aqui, a cada 15s), fazendo o card
   * principal — e a config de áudio dele — trocar de host sozinho na tela,
   * mesmo sem o usuário mexer em nada. Fix: 1ª carga usa o endpoint singular
   * (aproxima "host mais ativo agora"); da 2ª em diante, TRAVA nesse
   * `host_id` e busca ELE especificamente na lista completa — nunca deixa a
   * "identidade" do card principal mudar sozinha.
   */
  private reloadWorker(): void {
    const lockedHostId = this.worker()?.host_id;
    if (!lockedHostId) {
      this.api.getWorker().subscribe({
        next: (w) => this.worker.set(w),
        error: () => {
          /* keep last known state */
        },
      });
      return;
    }
    this.api.listWorkers().subscribe({
      next: (list) => {
        const match = list.find((w) => w.host_id === lockedHostId);
        if (match) {
          this.worker.set(match);
        }
      },
      error: () => {
        /* keep last known state */
      },
    });
  }

  private reloadUsage(): void {
    this.api.getUsage().subscribe({
      next: (u) => this.usage.set(u),
      error: () => {
        /* keep last known state */
      },
    });
  }

  /** Row tap — value rows (e.g. Idioma) are placeholders for now. */
  onRowClick(s: SettingRow): void {
    if (s.kind === 'value') {
      if (s.key === 'cues') {
        // Tri-estado: cicla off → chime → voice → off a cada toque.
        const order: CueMode[] = ['off', 'chime', 'voice'];
        const next = order[(order.indexOf(this.cues.mode()) + 1) % order.length];
        this.cues.setMode(next);
        return;
      }
      // 'lang' link — no language switcher wired yet.
    }
  }

  /** Toggles a local setting. Push stays locked (Fase 2). */
  toggle(key: SettingRow['key']): void {
    switch (key) {
      case 'push':
        // Só dá pra PEDIR a permissão; revogar é só nas configs do SO.
        if (this.notify.permission() !== 'granted') {
          void this.notify.requestPermission();
        }
        break;
      case 'realtime':
        this.realtimeEnabled.update((v) => !v);
        break;
      case 'dark':
        this.darkEnabled.update((v) => !v);
        break;
      case 'milestones': {
        const next = !this.milestonesAuto();
        this.milestonesAuto.set(next); // otimista
        this.api
          .setSettings({
            milestones_auto: next,
            jarvis_all: this.jarvisAll(),
            jarvis_full_all: this.jarvisFullAll(),
            voice_dedup_enabled: this.voiceDedupEnabled(),
            voice_dedup_model: 'qwen3:0.6b',
            text_dedup_enabled: this.textDedupEnabled(),
            keep_model_warm: this.keepModelWarm(),
            transcription_glossary: this.transcriptionGlossary(),
          })
          .subscribe({
            next: (s) => this.milestonesAuto.set(s.milestones_auto),
            error: () => this.milestonesAuto.set(!next), // reverte em erro
          });
        break;
      }
      case 'jarvis': {
        this.setJarvisAll(!this.jarvisAll());
        break;
      }
      case 'jarvis_full': {
        this.setJarvisFullAll(!this.jarvisFullAll());
        break;
      }
      case 'voice_dedup': {
        const next = !this.voiceDedupEnabled();
        this.voiceDedupEnabled.set(next); // otimista
        this.api
          .setSettings({
            milestones_auto: this.milestonesAuto(),
            jarvis_all: this.jarvisAll(),
            jarvis_full_all: this.jarvisFullAll(),
            voice_dedup_enabled: next,
            voice_dedup_model: 'qwen3:0.6b',
            text_dedup_enabled: this.textDedupEnabled(),
            keep_model_warm: this.keepModelWarm(),
            transcription_glossary: this.transcriptionGlossary(),
          })
          .subscribe({
            next: (s) => this.voiceDedupEnabled.set(!!s.voice_dedup_enabled),
            error: () => this.voiceDedupEnabled.set(!next), // reverte em erro
          });
        break;
      }
      case 'text_dedup': {
        const next = !this.textDedupEnabled();
        this.textDedupEnabled.set(next); // otimista
        this.api
          .setSettings({
            milestones_auto: this.milestonesAuto(),
            jarvis_all: this.jarvisAll(),
            jarvis_full_all: this.jarvisFullAll(),
            voice_dedup_enabled: this.voiceDedupEnabled(),
            voice_dedup_model: 'qwen3:0.6b',
            text_dedup_enabled: next,
            keep_model_warm: this.keepModelWarm(),
            transcription_glossary: this.transcriptionGlossary(),
          })
          .subscribe({
            next: (s) => this.textDedupEnabled.set(!!s.text_dedup_enabled),
            error: () => this.textDedupEnabled.set(!next), // reverte em erro
          });
        break;
      }
      case 'keep_warm': {
        const next = !this.keepModelWarm();
        this.keepModelWarm.set(next); // otimista
        this.api
          .setSettings({
            milestones_auto: this.milestonesAuto(),
            jarvis_all: this.jarvisAll(),
            jarvis_full_all: this.jarvisFullAll(),
            voice_dedup_enabled: this.voiceDedupEnabled(),
            voice_dedup_model: 'qwen3:0.6b',
            text_dedup_enabled: this.textDedupEnabled(),
            keep_model_warm: next,
            transcription_glossary: this.transcriptionGlossary(),
          })
          .subscribe({
            next: (s) => this.keepModelWarm.set(!!s.keep_model_warm),
            error: () => this.keepModelWarm.set(!next), // reverte em erro
          });
        break;
      }
      default:
        // 'lang' has no toggle.
        break;
    }
  }

  /** Persiste JARVIS_all otimista + rollback em erro. Reusado pelos toggles
   * da seção "Áudio (JARVIS)" e pela lista geral (caso o user clique numa row
   * legada, se sobrar alguma). */
  private setJarvisAll(next: boolean): void {
    const prev = this.jarvisAll();
    this.jarvisAll.set(next); // otimista
    this.api
      .setSettings({
        milestones_auto: this.milestonesAuto(),
        jarvis_all: next,
        jarvis_full_all: this.jarvisFullAll(),
        voice_dedup_enabled: this.voiceDedupEnabled(),
        voice_dedup_model: 'qwen3:0.6b',
        text_dedup_enabled: this.textDedupEnabled(),
        keep_model_warm: this.keepModelWarm(),
        transcription_glossary: this.transcriptionGlossary(),
      })
      .subscribe({
        next: (s) => this.jarvisAll.set(!!s.jarvis_all),
        error: () => this.jarvisAll.set(prev),
      });
  }

  /** Persiste a lista de chips (join em CSV) — otimista + rollback em erro,
   * mesmo padrão dos toggles de economia. Chamado por add/remove de chip. */
  private saveGlossary(chips: string[]): void {
    const prev = this.transcriptionGlossary();
    const next = chips.join(', ');
    this.transcriptionGlossary.set(next); // otimista
    this.api
      .setSettings({
        milestones_auto: this.milestonesAuto(),
        jarvis_all: this.jarvisAll(),
        jarvis_full_all: this.jarvisFullAll(),
        voice_dedup_enabled: this.voiceDedupEnabled(),
        voice_dedup_model: 'qwen3:0.6b',
        text_dedup_enabled: this.textDedupEnabled(),
        keep_model_warm: this.keepModelWarm(),
        transcription_glossary: next,
      })
      .subscribe({
        next: (s) => this.transcriptionGlossary.set(s.transcription_glossary || ''),
        error: () => this.transcriptionGlossary.set(prev),
      });
  }

  /** Digitação/colagem no input de glossário: cola "QI Tech, BMS\nBMP" e cada
   * termo já separado por vírgula/ponto-e-vírgula/quebra de linha vira chip
   * na hora; o que restar sem separador ainda fica no input (rascunho). */
  protected onGlossaryDraftInput(ev: Event): void {
    const raw = (ev.target as HTMLInputElement).value;
    const parts = raw.split(/[,;\n]/);
    if (parts.length === 1) {
      this.glossaryDraft.set(raw); // ainda sem separador — só rascunho
      return;
    }
    const trailing = parts.pop() ?? ''; // sobra após o último separador
    const newTerms = parts.map((t) => t.trim()).filter(Boolean);
    this.glossaryDraft.set(trailing.trimStart());
    if (newTerms.length) this.addGlossaryTerms(newTerms);
  }

  /** Enter no input também fecha o termo em digitação (sem precisar separador). */
  protected onGlossaryDraftEnter(ev: Event): void {
    ev.preventDefault();
    const term = this.glossaryDraft().trim();
    this.glossaryDraft.set('');
    if (term) this.addGlossaryTerms([term]);
  }

  private addGlossaryTerms(terms: string[]): void {
    const existing = this.glossaryChips();
    const merged = [...existing];
    for (const t of terms) {
      if (!merged.some((e) => e.toLowerCase() === t.toLowerCase())) merged.push(t);
    }
    this.saveGlossary(merged);
  }

  /** Fecha (remove) um chip — o X clicado. */
  protected removeGlossaryTerm(term: string): void {
    this.saveGlossary(this.glossaryChips().filter((t) => t !== term));
  }

  /** Idem para JARVIS COMPLETO (escuta de microfone em toda sessão). */
  private setJarvisFullAll(next: boolean): void {
    const prev = this.jarvisFullAll();
    this.jarvisFullAll.set(next); // otimista
    this.api
      .setSettings({
        milestones_auto: this.milestonesAuto(),
        jarvis_all: this.jarvisAll(),
        jarvis_full_all: next,
        voice_dedup_enabled: this.voiceDedupEnabled(),
        voice_dedup_model: 'qwen3:0.6b',
        text_dedup_enabled: this.textDedupEnabled(),
        keep_model_warm: this.keepModelWarm(),
        transcription_glossary: this.transcriptionGlossary(),
      })
      .subscribe({
        next: (s) => this.jarvisFullAll.set(!!s.jarvis_full_all),
        error: () => this.jarvisFullAll.set(prev),
      });
  }

  /** Handler do checkbox inline na seção Áudio (mesma lógica do setJarvisAll). */
  protected onJarvisAllToggle(ev: Event): void {
    this.setJarvisAll((ev.target as HTMLInputElement).checked);
  }

  /** Handler do checkbox inline de modo completo. */
  protected onJarvisFullAllToggle(ev: Event): void {
    this.setJarvisFullAll((ev.target as HTMLInputElement).checked);
  }

  installDedupModel(event: Event): void {
    event.stopPropagation();
    // TODO: reativar quando ApiService expor installOllamaModel (vestígio da
    // feature de meeting). Por ora só ligamos o spinner e liberamos depois —
    // não bloqueia a build do frontend.
    this.installingOllama.set(true);
    setTimeout(() => this.installingOllama.set(false), 30000);
  }

  /** Formata um percentual real (0–100) ou "—" quando ausente. */
  protected fmtPct(p: number | null | undefined): string {
    return p == null ? '—' : `${Math.round(p)}%`;
  }

  /** Abre o seletor de arquivo para escolher a foto de perfil. */
  pickPhoto(): void {
    this.fileInput()?.nativeElement.click();
  }

  /** Lê a imagem escolhida, redimensiona e persiste como data URL. */
  onPhotoSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) {
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const src = String(reader.result);
      this.downscale(src)
        .then((dataUrl) => this.persistPhoto(dataUrl))
        // Se o canvas falhar, tenta o original (melhor que perder a foto).
        .catch(() => this.persistPhoto(src));
    };
    reader.readAsDataURL(file);
    // Permite re-selecionar o mesmo arquivo depois.
    input.value = '';
  }

  /** Salva a foto NO SERVIDOR (não em localStorage) e reflete na UI. */
  private persistPhoto(dataUrl: string): void {
    this.photo.set(dataUrl); // otimista
    this.api.setProfilePhoto(dataUrl).subscribe({
      next: (r) => this.photo.set(r.photo ?? dataUrl),
      error: () => {
        /* mantém a otimista; próximo load reconcilia */
      },
    });
  }

  /** Remove a foto no servidor e volta para o avatar com a inicial. */
  removePhoto(): void {
    this.photo.set(null);
    this.api.clearProfilePhoto().subscribe({
      next: () => this.photo.set(null),
      error: () => {
        /* ignora */
      },
    });
  }

  /**
   * Redimensiona a imagem para no máximo {@link PHOTO_MAX_SIDE}px de lado
   * (mantendo proporção) e devolve um JPEG comprimido — evita estourar a
   * cota do localStorage com fotos grandes.
   */
  private downscale(src: string): Promise<string> {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const scale = Math.min(1, PHOTO_MAX_SIDE / Math.max(img.width, img.height));
        const w = Math.round(img.width * scale);
        const h = Math.round(img.height * scale);
        const canvas = document.createElement('canvas');
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext('2d');
        if (!ctx) {
          reject(new Error('no 2d context'));
          return;
        }
        ctx.drawImage(img, 0, 0, w, h);
        resolve(canvas.toDataURL('image/jpeg', 0.85));
      };
      img.onerror = () => reject(new Error('image load failed'));
      img.src = src;
    });
  }

  /** Instala o app: prompt nativo (Chromium) ou instruções no iOS. */
  async installApp(): Promise<void> {
    if (this.pwa.canPrompt()) {
      // canInstall é computed → reage sozinho quando o prompt é consumido.
      await this.pwa.promptInstall();
      return;
    }
    if (this.pwa.isIos) {
      this.showIosHelp.update((v) => !v);
    }
  }

  /** Dispara uma notificação do sistema de teste (diagnóstico). */
  testNotify(): void {
    void this.notify.notify('SessionFlow', {
      body: 'Notificação de teste ✅ — está funcionando!',
      tag: 'sf-test',
    });
  }

  /** Mensagem de resultado do teste de vibração (diagnóstico). */
  protected readonly vibeMsg = signal<string>('');

  /**
   * Testa a Vibration API DIRETO (dentro do gesto do clique — requisito do
   * Android/Chrome), separado da notificação, e mostra o resultado. Isola se o
   * problema é a vibração em si (não suportada / bloqueada / silencioso) ou o
   * caminho da notificação.
   */
  testVibrate(): void {
    const nav = navigator as Navigator & { vibrate?: (p: number | number[]) => boolean };
    if (typeof nav.vibrate !== 'function') {
      this.vibeMsg.set('❌ Este navegador não suporta vibração (ex.: iOS/Safari).');
      return;
    }
    // Padrão longo e forte p/ ser fácil de sentir no teste.
    const ok = nav.vibrate([400, 120, 400]);
    this.vibeMsg.set(
      ok
        ? '📳 Comando enviado. Se não sentiu: veja o modo silencioso/Não Perturbe e a vibração do canal de notificações do app nas configs do Android.'
        : '⚠️ O navegador recusou a vibração (silencioso/economia de bateria ou sem interação recente).',
    );
  }

  /** Em progresso o "recarregar app" (evita clique duplo). */
  protected readonly reloading = signal(false);

  /**
   * Limpa o cache do PWA (Service Worker + Cache Storage) e recarrega — força
   * baixar a versão mais nova. Necessário porque o pull-to-refresh está
   * desabilitado (comportamento de app instalado).
   */
  async reloadApp(): Promise<void> {
    if (this.reloading()) {
      return;
    }
    this.reloading.set(true);
    try {
      if ('serviceWorker' in navigator) {
        const regs = await navigator.serviceWorker.getRegistrations();
        await Promise.all(regs.map((r) => r.unregister()));
      }
      if ('caches' in window) {
        const keys = await caches.keys();
        await Promise.all(keys.map((k) => caches.delete(k)));
      }
    } catch {
      /* best-effort — recarrega mesmo se limpar falhar */
    }
    // `location.reload()` após limpar o SW/caches busca tudo fresco do servidor.
    location.reload();
  }

  /** Encerra a sessão e volta para o login. */
  logout(): void {
    this.auth.logout();
    this.router.navigate(['/login']);
  }

  /** Email do usuário logado (era hardcoded "sessionflow.local"). */
  protected readonly email = computed(() => this.auth.email() ?? '');

  /** Rótulo humano do grupo pro section header do Perfil. */
  protected groupLabel(g: SettingRow['group']): string {
    return groupLabelImpl(g);
  }

  /** Nome derivado do email (parte antes do @ e do 1º ponto), capitalizado —
   * era hardcoded "Diego", aparecendo até em outras contas (ex.: Heverton). */
  protected readonly displayName = computed(() => {
    const local = this.email().split('@')[0]?.split('.')[0] || '';
    return local ? local.charAt(0).toUpperCase() + local.slice(1) : 'Operador';
  });
}

/** Texto curto do estado dos avisos de evento (linha tri-estado do Perfil). */
function cueLabel(mode: CueMode): string {
  switch (mode) {
    case 'chime':
      return 'Toque';
    case 'voice':
      return 'Voz';
    default:
      return 'Desligado';
  }
}

/** Formata segundos de uptime em "Xd Yh", "Xh Ymin" ou "Xmin". */
function formatUptime(seconds: number | null | undefined): string {
  if (seconds == null || seconds < 0) {
    return '—';
  }
  const m = Math.floor(seconds / 60);
  if (m < 1) {
    return '<1min';
  }
  const days = Math.floor(m / 1440);
  const hours = Math.floor((m % 1440) / 60);
  const mins = m % 60;
  if (days > 0) {
    return `${days}d ${hours}h`;
  }
  if (hours > 0) {
    return `${hours}h ${mins}min`;
  }
  return `${mins}min`;
}

/** A single row in the settings list (toggle or read-only value). */
interface SettingRow {
  key: 'push' | 'realtime' | 'dark' | 'lang' | 'milestones' | 'jarvis' | 'jarvis_full' | 'cues' | 'voice_dedup' | 'text_dedup' | 'keep_warm';
  kind: 'toggle' | 'value';
  /** Agrupa opções relacionadas — o template renderiza um section header quando
   * o grupo muda, em vez de jogar tudo num único bloco embolado. */
  group: 'notif' | 'agente' | 'economia' | 'aparencia';
  title: string;
  /** Linha de apoio (explica o que o item faz). */
  sub?: string;
  /** Toggle state (toggle rows only). */
  value?: boolean;
  /** Read-only display text (value rows only). */
  display?: string;
  disabled?: boolean;
  soon?: boolean;
}

/** Rótulo humano de cada grupo pro section header. */
function groupLabelImpl(g: SettingRow['group']): string {
  switch (g) {
    case 'notif': return 'Notificações';
    case 'agente': return 'Agente';
    case 'economia': return 'Economia';
    case 'aparencia': return 'Aparência';
  }
}
