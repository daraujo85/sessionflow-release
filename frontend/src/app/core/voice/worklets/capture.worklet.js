// frontend/src/app/core/voice/worklets/capture.worklet.js
class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._frameSamples = 320; // 20ms @ 16kHz
    this._buffer = new Float32Array(0);
    this._inRate = sampleRate; // AudioWorkletGlobalScope, actual device rate
  }

  _resampleTo16k(input) {
    if (this._inRate === 16000) return input;
    const ratio = this._inRate / 16000;
    const outLength = Math.floor(input.length / ratio);
    const out = new Float32Array(outLength);
    for (let i = 0; i < outLength; i++) {
      out[i] = input[Math.floor(i * ratio)];
    }
    return out;
  }

  process(inputs) {
    const channel = inputs[0][0];
    if (!channel) return true;
    const resampled = this._resampleTo16k(channel);
    const merged = new Float32Array(this._buffer.length + resampled.length);
    merged.set(this._buffer);
    merged.set(resampled, this._buffer.length);
    this._buffer = merged;

    while (this._buffer.length >= this._frameSamples) {
      const frame = this._buffer.subarray(0, this._frameSamples);
      const pcm16 = new Int16Array(this._frameSamples);
      for (let i = 0; i < this._frameSamples; i++) {
        const s = Math.max(-1, Math.min(1, frame[i]));
        pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      this.port.postMessage(pcm16.buffer, [pcm16.buffer]);
      this._buffer = this._buffer.subarray(this._frameSamples);
    }
    return true;
  }
}
registerProcessor('capture-processor', CaptureProcessor);
