import { Routes } from '@angular/router';
import { authGuard } from './core/auth.guard';

/**
 * `/login` é público; todo o resto é protegido pelo `authGuard`
 * (sem JWT válido → redireciona para `/login`).
 */
export const routes: Routes = [
  {
    path: 'login',
    loadComponent: () =>
      import('./features/login/login.component').then((m) => m.LoginComponent),
  },

  // --- Bottom-nav tabs (protegidos) ---
  {
    path: 'inicio',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/inicio/inicio.component').then((m) => m.InicioComponent),
  },
  {
    path: 'sessoes',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/sessoes/sessoes.component').then((m) => m.SessoesComponent),
  },
  {
    path: 'timeline',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/timeline/timeline.component').then((m) => m.TimelineComponent),
  },
  {
    path: 'responder',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/responder/responder.component').then((m) => m.ResponderComponent),
  },
  {
    path: 'perfil',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/perfil/perfil.component').then((m) => m.PerfilComponent),
  },

  // --- Overlays (protegidos) ---
  {
    path: 'criar',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/criar/criar.component').then((m) => m.CriarComponent),
  },
  {
    path: 'sessao/:id',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/detalhe/detalhe.component').then((m) => m.DetalheComponent),
  },
  {
    path: 'remota/:id',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/remota/remota.component').then((m) => m.RemotaComponent),
  },
  {
    path: 'notificacoes',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/notificacoes/notificacoes.component').then(
        (m) => m.NotificacoesComponent,
      ),
  },

  // --- Link compartilhável (PÚBLICO, sem guard): modo convidado, escopado a
  //     UMA sessão via ?k=<token>. Reusa a tela de detalhe travada. ---
  {
    path: 's/:id',
    data: { guest: true },
    loadComponent: () =>
      import('./features/detalhe/detalhe.component').then((m) => m.DetalheComponent),
  },

  // --- Defaults ---
  { path: '', redirectTo: 'inicio', pathMatch: 'full' },
  { path: '**', redirectTo: 'inicio' },
];
