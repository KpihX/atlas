export type CaptureMode = "microphone" | "system" | "mixed";
export type CaptureResult = { microphone: boolean; system: boolean };

type FloorCallback = (busy: boolean) => void;
type ChunkCallback = (chunk: ArrayBuffer) => void;
type LevelCallback = (level: number) => void;

export class AudioBridge {
  private context?: AudioContext;
  private streams: MediaStream[] = [];
  private processor?: ScriptProcessorNode;
  private floorProcessor?: ScriptProcessorNode;
  private playback?: AudioBufferSourceNode;
  private streamSources = new Set<AudioBufferSourceNode>();
  private streamNextTime = 0;
  private streamEnded = false;
  private streamPromise?: Promise<void>;
  private streamResolve?: () => void;
  private cue?: {
    node: AudioBufferSourceNode;
    masterGain: GainNode;
  };
  private cueRequest = 0;
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

  async start(mode: CaptureMode): Promise<CaptureResult> {
    await this.stopCapture();
    this.context = new AudioContext();
    let microphone = false;
    let system = false;
    let microphoneStream: MediaStream | undefined;
    let systemStream: MediaStream | undefined;
    if (mode === "microphone" || mode === "mixed") {
      microphoneStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      this.streams.push(microphoneStream);
      microphone = true;
    }
    if (mode === "system" || mode === "mixed") {
      try {
        const display = await navigator.mediaDevices.getDisplayMedia({
          video: true,
          audio: {
            suppressLocalAudioPlayback: false,
          },
          systemAudio: "include",
        } as DisplayMediaStreamOptions);
        for (const track of display.getVideoTracks()) track.stop();
        if (display.getAudioTracks().length === 0) {
          display.getTracks().forEach((track) => track.stop());
          if (mode === "system") {
            throw new Error(
              "No audio track was shared. In Chromium/Linux, select an 'Edge Tab' (e.g. Google Meet tab) with 'Share tab audio' checked.",
            );
          }
        } else {
          systemStream = display;
          this.streams.push(display);
          system = true;
        }
      } catch (error) {
        if (mode === "system") throw error;
        // In mixed mode, if display audio fails or has no audio, we still allow microphone to continue
      }
    }
    const mix = this.context.createGain();
    for (const stream of this.streams) this.context.createMediaStreamSource(stream).connect(mix);
    this.processor = this.context.createScriptProcessor(4096, 1, 1);
    const silentMix = this.context.createGain();
    silentMix.gain.value = 0;
    mix.connect(this.processor);
    this.processor.connect(silentMix);
    silentMix.connect(this.context.destination);
    this.processor.onaudioprocess = (event) => this.processPcm(event.inputBuffer.getChannelData(0));

    const floorStream = microphoneStream;
    if (floorStream) {
      const floorSource = this.context.createMediaStreamSource(floorStream);
      this.floorProcessor = this.context.createScriptProcessor(2048, 1, 1);
      const silentFloor = this.context.createGain();
      silentFloor.gain.value = 0;
      floorSource.connect(this.floorProcessor);
      this.floorProcessor.connect(silentFloor);
      silentFloor.connect(this.context.destination);
      this.floorProcessor.onaudioprocess = (event) => {
        this.processFloor(event.inputBuffer.getChannelData(0));
      };
    }
    await this.context.resume();
    return { microphone, system };
  }

  async stopCapture(): Promise<void> {
    this.stopPlayback();
    this.processor?.disconnect();
    this.processor = undefined;
    this.floorProcessor?.disconnect();
    this.floorProcessor = undefined;
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

  async beginPcmStream(): Promise<void> {
    this.stopPlayback();
    const context = this.context && this.context.state !== "closed" ? this.context : new AudioContext();
    if (!this.context) this.context = context;
    this.playbackActive = true;
    this.streamEnded = false;
    this.streamNextTime = context.currentTime + 0.04;
    this.streamPromise = new Promise<void>((resolve) => {
      this.streamResolve = resolve;
    });
    await context.resume();
  }

  pushPcmChunk(base64: string, sampleRate: number): void {
    if (!this.playbackActive || !this.context) return;
    const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
    const samples = new Int16Array(bytes.buffer);
    if (samples.length === 0) return;
    const buffer = this.context.createBuffer(1, samples.length, sampleRate);
    const channel = buffer.getChannelData(0);
    for (let index = 0; index < samples.length; index += 1) channel[index] = samples[index] / 32768;
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.context.destination);
    const startAt = Math.max(this.context.currentTime + 0.015, this.streamNextTime);
    this.streamNextTime = startAt + buffer.duration;
    this.streamSources.add(source);
    source.onended = () => {
      this.streamSources.delete(source);
      this.completePcmStreamIfReady();
    };
    source.start(startAt);
  }

  endPcmStream(): Promise<void> {
    this.streamEnded = true;
    this.completePcmStreamIfReady();
    return this.streamPromise ?? Promise.resolve();
  }

  async playCue(audio?: { data_base64?: string; format?: string; sample_rate?: number }): Promise<void> {
    if (this.playbackActive || this.floorBusy || this.cue || !audio?.data_base64) return;
    const request = ++this.cueRequest;
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
    if (request !== this.cueRequest || this.playbackActive || this.floorBusy || this.cue) return;

    const node = context.createBufferSource();
    node.buffer = buffer;
    const masterGain = context.createGain();
    const now = context.currentTime;
    const duration = Math.min(0.42, buffer.duration);
    masterGain.gain.setValueAtTime(0.0001, now);
    masterGain.gain.exponentialRampToValueAtTime(0.2, now + 0.05);
    masterGain.gain.setValueAtTime(0.2, now + Math.max(0.06, duration - 0.12));
    masterGain.gain.exponentialRampToValueAtTime(0.0001, now + duration);
    node.connect(masterGain);
    masterGain.connect(context.destination);
    this.cue = { node, masterGain };
    node.onended = () => {
      if (this.cue?.node === node) this.cue = undefined;
      try {
        node.disconnect();
        masterGain.disconnect();
      } catch {
        // The audio graph may already be disconnected with its context.
      }
    };
    node.start(now, 0, duration);
  }

  stopCue(): void {
    this.cueRequest += 1;
    if (!this.cue || !this.context) return;
    const cue = this.cue;
    this.cue = undefined;
    const now = this.context.currentTime;
    cue.masterGain.gain.cancelScheduledValues(now);
    cue.masterGain.gain.setValueAtTime(Math.max(cue.masterGain.gain.value, 0.0001), now);
    cue.masterGain.gain.exponentialRampToValueAtTime(0.0001, now + 0.08);
    cue.node.stop(now + 0.08);
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
    for (const source of this.streamSources) {
      try {
        source.stop();
      } catch {
        // The scheduled chunk may already have ended.
      }
    }
    this.streamSources.clear();
    this.streamEnded = true;
    this.streamResolve?.();
    this.streamResolve = undefined;
    this.streamPromise = undefined;
  }

  private completePcmStreamIfReady(): void {
    if (!this.streamEnded || this.streamSources.size > 0) return;
    this.playbackActive = false;
    this.streamResolve?.();
    this.streamResolve = undefined;
    this.streamPromise = undefined;
  }

  private processFloor(input: Float32Array): void {
    if (!this.context) return;
    let energy = 0;
    for (const sample of input) energy += sample * sample;
    const rms = Math.sqrt(energy / input.length);
    this.onLevel(Math.min(1, rms / 0.12));
    const now = performance.now();
    const threshold = this.playbackActive ? 0.08 : 0.018;
    const requiredFrames = this.playbackActive ? 6 : 2;
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
  }

  private processPcm(input: Float32Array): void {
    if (!this.context) return;
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
