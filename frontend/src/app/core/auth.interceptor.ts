import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';
import { AuthService } from './auth.service';
import { ShareSessionService } from './share-session.service';

/** Rotas públicas (não recebem o Bearer): login por senha e login por biometria. */
function isPublicAuthRoute(url: string): boolean {
  return (
    url.includes('/auth/login') ||
    url.includes('/auth/webauthn/login/')
  );
}

/**
 * Functional interceptor: anexa `Authorization: Bearer <token>` nas requests à
 * API. No MODO CONVIDADO (link compartilhável, sem JWT) anexa o token de share
 * na query `?k=` — o backend o aceita só nas rotas daquela sessão. Em 401
 * desloga e manda pro /login (mas NÃO no modo convidado: lá não há login).
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const share = inject(ShareSessionService);
  const router = inject(Router);

  const token = auth.token();
  const shareToken = share.token();

  let authReq = req;
  if (token && !isPublicAuthRoute(req.url)) {
    authReq = req.clone({ setHeaders: { Authorization: `Bearer ${token}` } });
  } else if (shareToken && !isPublicAuthRoute(req.url)) {
    // Convidado: token de share vai na query (?k=), nunca no header.
    authReq = req.clone({ setParams: { k: shareToken } });
  }

  return next(authReq).pipe(
    catchError((err: unknown) => {
      if (err instanceof HttpErrorResponse && err.status === 401 && !shareToken) {
        auth.logout();
        router.navigate(['/login']);
      }
      return throwError(() => err);
    }),
  );
};
