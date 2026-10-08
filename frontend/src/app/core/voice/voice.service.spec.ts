// frontend/src/app/core/voice/voice.service.spec.ts
import { vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { Subject } from 'rxjs';
import { VoiceService } from './voice.service';
import { MicrophoneService } from './microphone.service';
import { VoiceSocketService } from './voice-socket.service';
import { AudioPlaybackService } from './audio-playback.service';
import { EventCuesService } from '../event-cues.service';
import { JarvisAudioService } from '../jarvis-audio.service';

describe('VoiceService', () => {
  beforeEach(() => {
    vi.stubGlobal('AudioContext', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  describe('half-duplex', () => {
    beforeEach(() => {
      vi.useFakeTimers();
      TestBed.configureTestingModule({
        providers: [
          { provide: MicrophoneService, useValue: { setMuted: vi.fn(), stop: vi.fn() } },
          { provide: VoiceSocketService, useValue: { binary$: new Subject(), messages$: new Subject(), closed$: new Subject() } },
          { provide: AudioPlaybackService, useValue: { enqueue: vi.fn(), close: vi.fn() } },
          { provide: EventCuesService, useValue: { unsuppress: vi.fn() } },
          { provide: JarvisAudioService, useValue: { setRecording: vi.fn() } },
        ],
      });
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it('mutes mic during SPEAKING and unmutes after cooldown', () => {
      const service = TestBed.inject(VoiceService);
      const mic = TestBed.inject(MicrophoneService);
      const muteSpy = vi.spyOn(mic, 'setMuted');

      (service as any).onSpeechStart();
      expect(muteSpy).toHaveBeenCalledWith(true);

      (service as any).onSpeechEnd();
      vi.advanceTimersByTime(349);
      expect(muteSpy).not.toHaveBeenCalledWith(false);
      vi.advanceTimersByTime(1);
      expect(muteSpy).toHaveBeenCalledWith(false);
    });
  });

  describe('audio playback (Important #6)', () => {
    beforeEach(() => {
      TestBed.configureTestingModule({
        providers: [
          { provide: MicrophoneService, useValue: { setMuted: vi.fn(), stop: vi.fn() } },
          { provide: VoiceSocketService, useValue: { binary$: new Subject(), messages$: new Subject(), closed$: new Subject() } },
          { provide: AudioPlaybackService, useValue: { enqueue: vi.fn(), close: vi.fn() } },
          { provide: EventCuesService, useValue: { unsuppress: vi.fn() } },
          { provide: JarvisAudioService, useValue: { setRecording: vi.fn() } },
        ],
      });
    });

    it('forwards binary$ chunks to playback.enqueue at the default sample rate', () => {
      const service = TestBed.inject(VoiceService);
      const socket = TestBed.inject(VoiceSocketService);
      const playback = TestBed.inject(AudioPlaybackService);
      const enqueueSpy = vi.spyOn(playback, 'enqueue');

      const buf = new Int16Array([1, 2, 3]).buffer;
      socket.binary$.next(buf);

      expect(enqueueSpy).toHaveBeenCalledTimes(1);
      const [pcm, sampleRate] = enqueueSpy.mock.calls[0];
      expect(Array.from(pcm as Int16Array)).toEqual([1, 2, 3]);
      expect(sampleRate).toBe(22050);
    });

    it('uses the sampleRate carried by the most recent assistant.speech.start', () => {
      const service = TestBed.inject(VoiceService);
      const socket = TestBed.inject(VoiceSocketService);
      const playback = TestBed.inject(AudioPlaybackService);
      const enqueueSpy = vi.spyOn(playback, 'enqueue');

      socket.messages$.next({ type: 'assistant.speech.start', turnId: 't1', sampleRate: 16000 });
      socket.binary$.next(new Int16Array([9]).buffer);

      expect(enqueueSpy).toHaveBeenCalledWith(expect.any(Int16Array), 16000);
    });
  });
});

describe('VoiceService disconnect (spec §21)', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        { provide: MicrophoneService, useValue: { setMuted: vi.fn(), stop: vi.fn() } },
        { provide: VoiceSocketService, useValue: { binary$: new Subject(), messages$: new Subject(), closed$: new Subject() } },
        { provide: AudioPlaybackService, useValue: { enqueue: vi.fn(), close: vi.fn() } },
        { provide: EventCuesService, useValue: { unsuppress: vi.fn() } },
        { provide: JarvisAudioService, useValue: { setRecording: vi.fn() } },
      ],
    });
  });

  it('emitting closed$ calls mic.stop(), playback.close(), and clears turn state', () => {
    const service = TestBed.inject(VoiceService);
    const socket = TestBed.inject(VoiceSocketService);
    const mic = TestBed.inject(MicrophoneService);
    const playback = TestBed.inject(AudioPlaybackService);
    const stopSpy = vi.spyOn(mic, 'stop');
    const closeSpy = vi.spyOn(playback, 'close');

    (service as any).turnId = 'turn-1';
    service.transcript.set('algum texto');
    service.reply.set('alguma resposta');
    service.state.set('speaking');

    socket.closed$.next();

    expect(stopSpy).toHaveBeenCalled();
    expect(closeSpy).toHaveBeenCalled();
    expect(service.state()).toBe('disconnected');
    expect((service as any).turnId).toBeNull();
    expect(service.transcript()).toBe('');
    expect(service.reply()).toBe('');
  });
});

describe('VoiceService suppresses notification cues during a call', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        { provide: MicrophoneService, useValue: { setMuted: vi.fn(), stop: vi.fn() } },
        { provide: VoiceSocketService, useValue: { binary$: new Subject(), messages$: new Subject(), closed$: new Subject() } },
        { provide: AudioPlaybackService, useValue: { enqueue: vi.fn(), close: vi.fn() } },
        { provide: EventCuesService, useValue: { unsuppress: vi.fn() } },
        { provide: JarvisAudioService, useValue: { setRecording: vi.fn() } },
      ],
    });
  });

  it('unsuppresses EventCuesService when the socket closes', () => {
    TestBed.inject(VoiceService);
    const socket = TestBed.inject(VoiceSocketService);
    const cues = TestBed.inject(EventCuesService);
    const unsuppressSpy = vi.spyOn(cues, 'unsuppress');

    socket.closed$.next();

    expect(unsuppressSpy).toHaveBeenCalled();
  });
});

describe('VoiceService suppresses JARVIS spoken summaries during a call', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        { provide: MicrophoneService, useValue: { setMuted: vi.fn(), stop: vi.fn() } },
        { provide: VoiceSocketService, useValue: { binary$: new Subject(), messages$: new Subject(), closed$: new Subject() } },
        { provide: AudioPlaybackService, useValue: { enqueue: vi.fn(), close: vi.fn() } },
        { provide: EventCuesService, useValue: { unsuppress: vi.fn() } },
        { provide: JarvisAudioService, useValue: { setRecording: vi.fn() } },
      ],
    });
  });

  it('holds JarvisAudioService playback when the socket closes', () => {
    TestBed.inject(VoiceService);
    const socket = TestBed.inject(VoiceSocketService);
    const jarvisAudio = TestBed.inject(JarvisAudioService);
    const setRecordingSpy = vi.spyOn(jarvisAudio, 'setRecording');

    socket.closed$.next();

    expect(setRecordingSpy).toHaveBeenCalledWith(false, 'voice-call');
  });
});
