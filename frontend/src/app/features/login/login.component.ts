import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { AuthService } from '../../core/auth.service';

/**
 * Tela de login (rota pública `/login`, fora do shell-nav). Tema dark, com
 * email+senha e, quando o aparelho suporta, login/registro de biometria.
 */
@Component({
  selector: 'sf-login',
  standalone: true,
  imports: [FormsModule],
  template: `
    <div class="login-page">
      <div class="login-card">
        <div class="brand">
          <span class="brand-logo" aria-hidden="true">
            <svg
              width="26"
              height="26"
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
          <h1 class="brand-name">SessionFlow</h1>
        </div>

        <p class="subtitle">Entre para continuar</p>

        <form class="form" (ngSubmit)="onSubmit()">
          <label class="field">
            <span class="label">Email</span>
            <input
              type="email"
              name="email"
              autocomplete="username"
              placeholder="voce@exemplo.com"
              [ngModel]="email()"
              (ngModelChange)="email.set($event)"
              [disabled]="busy()"
              required
            />
          </label>

          <label class="field">
            <span class="label">Senha</span>
            <input
              type="password"
              name="password"
              autocomplete="current-password"
              placeholder="••••••••"
              [ngModel]="password()"
              (ngModelChange)="password.set($event)"
              [disabled]="busy()"
              required
            />
          </label>

          @if (error()) {
            <div class="error" role="alert">{{ error() }}</div>
          }

          <button class="btn btn-primary" type="submit" [disabled]="busy()">
            {{ busy() ? 'Entrando…' : 'Entrar' }}
          </button>
        </form>

        @if (biometricEnabled()) {
          <div class="divider"><span>ou</span></div>
          <button
            class="btn btn-ghost"
            type="button"
            (click)="onBiometricLogin()"
            [disabled]="busy()"
          >
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path d="M12 11a3 3 0 0 0-3 3v3" />
              <path d="M2 16v-2a10 10 0 0 1 18-6" />
              <path d="M5 19.5A9 9 0 0 1 12 7a8.9 8.9 0 0 1 5 1.5" />
              <path d="M8 20.5A11 11 0 0 1 7 16v-2a5 5 0 0 1 10 0v2" />
              <path d="M15 19v-5" />
            </svg>
            Entrar com biometria
          </button>
        }

        @if (offerRegister()) {
          <div class="register-prompt">
            <p>Quer entrar mais rápido na próxima vez?</p>
            <button
              class="btn btn-outline"
              type="button"
              (click)="onActivateBiometric()"
              [disabled]="busy()"
            >
              Ativar biometria neste aparelho
            </button>
          </div>
        }

        @if (info()) {
          <div class="info" role="status">{{ info() }}</div>
        }
      </div>
    </div>
  `,
  styles: [
    `
      :host {
        display: block;
      }
      .login-page {
        min-height: 100dvh;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 24px;
        background:
          radial-gradient(
            1200px 600px at 50% -10%,
            rgba(0, 228, 180, 0.12),
            transparent 60%
          ),
          var(--surface-page, #0e1113);
      }
      .login-card {
        width: 100%;
        max-width: 380px;
        background: var(--surface-card, #181c1b);
        border: 1px solid var(--surface-raised, #20262a);
        border-radius: 18px;
        padding: 32px 26px 28px;
        box-shadow: 0 24px 60px rgba(0, 0, 0, 0.45);
      }
      .brand {
        display: flex;
        align-items: center;
        gap: 12px;
        justify-content: center;
        margin-bottom: 4px;
      }
      .brand-logo {
        width: 44px;
        height: 44px;
        display: grid;
        place-items: center;
        border-radius: 13px;
        background: linear-gradient(150deg, #2cecc4, #00a482);
      }
      .brand-name {
        margin: 0;
        font-size: 22px;
        font-weight: 700;
        color: #eef2f0;
        letter-spacing: -0.01em;
      }
      .subtitle {
        text-align: center;
        color: #8b9490;
        font-size: 14px;
        margin: 6px 0 22px;
      }
      .form {
        display: flex;
        flex-direction: column;
        gap: 14px;
      }
      .field {
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .label {
        font-size: 12.5px;
        color: #9aa3a0;
      }
      input {
        width: 100%;
        box-sizing: border-box;
        padding: 12px 14px;
        border-radius: 11px;
        border: 1px solid var(--surface-raised, #20262a);
        background: var(--surface-inset, #0e1113);
        color: #eef2f0;
        font-size: 15px;
        outline: none;
      }
      input:focus {
        border-color: var(--color-accent, #00e4b4);
        box-shadow: 0 0 0 3px var(--focus-ring, rgba(0, 228, 180, 0.45));
      }
      .btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
        width: 100%;
        padding: 13px 16px;
        border-radius: 12px;
        font-size: 15px;
        font-weight: 600;
        cursor: pointer;
        border: none;
        transition:
          opacity 0.15s ease,
          transform 0.05s ease;
      }
      .btn:disabled {
        opacity: 0.55;
        cursor: not-allowed;
      }
      .btn:active:not(:disabled) {
        transform: translateY(1px);
      }
      .btn-primary {
        margin-top: 4px;
        background: linear-gradient(150deg, #2cecc4, #00a482);
        color: #06231d;
      }
      .btn-ghost {
        background: var(--surface-inset, #0e1113);
        border: 1px solid var(--surface-raised, #20262a);
        color: #eef2f0;
      }
      .btn-outline {
        background: transparent;
        border: 1px solid var(--color-accent, #00e4b4);
        color: var(--color-accent, #00e4b4);
      }
      .error {
        background: rgba(239, 68, 68, 0.12);
        border: 1px solid rgba(239, 68, 68, 0.35);
        color: #fca5a5;
        font-size: 13px;
        padding: 9px 12px;
        border-radius: 10px;
      }
      .info {
        margin-top: 14px;
        text-align: center;
        font-size: 13px;
        color: var(--color-accent, #00e4b4);
      }
      .divider {
        display: flex;
        align-items: center;
        gap: 12px;
        margin: 18px 0;
        color: #6b7280;
        font-size: 12px;
      }
      .divider::before,
      .divider::after {
        content: '';
        flex: 1;
        height: 1px;
        background: var(--surface-raised, #20262a);
      }
      .register-prompt {
        margin-top: 18px;
        padding-top: 18px;
        border-top: 1px solid var(--surface-raised, #20262a);
        text-align: center;
      }
      .register-prompt p {
        margin: 0 0 12px;
        font-size: 13px;
        color: #9aa3a0;
      }
    `,
  ],
})
export class LoginComponent implements OnInit {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  readonly email = signal('');
  readonly password = signal('');
  readonly busy = signal(false);
  readonly error = signal('');
  readonly info = signal('');

  /** Biometria disponível (backend + browser) — controla os botões. */
  readonly biometricEnabled = signal(false);
  /** Oferecer ativar biometria após login por senha bem-sucedido. */
  readonly offerRegister = signal(false);

  async ngOnInit(): Promise<void> {
    // Já logado? Pula direto pro app.
    if (this.auth.isAuthenticated()) {
      this.router.navigate(['/inicio']);
      return;
    }
    this.biometricEnabled.set(await this.auth.biometricAvailable());
  }

  async onSubmit(): Promise<void> {
    if (this.busy()) return;
    this.error.set('');
    this.info.set('');
    this.busy.set(true);
    try {
      await this.auth.login(this.email().trim(), this.password());
      // Aparelho suporta biometria e ainda não ativou? Oferece antes de seguir.
      if (this.biometricEnabled() && !this.auth.hasBiometricOnDevice()) {
        this.offerRegister.set(true);
        this.busy.set(false);
        return;
      }
      this.router.navigate(['/inicio']);
    } catch {
      this.error.set('Credenciais inválidas');
      this.busy.set(false);
    }
  }

  async onBiometricLogin(): Promise<void> {
    if (this.busy()) return;
    this.error.set('');
    this.info.set('');
    this.busy.set(true);
    try {
      await this.auth.loginWithBiometric();
      this.router.navigate(['/inicio']);
    } catch {
      this.error.set('Não foi possível entrar com biometria');
      this.busy.set(false);
    }
  }

  async onActivateBiometric(): Promise<void> {
    if (this.busy()) return;
    this.error.set('');
    this.busy.set(true);
    try {
      await this.auth.registerBiometric();
      this.info.set('Biometria ativada neste aparelho.');
      this.offerRegister.set(false);
      setTimeout(() => this.router.navigate(['/inicio']), 700);
    } catch {
      this.error.set('Não foi possível ativar a biometria');
      // Mesmo se falhar o registro, o login por senha já valeu — segue pro app.
      setTimeout(() => this.router.navigate(['/inicio']), 1200);
    } finally {
      this.busy.set(false);
    }
  }
}
