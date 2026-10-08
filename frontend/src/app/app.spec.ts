import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { App } from './app';
import { routes } from './app.routes';
import { AuthService } from './core/auth.service';
import { WorkersStore } from './core/workers-store';
import { WorkerStatus } from './core/models';

/** AuthService falso: sempre autenticado (libera o authGuard nos testes de rota). */
const fakeAuth = {
  isAuthenticated: () => true,
  tokenValid: () => true,
  token: () => null,
  email: () => 'test@test',
  biometricEnabled: () => false,
  logout: () => {},
};

/** Dois hosts: MacBook com modelo carregado, DuckServer sem nenhum ativo. */
const twoHostWorkers: WorkerStatus[] = [
  {
    online: true,
    hostname: 'macbook.local',
    display_name: 'MacBook',
    emoji: '💻',
    host_id: 'mac',
    uptime_seconds: 0,
    started_at: null,
    updated_at: null,
    ollama_models: [
      { name: 'llava', size_gb: 4, modified_at: null, loaded: true },
    ],
  },
  {
    online: true,
    hostname: 'duckserver',
    display_name: 'DuckServer',
    emoji: '🦆',
    host_id: 'duck',
    uptime_seconds: 0,
    started_at: null,
    updated_at: null,
    ollama_models: [
      { name: 'qwen3', size_gb: 8, modified_at: null, loaded: false },
    ],
  },
];

describe('App shell', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideRouter(routes),
        provideNoopAnimations(),
        { provide: AuthService, useValue: { ...fakeAuth, nome: () => 'Test' } },
      ],
    }).compileComponents();
  });

  it('should create the app', () => {
    const fixture = TestBed.createComponent(App);
    expect(fixture.componentInstance).toBeTruthy();
  });

  it('should render a bottom-nav with 4 items', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    fixture.detectChanges();
    const items = fixture.nativeElement.querySelectorAll('.sf-nav .sf-nav-item');
    expect(items.length).toBe(4);
    const labels = Array.from(items).map((el) => (el as HTMLElement).textContent?.trim());
    expect(labels).toEqual(['Início', 'Sessões', 'Timeline', 'Perfil']);
  });

  it('should default to the Início screen', async () => {
    const harness = await RouterTestingHarness.create('/inicio');
    // Tela real de Início traz a saudação com o nome do usuário logado.
    expect(harness.routeNativeElement?.textContent).toContain('Test');
  });

  it('should navigate between tabs swapping the stub content', async () => {
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/timeline');
    expect(harness.routeNativeElement?.textContent).toContain('Timeline');

    await harness.navigateByUrl('/perfil');
    expect(harness.routeNativeElement?.textContent).toContain('Perfil');
  });

  it('should open the criar overlay via route', async () => {
    const harness = await RouterTestingHarness.create('/criar');
    // Tela real de criação tem o título "Nova sessão".
    expect(harness.routeNativeElement?.textContent).toContain('Nova sessão');
  });

  it('should show the active Ollama model per host in the nav footer', async () => {
    const fakeWorkersStore = { workers: signal(twoHostWorkers) };
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideRouter(routes),
        provideNoopAnimations(),
        { provide: AuthService, useValue: fakeAuth },
        { provide: WorkersStore, useValue: fakeWorkersStore },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    fixture.detectChanges();
    const rows = fixture.nativeElement.querySelectorAll('.sf-nav-ollama-row');
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain('llava');
    expect(rows[1].textContent).toContain('ocioso');
  });

  it('should hide the Ollama footer when no host has models installed', async () => {
    const fakeWorkersStore = { workers: signal<WorkerStatus[]>([]) };
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideRouter(routes),
        provideNoopAnimations(),
        { provide: AuthService, useValue: fakeAuth },
        { provide: WorkersStore, useValue: fakeWorkersStore },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.sf-nav-ollama')).toBeNull();
  });
});
