/* Pegel des Mikrofons je 100 ms, fuer die Live-Vorschau in Abschnitten (app.js,
   PauseCutter). Laeuft im Audio-Thread: anders als setInterval wird er nicht
   gedrosselt, wenn der Browser hinter T2med liegt. */
class LevelMeter extends AudioWorkletProcessor {
  constructor() {
    super();
    this.size = Math.round(sampleRate / 10);
    this.sum = 0;
    this.count = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) {
      for (let i = 0; i < channel.length; i++) {
        this.sum += channel[i] * channel[i];
        if (++this.count >= this.size) {
          this.port.postMessage(Math.sqrt(this.sum / this.count));
          this.sum = 0;
          this.count = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("level-meter", LevelMeter);
