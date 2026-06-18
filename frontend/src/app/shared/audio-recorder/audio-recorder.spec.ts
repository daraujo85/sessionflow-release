import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { API_BASE_URL } from '../../core/api.service';
import { AudioRecorderService } from './audio-recorder.service';
import { AudioRecorderComponent } from './audio-recorder.component';

const BASE = 'http://localhost:8000';

/** Controllable MediaRecorder fake driven manually from the tests. */
class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = [];
  private listeners: Record<string, ((ev: unknown) => void)[]> = {};
  state: 'inactive' | 'recording' = 'inactive';

  constructor(public stream: MediaStream) {
    FakeMediaRecorder.instances.push(this);
  }

  addEventListener(type: string, cb: (ev: unknown) => void): void {
    (this.listeners[type] ??= []).push(cb);
  }

  private dispatch(type: string, ev: unknown): void {
    (this.listeners[type] ?? []).forEach((cb) => cb(ev));
  }

  start(): void {
    this.state = 'recording';
  }

  stop(): void {
    this.state = 'inactive';
    // Emit a chunk then signal completion, like the real API.
    this.dispatch('dataavailable', {
      data: new Blob(['chunk'], { type: 'audio/webm' }),
    });
    this.dispatch('stop', {});
  }
}

/** A fake MediaStream whose tracks record whether stop() was called. */
function fakeStream(): MediaStream {
  const track = { stop: vi.fn() };
  return { getTracks: () => [track] } as unknown as MediaStream;
}

describe('AudioRecorderService', () => {
  let service: AudioRecorderService;
  let getUserMedia: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    FakeMediaRecorder.instances = [];
    getUserMedia = vi.fn().mockResolvedValue(fakeStream());
    vi.stubGlobal('MediaRecorder', FakeMediaRecorder);
    vi.stubGlobal('navigator', {
      mediaDevices: { getUserMedia },
    });

    TestBed.configureTestingModule({});
    service = TestBed.inject(AudioRecorderService);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('start() sets recording=true and requests audio permission', async () => {
    await service.start();
    expect(getUserMedia).toHaveBeenCalledWith({ audio: true });
    expect(service.recording()).toBe(true);
    expect(service.error()).toBeNull();
  });

  it('stop() sets recording=false and resolves with an audio/webm Blob', async () => {
    await service.start();
    const blob = await service.stop();
    expect(service.recording()).toBe(false);
    expect(blob).toBeInstanceOf(Blob);
    expect(blob.type).toBe('audio/webm');
    expect(blob.size).toBeGreaterThan(0);
  });

  it('sets error signal when microphone permission is denied', async () => {
    const denied = new Error('denied');
    denied.name = 'NotAllowedError';
    getUserMedia.mockRejectedValueOnce(denied);

    await service.start();

    expect(service.recording()).toBe(false);
    expect(service.error()).toContain('Permissão');
  });

  it('stop() rejects when nothing is being recorded', async () => {
    await expect(service.stop()).rejects.toThrow();
  });
});

describe('AudioRecorderComponent', () => {
  let httpMock: HttpTestingController;
  let getUserMedia: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    FakeMediaRecorder.instances = [];
    getUserMedia = vi.fn().mockResolvedValue(fakeStream());
    vi.stubGlobal('MediaRecorder', FakeMediaRecorder);
    vi.stubGlobal('navigator', {
      mediaDevices: { getUserMedia },
    });

    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: API_BASE_URL, useValue: BASE },
      ],
    });
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('uploads audio via ApiService when stopping the recording', async () => {
    const fixture = TestBed.createComponent(AudioRecorderComponent);
    const comp = fixture.componentInstance;
    comp.sessionId = 'sess-1';
    const transcribing: boolean[] = [];
    comp.transcribing.subscribe((v) => transcribing.push(v));
    let uploaded = false;
    comp.uploaded.subscribe(() => (uploaded = true));
    fixture.detectChanges();

    // Start, then stop -> triggers upload.
    await comp.start();
    expect(comp['recorder'].recording()).toBe(true);

    // Don't await: finish() blocks on the HTTP call until we flush below.
    const stopping = comp.stop();
    // Let the recorder's stop promise + upload kick off.
    await new Promise((r) => setTimeout(r));

    const req = httpMock.expectOne(`${BASE}/sessions/sess-1/audio`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body instanceof FormData).toBe(true);
    req.flush(null);
    await stopping;

    expect(transcribing).toEqual([true, false]);
    expect(uploaded).toBe(true);
    httpMock.verify();
  });
});
