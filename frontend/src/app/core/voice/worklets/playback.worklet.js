// frontend/src/app/core/voice/worklets/playback.worklet.js
class PlaybackProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._queue = [];
    this._offset = 0;
    this.port.onmessage = (ev) => {
      if (ev.data === 'clear') {
        this._queue = [];
        this._offset = 0;
        return;
      }
      this._queue.push(new Int16Array(ev.data));
    };
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    for (let i = 0; i < out.length; i++) {
      if (this._queue.length === 0) {
        out[i] = 0;
        continue;
      }
      const current = this._queue[0];
      out[i] = current[this._offset] / 0x8000;
      this._offset++;
      if (this._offset >= current.length) {
        this._queue.shift();
        this._offset = 0;
      }
    }
    return true;
  }
}
registerProcessor('playback-processor', PlaybackProcessor);
