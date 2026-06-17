import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { ApiService, API_BASE_URL } from './api.service';
import { CreateSessionPayload, Session } from './models';

const BASE = 'http://localhost:8000';

describe('ApiService', () => {
  let service: ApiService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        ApiService,
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: API_BASE_URL, useValue: BASE },
      ],
    });
    service = TestBed.inject(ApiService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('listSessions sends GET with status param', () => {
    service.listSessions('running').subscribe();
    const req = httpMock.expectOne(`${BASE}/sessions?status=running`);
    expect(req.request.method).toBe('GET');
    req.flush([]);
  });

  it('getSession sends GET to /sessions/{id}', () => {
    service.getSession('abc').subscribe();
    const req = httpMock.expectOne(`${BASE}/sessions/abc`);
    expect(req.request.method).toBe('GET');
    req.flush({} as Session);
  });

  it('createSession sends POST with body', () => {
    const payload: CreateSessionPayload = {
      name: 'My Session',
      agent_type: 'claude',
      work_dir: '/tmp',
      model: 'opus',
      effort: 'high',
    };
    service.createSession(payload).subscribe();
    const req = httpMock.expectOne(`${BASE}/sessions`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(payload);
    req.flush({} as Session);
  });

  it('deleteSession sends DELETE to /sessions/{id}', () => {
    service.deleteSession('xyz').subscribe();
    const req = httpMock.expectOne(`${BASE}/sessions/xyz`);
    expect(req.request.method).toBe('DELETE');
    req.flush(null);
  });

  it('renameSession sends PATCH with display_name', () => {
    service.renameSession('id1', 'New Name').subscribe();
    const req = httpMock.expectOne(`${BASE}/sessions/id1`);
    expect(req.request.method).toBe('PATCH');
    expect(req.request.body).toEqual({ display_name: 'New Name' });
    req.flush({} as Session);
  });

  it('resumeSession sends POST to /sessions/{id}/resume', () => {
    service.resumeSession('id2').subscribe();
    const req = httpMock.expectOne(`${BASE}/sessions/id2/resume`);
    expect(req.request.method).toBe('POST');
    req.flush({} as Session);
  });

  it('sendInput sends POST with text body', () => {
    service.sendInput('id3', 'hello').subscribe();
    const req = httpMock.expectOne(`${BASE}/sessions/id3/input`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ text: 'hello', enter: true });
    req.flush(null);
  });

  it('uploadAudio sends POST with FormData', () => {
    const blob = new Blob(['audio'], { type: 'audio/wav' });
    service.uploadAudio('id4', blob).subscribe();
    const req = httpMock.expectOne(`${BASE}/sessions/id4/audio`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body instanceof FormData).toBe(true);
    req.flush(null);
  });

  it('getOutput sends GET with after param', () => {
    service.getOutput('id5', 42).subscribe();
    const req = httpMock.expectOne(`${BASE}/sessions/id5/output?after=42`);
    expect(req.request.method).toBe('GET');
    req.flush([]);
  });

  it('getScreen sends GET to /sessions/{id}/screen and returns body', () => {
    let result: unknown;
    service.getScreen('id6').subscribe((r) => (result = r));
    const req = httpMock.expectOne(`${BASE}/sessions/id6/screen`);
    expect(req.request.method).toBe('GET');
    const body = { text: 'hello\nworld', at: '2026-06-17T00:00:00Z' };
    req.flush(body);
    expect(result).toEqual(body);
  });

  it('getHistory sends GET with day param', () => {
    service.getHistory('2026-06-16').subscribe();
    const req = httpMock.expectOne(`${BASE}/events/history?day=2026-06-16`);
    expect(req.request.method).toBe('GET');
    req.flush([]);
  });

  it('getNotifications sends GET to /notifications', () => {
    service.getNotifications().subscribe();
    const req = httpMock.expectOne(`${BASE}/notifications`);
    expect(req.request.method).toBe('GET');
    req.flush([]);
  });

  it('getTasks sends GET with session param', () => {
    service.getTasks('sess1').subscribe();
    const req = httpMock.expectOne(`${BASE}/tasks?session=sess1`);
    expect(req.request.method).toBe('GET');
    req.flush([]);
  });

  it('getModels sends GET with agent param and returns first item', () => {
    let result: unknown;
    service.getModels('claude').subscribe((r) => (result = r));
    const req = httpMock.expectOne(`${BASE}/models?agent=claude`);
    expect(req.request.method).toBe('GET');
    const item = {
      agent: 'claude',
      source: 'config',
      models: [
        { id: 'default', label: 'Default', is_default: true },
        { id: 'opus', label: 'Opus' },
      ],
    };
    req.flush({ items: [item] });
    expect(result).toEqual(item);
  });

  it('getModels returns null when items is empty', () => {
    let result: unknown = 'unset';
    service.getModels('gemini').subscribe((r) => (result = r));
    const req = httpMock.expectOne(`${BASE}/models?agent=gemini`);
    req.flush({ items: [] });
    expect(result).toBeNull();
  });

  it('searchDirectories sends GET with q param', () => {
    service.searchDirectories('proj').subscribe();
    const req = httpMock.expectOne(`${BASE}/directories?q=proj`);
    expect(req.request.method).toBe('GET');
    req.flush([]);
  });
});
