export type CaptureMode = "microphone" | "system" | "mixed";

type FloorCallback = (busy: boolean) => void;
type ChunkCallback = (chunk: ArrayBuffer) => void;
type LevelCallback = (level: number) => void;

export class AudioBridge {
  private context?: AudioContext;
  private streams: MediaStream[] = [];
  private processor?: ScriptProcessorNode;
  private playback?: AudioBufferSourceNode;
  private cue?: { source: AudioBufferSourceNode; gain: GainNode };
  private playbackActive = false;
  private voicedFrames = 0;
  private floorBusy = false;
  private lastEnergyAt = 0;
  private pcmPending = new Int16Array(0);

  constructor(
    private readonly onChunk: ChunkCallback,
    private readonly onFloor: FloorCallback,
    private readonly onLevel: LevelCallback,
    private readonly onBargeIn: () => void,
  ) {}

  async start(mode: CaptureMode): Promise<void> {
    await this.stopCapture();
    this.context = new AudioContext();
    if (mode === "microphone" || mode === "mixed") {
      this.streams.push(await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      }));
    }
    if (mode === "system" || mode === "mixed") {
      const display = await navigator.mediaDevices.getDisplayMedia({ audio: true, video: true });
      for (const track of display.getVideoTracks()) track.stop();
      if (display.getAudioTracks().length === 0) {
        display.getTracks().forEach((track) => track.stop());
        throw new Error("The selected surface did not provide system audio.");
      }
      this.streams.push(display);
    }
    const mix = this.context.createGain();
    for (const stream of this.streams) this.context.createMediaStreamSource(stream).connect(mix);
    this.processor = this.context.createScriptProcessor(4096, 1, 1);
    const silent = this.context.createGain();
    silent.gain.value = 0;
    mix.connect(this.processor);
    this.processor.connect(silent);
    silent.connect(this.context.destination);
    this.processor.onaudioprocess = (event) => this.process(event.inputBuffer.getChannelData(0));
    await this.context.resume();
  }

  async stopCapture(): Promise<void> {
    this.processor?.disconnect();
    this.processor = undefined;
    for (const stream of this.streams) stream.getTracks().forEach((track) => track.stop());
    this.streams = [];
    if (this.context) await this.context.close();
    this.context = undefined;
    this.pcmPending = new Int16Array(0);
    this.setFloor(false);
    this.onLevel(0);
  }

  async playAudio(base64: string, format: string, sampleRate: number): Promise<void> {
    this.stopPlayback();
    const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
    const context = this.context && this.context.state !== "closed" ? this.context : new AudioContext();
    if (!this.context) this.context = context;
    let buffer: AudioBuffer;
    if (format.startsWith("pcm")) {
      const samples = new Int16Array(bytes.buffer);
      buffer = context.createBuffer(1, samples.length, sampleRate);
      const channel = buffer.getChannelData(0);
      for (let index = 0; index < samples.length; index += 1) channel[index] = samples[index] / 32768;
    } else {
      buffer = await context.decodeAudioData(bytes.buffer.slice(0) as ArrayBuffer);
    }
    this.playback = context.createBufferSource();
    this.playback.buffer = buffer;
    this.playback.connect(context.destination);
    await new Promise<void>((resolve) => {
      if (!this.playback) return resolve();
      this.playbackActive = true;
      this.playback.onended = () => {
        this.playbackActive = false;
        resolve();
      };
      this.playback.start();
    });
    this.playback = undefined;
  }

  speakBrowser(text: string, language: string): Promise<void> {
    this.stopPlayback();
    return new Promise((resolve) => {
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = language;
      this.playbackActive = true;
      utterance.onend = () => {
        this.playbackActive = false;
        resolve();
      };
      utterance.onerror = () => {
        this.playbackActive = false;
        resolve();
      };
      speechSynthesis.speak(utterance);
    });
  }

  async playCue(audio?: { data_base64?: string; format?: string; sample_rate?: number }): Promise<void> {
    if (this.playbackActive || this.floorBusy || this.cue || !audio?.data_base64) return;
    const context = this.context && this.context.state !== "closed" ? this.context : new AudioContext();
    if (!this.context) this.context = context;
    await context.resume();
    const bytes = Uint8Array.from(atob(audio.data_base64), (character) => character.charCodeAt(0));
    let buffer: AudioBuffer;
    if ((audio.format ?? "").startsWith("pcm")) {
      const samples = new Int16Array(bytes.buffer);
      buffer = context.createBuffer(1, samples.length, audio.sample_rate ?? 24000);
      const channel = buffer.getChannelData(0);
      for (let index = 0; index < samples.length; index += 1) channel[index] = samples[index] / 32768;
    } else {
      buffer = await context.decodeAudioData(bytes.buffer.slice(0) as ArrayBuffer);
    }
    const gain = context.createGain();
    const source = context.createBufferSource();
    const now = context.currentTime;
    source.buffer = buffer;
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(0.42, now + 0.08);
    gain.gain.exponentialRampToValueAtTime(0.08, now + Math.max(0.2, buffer.duration - 0.08));
    source.connect(gain);
    gain.connect(context.destination);
    source.start(now);
    source.onended = () => {
      if (this.cue?.source === source) this.cue = undefined;
    };
    this.cue = { source, gain };
  }

  stopCue(): void {
    if (!this.cue || !this.context) return;
    const cue = this.cue;
    this.cue = undefined;
    const now = this.context.currentTime;
    cue.gain.gain.cancelScheduledValues(now);
    cue.gain.gain.setValueAtTime(Math.max(cue.gain.gain.value, 0.0001), now);
    cue.gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.16);
    cue.source.stop(now + 0.18);
  }

  stopPlayback(): void {
    this.stopCue();
    this.playbackActive = false;
    if (this.playback) {
      try {
        this.playback.stop();
      } catch {
        // The source may already have ended.
      }
      this.playback = undefined;
    }
    speechSynthesis.cancel();
  }

  private process(input: Float32Array): void {
    if (!this.context) return;
    let energy = 0;
    for (const sample of input) energy += sample * sample;
    const rms = Math.sqrt(energy / input.length);
    this.onLevel(Math.min(1, rms / 0.12));
    const now = performance.now();
    const threshold = this.playbackActive ? 0.045 : 0.018;
    const requiredFrames = this.playbackActive ? 3 : 2;
    if (rms > threshold) {
      this.voicedFrames += 1;
      this.lastEnergyAt = now;
      if (this.voicedFrames >= requiredFrames && !this.floorBusy) {
        const interruptedPlayback = this.playbackActive;
        this.setFloor(true);
        if (interruptedPlayback) {
          this.stopPlayback();
          this.onBargeIn();
        }
      }
    } else if (this.floorBusy && now - this.lastEnergyAt > 650) {
      this.voicedFrames = 0;
      this.setFloor(false);
    } else {
      this.voicedFrames = 0;
    }
    this.queuePcm(downsamplePcm16(input, this.context.sampleRate, 24000));
  }

  private queuePcm(chunk: Int16Array): void {
    const combined = new Int16Array(this.pcmPending.length + chunk.length);
    combined.set(this.pcmPending);
    combined.set(chunk, this.pcmPending.length);
    let offset = 0;
    while (combined.length - offset >= 1920) {
      const frame = combined.slice(offset, offset + 1920);
      this.onChunk(frame.buffer as ArrayBuffer);
      offset += 1920;
    }
    this.pcmPending = combined.slice(offset);
  }

  private setFloor(busy: boolean): void {
    if (busy === this.floorBusy) return;
    this.floorBusy = busy;
    this.onFloor(busy);
  }
}

function downsamplePcm16(input: Float32Array, sourceRate: number, targetRate: number): Int16Array {
  const ratio = sourceRate / targetRate;
  const length = Math.floor(input.length / ratio);
  const output = new Int16Array(length);
  for (let index = 0; index < length; index += 1) {
    const start = Math.floor(index * ratio);
    const end = Math.max(start + 1, Math.floor((index + 1) * ratio));
    let sum = 0;
    for (let offset = start; offset < end && offset < input.length; offset += 1) sum += input[offset];
    const sample = Math.max(-1, Math.min(1, sum / (end - start)));
    output[index] = sample < 0 ? sample * 32768 : sample * 32767;
  }
  return output;
}
