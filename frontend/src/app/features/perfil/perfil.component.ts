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
  SessionStatus,
  UsageInfo,
  WorkerHardware,
  WorkerStatus,
} from '../../core/models';

/** Lado máximo (px) para onde a foto é redimensionada antes de enviar. */
const PHOTO_MAX_SIDE = 256;

/** Session statuses considered "active right now". */
const ACTIVE_STATUSES: readonly SessionStatus[] = ['running', 'waiting_input'];

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

      <!-- Worker status -->
      <div class="sf-worker">
        <span
          class="sf-worker-dot"
          [class.sf-pulse]="connected()"
          [style.background]="connected() ? '#34D399' : '#7A8090'"
        ></span>
        <div class="sf-worker-body">
          @if (editingHostId() === primaryHostId() && primaryHostId()) {
            <span class="sf-worker-edit-row">
              <input
                #editEmoji
                class="sf-worker-edit-emoji mono"
                [value]="editEmojiValue()"
                (input)="editEmojiValue.set(editEmoji.value)"
                (keydown.enter)="saveEditName(primaryHostId()!)"
                (keydown.escape)="cancelEditName()"
                placeholder="🍎"
                maxlength="8"
              />
              <input
                #editInput
                class="sf-worker-edit-input mono"
                [value]="editNameValue()"
                (input)="editNameValue.set(editInput.value)"
                (keydown.enter)="saveEditName(primaryHostId()!)"
                (keydown.escape)="cancelEditName()"
                placeholder="Nome deste host"
                autofocus
              />
            </span>
            <span class="sf-worker-edit-acts">
              <button type="button" (click)="saveEditName(primaryHostId()!)">Salvar</button>
              <button type="button" (click)="cancelEditName()">Cancelar</button>
            </span>
          } @else {
            <div class="sf-worker-label">
              <span class="sf-worker-name">{{ workerTitle() }}</span>
              @if (primaryHostId()) {
                <button
                  type="button"
                  class="sf-worker-edit-btn"
                  (click)="startEditName(primaryHostId(), worker()?.display_name ?? null, worker()?.emoji ?? null)"
                  aria-label="Renomear/trocar emoji deste host"
                  title="Renomear/trocar emoji deste host"
                >✎</button>
              }
            </div>
            <div class="sf-worker-meta">{{ workerMeta() }}</div>
            @if (worker()?.hardware; as hw) {
              <button
                type="button"
                class="sf-hw-summary"
                (click)="toggleHardware(primaryHostId())"
                aria-label="Ver detalhes de hardware deste host"
              >
                <span class="sf-hw-stats">
                  @for (p of hwSummaryParts(hw); track p) {
                    <span class="sf-hw-stat">{{ p }}</span>
                  }
                </span>
                <span class="sf-hw-caret">{{ expandedHostId() === primaryHostId() ? '▲' : '▼' }}</span>
              </button>
              @if (expandedHostId() === primaryHostId()) {
                <div class="sf-hw-detail">
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
                </div>
              }
            }
          }
        </div>
        <span
          class="sf-worker-pill"
          [style.color]="connected() ? '#34D399' : '#8A90A0'"
          [style.background]="
            connected() ? 'rgba(52,211,153,.14)' : 'rgba(138,144,160,.14)'
          "
        >
          {{ connected() ? 'online' : 'offline' }}
        </span>
      </div>

      <!-- Outros hosts (multi-host) — só aparece com >1 host ativo. -->
      @for (w of otherWorkers(); track w.host_id) {
        <div class="sf-worker">
          <span
            class="sf-worker-dot"
            [class.sf-pulse]="w.online"
            [style.background]="w.online ? '#34D399' : '#7A8090'"
          ></span>
          <div class="sf-worker-body">
            @if (editingHostId() === w.host_id && w.host_id) {
              <span class="sf-worker-edit-row">
                <input
                  #editEmoji2
                  class="sf-worker-edit-emoji mono"
                  [value]="editEmojiValue()"
                  (input)="editEmojiValue.set(editEmoji2.value)"
                  (keydown.enter)="saveEditName(w.host_id!)"
                  (keydown.escape)="cancelEditName()"
                  placeholder="🦆"
                  maxlength="8"
                />
                <input
                  #editInput2
                  class="sf-worker-edit-input mono"
                  [value]="editNameValue()"
                  (input)="editNameValue.set(editInput2.value)"
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
                  @if (w.emoji) {
                    {{ w.emoji }}
                  } @else {
                    Worker ·
                  }
                  {{ w.display_name || w.hostname || '—' }}
                </span>
                <button
                  type="button"
                  class="sf-worker-edit-btn"
                  (click)="startEditName(w.host_id ?? null, w.display_name ?? null, w.emoji ?? null)"
                  aria-label="Renomear/trocar emoji deste host"
                  title="Renomear/trocar emoji deste host"
                >✎</button>
              </div>
              <div class="sf-worker-meta">
                {{ w.platform ?? '—' }} · uptime {{ formatUptimeFor(w) }}
              </div>
              @if (w.hardware; as hw) {
                <button
                  type="button"
                  class="sf-hw-summary"
                  (click)="toggleHardware(w.host_id ?? null)"
                  aria-label="Ver detalhes de hardware deste host"
                >
                  <span class="sf-hw-stats">
                    @for (p of hwSummaryParts(hw); track p) {
                      <span class="sf-hw-stat">{{ p }}</span>
                    }
                  </span>
                  <span class="sf-hw-caret">{{ expandedHostId() === w.host_id ? '▲' : '▼' }}</span>
                </button>
                @if (expandedHostId() === w.host_id) {
                  <div class="sf-hw-detail">
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
                  </div>
                }
              }
            }
          </div>
          <span
            class="sf-worker-pill"
            [style.color]="w.online ? '#34D399' : '#8A90A0'"
            [style.background]="
              w.online ? 'rgba(52,211,153,.14)' : 'rgba(138,144,160,.14)'
            "
          >
            {{ w.online ? 'online' : 'offline' }}
          </span>
        </div>
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
        @for (s of settings(); track s.key; let first = $first) {
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
            } @else {
              <span class="sf-setting-value">{{ s.display }}</span>
            }
          </div>
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
      @if (gitSha()) {
        <div class="sf-version">v{{ gitSha() }}</div>
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
      /* Resumo de hardware (ícones) + detalhe expandido, dentro do card do
         host — some por padrão, só um botão discreto que expande. */
      .sf-hw-summary {
        display: flex;
        align-items: flex-start;
        gap: 6px;
        margin-top: 6px;
        background: none;
        border: none;
        padding: 0;
        font-size: 11.5px;
        color: #7a8090;
        cursor: pointer;
        font-family: 'JetBrains Mono', monospace;
        text-align: left;
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
  readonly gitSha = signal<string | null>(null);

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

  /**
   * Estatísticas resumidas (ícones) do hardware — o detalhe completo só
   * aparece expandido, pra não virar um card gigante por padrão. Devolve
   * um ARRAY (não uma string única) pra cada estatística ficar num `<span>`
   * próprio no template — assim o `flex-wrap` quebra ENTRE estatísticas
   * (nunca no meio de uma, ex.: "🎮" numa linha e "228GB" sozinho na
   * próxima), que era o problema real reportado.
   */
  protected hwSummaryParts(hw: WorkerHardware | null | undefined): string[] {
    if (!hw) {
      return [];
    }
    const parts: string[] = [];
    if (hw.cpu_cores) {
      parts.push(`🧩 ${hw.cpu_cores}-core`);
    }
    if (hw.ram_total_gb) {
      parts.push(`💾 ${hw.ram_total_gb}GB`);
    }
    if (hw.gpu) {
      parts.push('🎮 GPU');
    }
    const disk = hw.disks?.[0];
    if (disk) {
      parts.push(`💽 ${Math.round(disk.total_gb)}GB`);
    }
    return parts;
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
      this.sessions().filter((s) => ACTIVE_STATUSES.includes(s.status)).length,
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
  private readonly jarvisAll = signal(false);

  readonly settings = computed<SettingRow[]>(() => [
    {
      key: 'push',
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
      kind: 'toggle',
      title: 'Tempo real (SSE)',
      value: this.realtimeEnabled(),
    },
    {
      key: 'milestones',
      kind: 'toggle',
      title: 'Trabalhar em tarefas',
      value: this.milestonesAuto(),
    },
    {
      key: 'jarvis',
      kind: 'toggle',
      title: 'JARVIS — resumo falado',
      sub: 'Lê um resumo do que a sessão fez. Liga p/ TODAS aqui; por sessão, no botão 🔊 dela.',
      value: this.jarvisAll(),
    },
    {
      key: 'dark',
      kind: 'toggle',
      title: 'Tema escuro',
      value: this.darkEnabled(),
    },
    {
      key: 'cues',
      kind: 'value',
      title: 'Som de notificação',
      sub: 'Aviso curto quando algo acontece (início/fim/tarefa). Toque um bip, Voz uma frase, ou Desligado.',
      display: cueLabel(this.cues.mode()),
    },
    {
      key: 'lang',
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
      },
      error: () => {
        /* mantém default (on) */
      },
    });
    // SHA do commit deployado (rodapé) — não crítico, falha em silêncio.
    this.api.getVersion().subscribe({
      next: (v) => this.gitSha.set(v.git_sha && v.git_sha !== 'unknown' ? v.git_sha : null),
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
          .setSettings({ milestones_auto: next, jarvis_all: this.jarvisAll() })
          .subscribe({
            next: (s) => this.milestonesAuto.set(s.milestones_auto),
            error: () => this.milestonesAuto.set(!next), // reverte em erro
          });
        break;
      }
      case 'jarvis': {
        const next = !this.jarvisAll();
        this.jarvisAll.set(next); // otimista
        this.api
          .setSettings({ milestones_auto: this.milestonesAuto(), jarvis_all: next })
          .subscribe({
            next: (s) => this.jarvisAll.set(!!s.jarvis_all),
            error: () => this.jarvisAll.set(!next), // reverte em erro
          });
        break;
      }
      default:
        // 'lang' has no toggle.
        break;
    }
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
  key: 'push' | 'realtime' | 'dark' | 'lang' | 'milestones' | 'jarvis' | 'cues';
  kind: 'toggle' | 'value';
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
