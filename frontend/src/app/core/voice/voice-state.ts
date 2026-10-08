// frontend/src/app/core/voice/voice-state.ts
export type VoiceState =
  | 'idle' | 'connecting' | 'listening' | 'endpointing' | 'transcribing' | 'routing'
  | 'waiting_agent' | 'speaking' | 'cooldown'
  | 'disconnected' | 'cancelled' | 'error';

export interface VoiceServerMessage {
  type:
    | 'transcript.partial' | 'transcript.final'
    | 'assistant.text.delta'
    | 'assistant.speech.start' | 'assistant.speech.end'
    | 'error';
  turnId: string;
  text?: string;
  sampleRate?: number;
  format?: string;
  message?: string;
}
