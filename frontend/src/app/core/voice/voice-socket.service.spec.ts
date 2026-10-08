// frontend/src/app/core/voice/voice-socket.service.spec.ts
import { TestBed } from '@angular/core/testing';
import { VoiceSocketService } from './voice-socket.service';

describe('VoiceSocketService', () => {
  it('does not reuse a closed socket for sendPcmFrame', () => {
    const service = TestBed.inject(VoiceSocketService);
    service.disconnect(); // never connected
    // must not throw when the socket is null/closed
    expect(() => service.sendPcmFrame(new ArrayBuffer(4))).not.toThrow();
  });
});
