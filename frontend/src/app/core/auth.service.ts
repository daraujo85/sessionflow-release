import { HttpClient } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import {
  startAuthentication,
  startRegistration,
} from '@simplewebauthn/browser';
import { API_BASE_URL } from './api.service';

/** Resposta de login (senha ou biometria). */
interface LoginResponse {
  token: string;
  expires_in: number;
  email: string;
}

const TOKEN_KEY = 'sf.auth.token';
const EXPIRES_KEY = 'sf.auth.expiresAt';
const EMAIL_KEY = 'sf.auth.email';
/** Flag: biometria já registrada NESTE aparelho. */
const BIOMETRIC_KEY = 'sf.auth.biometric';

/**
 * Autenticação por email+senha (JWT) e biometria (WebAuthn / Face ID / Touch ID).
 * O token é persistido em localStorage; signals expõem o estado reativo.
 */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = inject(API_BASE_URL);

  private readonly _token = signal<string | null>(read(TOKEN_KEY));
  private readonly _expiresAt = signal<number>(Number(read(EXPIRES_KEY)) || 0);
  private readonly _email = signal<string | null>(read(EMAIL_KEY));

  /** Email do usuário logado (ou null). */
  readonly email = this._email.asReadonly();

  /** True quando há um token presente e ainda válido. */
  readonly isAuthenticated = computed(
    () => !!this._token() && this.tokenValid(),
  );

  /** Token JWT atual (string crua), ou null. */
  token(): string | null {
    return this._token();
  }

  /** O token existe e não expirou. */
  tokenValid(): boolean {
    const t = this._token();
    if (!t) return false;
    const exp = this._expiresAt();
    return exp > Date.now();
  }

  /** Login com email+senha. Em sucesso persiste o token. */
  async login(email: string, password: string): Promise<void> {
    const res = await firstValueFrom(
      this.http.post<LoginResponse>(`${this.baseUrl}/auth/login`, {
        email,
        password,
      }),
    );
    this.persist(res);
  }

  /** Limpa o token e o estado de autenticação (mantém a flag de biometria). */
  logout(): void {
    this._token.set(null);
    this._expiresAt.set(0);
    this._email.set(null);
    remove(TOKEN_KEY);
    remove(EXPIRES_KEY);
    remove(EMAIL_KEY);
  }

  // --- Biometria (WebAuthn) ---

  /** Biometria já registrada neste aparelho (flag local). */
  hasBiometricOnDevice(): boolean {
    return read(BIOMETRIC_KEY) === '1';
  }

  /**
   * Biometria disponível: o backend habilita (GET /auth/webauthn/available)
   * E o browser suporta WebAuthn. Tolerante a falhas de rede (retorna false).
   */
  async biometricAvailable(): Promise<boolean> {
    if (
      typeof window === 'undefined' ||
      typeof window.PublicKeyCredential === 'undefined'
    ) {
      return false;
    }
    try {
      const res = await firstValueFrom(
        this.http.get<{ available: boolean }>(
          `${this.baseUrl}/auth/webauthn/available`,
        ),
      );
      return !!res?.available;
    } catch {
      return false;
    }
  }

  /**
   * Registra a biometria deste aparelho. Requer estar logado (usa o Bearer
   * via interceptor). Em sucesso marca a flag local.
   */
  async registerBiometric(): Promise<void> {
    if (!this.isAuthenticated()) {
      throw new Error('É preciso estar logado para ativar a biometria.');
    }
    const options = await firstValueFrom(
      this.http.post<any>(
        `${this.baseUrl}/auth/webauthn/register/options`,
        {},
      ),
    );
    const credential = await startRegistration({ optionsJSON: options });
    await firstValueFrom(
      this.http.post<{ ok: boolean }>(
        `${this.baseUrl}/auth/webauthn/register/verify`,
        { credential },
      ),
    );
    write(BIOMETRIC_KEY, '1');
  }

  /**
   * Login por biometria (público). Pede as options, dispara o prompt nativo
   * (Face ID / Touch ID) e troca a assertion por um JWT.
   */
  async loginWithBiometric(): Promise<void> {
    const options = await firstValueFrom(
      this.http.post<any>(
        `${this.baseUrl}/auth/webauthn/login/options`,
        {},
      ),
    );
    if (options && options.available === false) {
      throw new Error('Biometria indisponível.');
    }
    const assertion = await startAuthentication({ optionsJSON: options });
    const res = await firstValueFrom(
      this.http.post<LoginResponse>(
        `${this.baseUrl}/auth/webauthn/login/verify`,
        { assertion },
      ),
    );
    this.persist(res);
    write(BIOMETRIC_KEY, '1');
  }

  // --- internos ---

  private persist(res: LoginResponse): void {
    const expiresAt = Date.now() + res.expires_in * 1000;
    this._token.set(res.token);
    this._expiresAt.set(expiresAt);
    this._email.set(res.email);
    write(TOKEN_KEY, res.token);
    write(EXPIRES_KEY, String(expiresAt));
    write(EMAIL_KEY, res.email);
  }
}

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage indisponível (modo privado/SSR) — silencioso */
  }
}
function remove(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    /* noop */
  }
}
