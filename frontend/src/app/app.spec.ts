import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { App } from './app';
import { routes } from './app.routes';
import { AuthService } from './core/auth.service';

/** AuthService falso: sempre autenticado (libera o authGuard nos testes de rota). */
const fakeAuth = {
  isAuthenticated: () => true,
  tokenValid: () => true,
  token: () => null,
  email: () => 'test@test',
  biometricEnabled: () => false,
  logout: () => {},
};

describe('App shell', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [provideRouter(routes), { provide: AuthService, useValue: fakeAuth }],
    }).compileComponents();
  });

  it('should create the app', () => {
    const fixture = TestBed.createComponent(App);
    expect(fixture.componentInstance).toBeTruthy();
  });

  it('should render a bottom-nav with 5 items', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    fixture.detectChanges();
    const items = fixture.nativeElement.querySelectorAll('.sf-nav .sf-nav-item');
    expect(items.length).toBe(5);
    const labels = Array.from(items).map((el) => (el as HTMLElement).textContent?.trim());
    expect(labels).toEqual(['Início', 'Sessões', 'Timeline', 'Responder', 'Perfil']);
  });

  it('should default to the Início screen', async () => {
    const harness = await RouterTestingHarness.create('/inicio');
    // Tela real de Início traz a saudação "Boa noite, Diego".
    expect(harness.routeNativeElement?.textContent).toContain('Diego');
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
});
