/* Mono PCM16, 24 kHz, delivered in 100 ms frames; never played back. */
class MicrophonePCM extends AudioWorkletProcessor {
  constructor() {
    super();
    this.samples = new Int16Array(2400);
    this.offset = 0;
    this.port.onmessage = event => {
      if (event.data === 'flush') {
        if (this.offset) this.port.postMessage({pcm:this.samples.slice(0,this.offset).buffer});
        this.offset = 0;
        this.port.postMessage({flushed:true});
      }
    };
  }
  process(inputs) {
    const input = inputs[0]?.[0];
    if (input) for (const value of input) {
      const sample = Math.max(-1, Math.min(1, value));
      this.samples[this.offset++] = sample < 0 ? sample * 32768 : sample * 32767;
      if (this.offset === this.samples.length) {
        const pcm = this.samples.slice().buffer;
        this.port.postMessage({pcm}, [pcm]);
        this.offset = 0;
      }
    }
    return true;
  }
}
registerProcessor('microphone-pcm', MicrophonePCM);
