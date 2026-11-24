/* RMS observations only. No audio or business evidence is sent by this worklet. */
class RealtimeMeter extends AudioWorkletProcessor {
  constructor() { super(); this.energy = 0; this.samples = 0; }
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (channel) {
      for (const value of channel) this.energy += value * value;
      this.samples += channel.length;
      if (this.samples >= sampleRate * 0.02) {
        this.port.postMessage({rms: Math.sqrt(this.energy / this.samples)});
        this.energy = 0; this.samples = 0;
      }
    }
    return true;
  }
}
registerProcessor('realtime-meter', RealtimeMeter);
