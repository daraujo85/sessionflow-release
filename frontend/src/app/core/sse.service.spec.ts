import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { API_BASE_URL } from './api.service';
import { SseService } from './sse.service';

/**
 * Controllable fake EventSource. jsdom does not ship a usable EventSource, so
 * we install this on the global object and drive its lifecycle manually from
 * the tests (open / message / error).
 */
class FakeEventSource {
  static instances: FakeEventSource[] = [];

  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  readonly url: string;
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  close(): void {
    this.closed = true;
  }

  // --- test driving helpers ---
  emitOpen(): void {
    this.onopen?.(new Event('open'));
  }

  emitMessage(data: unknown): void {
    const payload = typeof data === 'string' ? data : JSON.stringify(data);
    this.onmessage?.(new MessageEvent('message', { data: payload }));
  }

  emitError(): void {
    this.onerror?.(new Event('error'));
  }

  static get last(): FakeEventSource {
    return FakeEventSource.instances[FakeEventSource.instances.length - 1];
  }

  static reset(): void {
    FakeEventSource.instances = [];
  }
}

describe('SseService', () => {
  let service: SseService;
  const originalEventSource = (globalThis as Record<string, unknown>)[
    'EventSource'
  ];

  beforeEach(() => {
    FakeEventSource.reset();
    (globalThis as Record<string, unknown>)['EventSource'] = FakeEventSource;
    TestBed.configureTestingModule({
      providers: [
        SseService,
        { provide: API_BASE_URL, useValue: 'http://test.local' },
      ],
    });
    service = TestBed.inject(SseService);
  });

  afterEach(() => {
    service.disconnect();
    vi.useRealTimers();
    (globalThis as Record<string, unknown>)['EventSource'] =
      originalEventSource;
  });

  it('connects to {apiBase}/events with the session query param', () => {
    service.connect('sess-1');
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.last.url).toBe(
      'http://test.local/events?session=sess-1',
    );
    FakeEventSource.last.emitOpen();
    expect(service.connected()).toBe(true);
  });

  it('parses a structured event frame and updates signals', () => {
    service.connect();
    const event = {
      id: 'e1',
      session_id: 's1',
      type: 'notification',
      kind: 'info',
      title: 'Hello',
      desc: 'world',
      at: '2026-06-16T00:00:00Z',
    };
    FakeEventSource.last.emitMessage(event);

    expect(service.lastEvent()).toEqual(event);
    expect(service.events()).toHaveLength(1);
    expect(service.events()[0].title).toBe('Hello');
    // type === 'notification' routes it into the notifications buffer too.
    expect(service.notifications()).toHaveLength(1);
    expect(service.outputLines()).toHaveLength(0);
  });

  it('parses an output-line frame into outputLines', () => {
    service.connect();
    const line = { session_id: 's1', seq: 7, text: 'compiling', line_type: 'stdout' };
    FakeEventSource.last.emitMessage(line);

    expect(service.outputLines()).toHaveLength(1);
    expect(service.outputLines()[0]).toEqual(line);
    expect(service.events()).toHaveLength(0);
  });

  it('ignores invalid JSON and heartbeat frames', () => {
    service.connect();
    FakeEventSource.last.emitMessage('not-json{');
    FakeEventSource.last.emitMessage(': ping');
    FakeEventSource.last.emitMessage('');

    expect(service.lastEvent()).toBeNull();
    expect(service.events()).toHaveLength(0);
    expect(service.outputLines()).toHaveLength(0);
  });

  it('schedules a reconnect with exponential backoff on error', () => {
    vi.useFakeTimers();
    service.connect();
    expect(FakeEventSource.instances).toHaveLength(1);

    // First error -> reconnect after 1s.
    FakeEventSource.last.emitError();
    expect(service.connected()).toBe(false);
    vi.advanceTimersByTime(999);
    expect(FakeEventSource.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeEventSource.instances).toHaveLength(2);

    // Second error -> backoff doubled to 2s.
    FakeEventSource.last.emitError();
    vi.advanceTimersByTime(1999);
    expect(FakeEventSource.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(FakeEventSource.instances).toHaveLength(3);
  });

  it('stops reconnecting after disconnect()', () => {
    vi.useFakeTimers();
    service.connect();
    FakeEventSource.last.emitError();
    service.disconnect();
    vi.advanceTimersByTime(60_000);
    // No new EventSource created after disconnect.
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(service.connected()).toBe(false);
  });

  it('resets backoff after a successful reconnect', () => {
    vi.useFakeTimers();
    service.connect();

    FakeEventSource.last.emitError();
    vi.advanceTimersByTime(1000); // reconnect #2
    FakeEventSource.last.emitOpen(); // healthy -> backoff resets to 1s

    FakeEventSource.last.emitError();
    vi.advanceTimersByTime(1000); // should reconnect at 1s again, not 2s
    expect(FakeEventSource.instances).toHaveLength(3);
  });
});
