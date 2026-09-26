import { StrictMode, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Columns3,
  Download,
  FileText,
  MessageSquareText,
  Pause,
  Pencil,
  Play,
  Send,
  Sparkles,
  Square,
  Workflow,
  Volume2,
  VolumeX,
} from "lucide-react";

import {
  bootstrap,
  deleteMeetingSession,
  exportMeetingSession,
  liveSocket,
  renameMeetingSession,
  send,
} from "./api";
import { AudioBridge, type CaptureMode } from "./audio";
import { NotesView } from "./components/NotesView";
import { WorkflowCanvas } from "./components/WorkflowCanvas";
import { CLIENT_PROTOCOL_VERSION } from "./protocol";
import type { MeetingLanguage, MeetingState, ServerMessage, SessionSummary, SpeechAuthorized } from "./protocol";
import "./style.css";

type View = "flow" | "board" | "notes" | "transcript";
type AudioStatus = "idle" | "requesting" | "active" | "paused" | "error";

const WAVE_SHAPE = [0.45, 0.7, 1, 0.6, 0.85, 0.5, 0.95, 0.65, 0.8, 0.4, 0.75, 0.55];
const LANGUAGES: Array<{ value: MeetingLanguage; label: string }> = [
  { value: "en", label: "English" },
  { value: "fr", label: "Français" },
  { value: "es", label: "Español" },
  { value: "de", label: "Deutsch" },
  { value: "pt", label: "Português" },
];

function App() {
  const [state, setState] = useState<MeetingState>();
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [partial, setPartial] = useState("");
  const [connected, setConnected] = useState(false);
  const [view, setView] = useState<View>("flow");
  const [assistantName, setAssistantName] = useState("Assistant");
  const [captureMode, setCaptureMode] = useState<CaptureMode>("mixed");
  const [language, setLanguage] = useState<MeetingLanguage>("en");
  const [manual, setManual] = useState("");
  const [error, setError] = useState("");
  const [audioStatus, setAudioStatus] = useState<AudioStatus>("idle");
  const [audioLevel, setAudioLevel] = useState(0);
  const socketRef = useRef<WebSocket | undefined>(undefined);
  const audioRef = useRef<AudioBridge | undefined>(undefined);
  const languageRef = useRef<MeetingLanguage>("en");
  const activeSpeechRef = useRef<string | null>(null);

  useEffect(() => {
    let active = true;
    const bootstrapRequest = bootstrap();
    void bootstrapRequest.then((payload) => {
      if (!active) return;
      if (payload.protocol_version !== CLIENT_PROTOCOL_VERSION) {
        throw new Error(`Frontend protocol ${CLIENT_PROTOCOL_VERSION} does not match backend protocol ${payload.protocol_version}. Restart the backend and reload.`);
      }
      document.title = payload.project_id;
      setState(payload.state);
      setSessions(payload.sessions);
      setAssistantName(payload.state.assistant_name);
      const initialLanguage = payload.state.language ?? "en";
      setLanguage(initialLanguage);
      languageRef.current = initialLanguage;
    }).catch((reason: unknown) => setError(String(reason)));
    const socket = liveSocket();
    socketRef.current = socket;
    const audio = new AudioBridge(
      (chunk) => {
        if (socket.readyState === WebSocket.OPEN) socket.send(chunk);
      },
      (busy) => {
        send(socket, { type: "floor.changed", busy });
        if (busy) audio.stopCue();
      },
      setAudioLevel,
      () => {
        const speechId = activeSpeechRef.current;
        activeSpeechRef.current = null;
        if (speechId && socket.readyState === WebSocket.OPEN) {
          send(socket, { type: "playback.interrupted", speech_id: speechId });
        }
      },
    );
    audioRef.current = audio;
    socket.onopen = async () => {
      try {
        const payload = await bootstrapRequest;
        if (payload.protocol_version !== CLIENT_PROTOCOL_VERSION) throw new Error("Backend restart required.");
        setConnected(true);
        send(socket, {
          type: "client.hello",
          protocol_version: CLIENT_PROTOCOL_VERSION,
          client_id: crypto.randomUUID(),
          capabilities: { audio_capture: true, audio_playback: true },
        });
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
        socket.close();
      }
    };
    socket.onclose = () => setConnected(false);
    socket.onmessage = (event) => {
      const message = JSON.parse(String(event.data)) as ServerMessage;
      if (message.type === "state.snapshot") {
        setState(message.state);
        setSessions(message.sessions);
        const currentLanguage = message.state.language ?? "en";
        setLanguage(currentLanguage);
        languageRef.current = currentLanguage;
      }
      else if (message.type === "transcript.partial") setPartial(message.text);
      else if (message.type === "speech.stop") {
        audio.stopPlayback();
        if (activeSpeechRef.current === message.speech_id) activeSpeechRef.current = null;
      }
      else if (message.type === "speech.authorized") {
        void playSpeech(socket, audio, message, languageRef.current, activeSpeechRef);
      }
      else if (message.type === "presence.cue") void audio.playCue(message.audio ?? undefined);
      else if (message.type === "protocol.error") setError(message.message);
    };
    return () => {
      active = false;
      socket.close();
      audio.stopPlayback();
      void audio.stopCapture();
    };
  }, []);

  async function start(): Promise<void> {
    setError("");
    try {
      const socket = socketRef.current;
      if (!socket || socket.readyState !== WebSocket.OPEN) throw new Error("Backend connection is not ready.");
      interruptPlayback();
      await startCapture();
      send(socket, {
        type: "session.start",
        assistant_name: assistantName,
        language,
        capture_mode: captureMode,
        output_mode: "local_only",
      });
    } catch (reason) {
      setAudioStatus("error");
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function command(value: "pause" | "resume" | "stop"): Promise<void> {
    setError("");
    try {
      const socket = socketRef.current;
      if (!socket || socket.readyState !== WebSocket.OPEN) throw new Error("Backend connection is not ready.");
      if (value === "pause" || value === "stop") interruptPlayback();
      if (value === "resume") await startCapture();
      if (value === "pause" || value === "stop") {
        await audioRef.current?.stopCapture();
        setAudioStatus(value === "pause" ? "paused" : "idle");
      }
      send(socket, { type: "session.command", command: value });
    } catch (reason) {
      setAudioStatus("error");
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function startCapture(): Promise<void> {
    setAudioStatus("requesting");
    await audioRef.current?.start(captureMode);
    setAudioStatus("active");
  }

  async function resumeSession(sessionId: string): Promise<void> {
    setError("");
    try {
      const socket = socketRef.current;
      if (!socket || socket.readyState !== WebSocket.OPEN) throw new Error("Backend connection is not ready.");
      interruptPlayback();
      await startCapture();
      send(socket, { type: "session.open", session_id: sessionId });
      send(socket, { type: "session.command", command: "resume" });
    } catch (reason) {
      setAudioStatus("error");
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function changeLanguage(value: MeetingLanguage): void {
    setLanguage(value);
    languageRef.current = value;
    if (state?.session_id && socketRef.current) {
      send(socketRef.current, { type: "session.language", language: value });
    }
  }

  function interruptPlayback(): void {
    audioRef.current?.stopPlayback();
    const speechId = activeSpeechRef.current;
    activeSpeechRef.current = null;
    const socket = socketRef.current;
    if (speechId && socket?.readyState === WebSocket.OPEN) {
      send(socket, { type: "playback.interrupted", speech_id: speechId });
    }
  }

  async function renameSession(sessionId: string, currentTitle: string): Promise<void> {
    const title = window.prompt("Session title", currentTitle)?.trim();
    if (!title || title === currentTitle) return;
    try {
      await renameMeetingSession(sessionId, title);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function deleteSession(sessionId: string, title: string): Promise<void> {
    if (!window.confirm(`Delete "${title}" permanently?`)) return;
    try {
      await deleteMeetingSession(sessionId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function inject(): void {
    const text = manual.trim();
    if (!text || !socketRef.current) return;
    send(socketRef.current, { type: "transcript.inject", text });
    setManual("");
  }

  if (!state) return <main className="loading">{error || "Connecting to the sidecar..."}</main>;
  const listening = state.session_status === "listening";
  const capturing = audioStatus === "active" || audioStatus === "requesting";
  const navigation: Array<{ view: View; label: string; icon: typeof Workflow }> = [
    { view: "flow", label: "Flow", icon: Workflow },
    { view: "board", label: "Board", icon: Columns3 },
    { view: "notes", label: "Notes", icon: FileText },
    { view: "transcript", label: "Transcript", icon: MessageSquareText },
  ];

  return (
    <main>
      <header className={listening ? "session-header live" : "session-header"}>
        <div className="brand">
          <span className={`pulse ${listening ? "live" : ""}`} />
          <div><strong>{state.title || state.assistant_name}</strong><small>{state.assistant_name} / {state.session_status}</small></div>
        </div>
        <nav className="view-nav">
          {navigation.map((item) => {
            const Icon = item.icon;
            return <button className={view === item.view ? "active" : ""} onClick={() => setView(item.view)} key={item.view} title={item.label}><Icon size={16} /><span>{item.label}</span></button>;
          })}
        </nav>
        <div className="header-actions">
          {state.session_status !== "idle" && state.session_status !== "closed" && <>
            <select className="header-language" value={language} onChange={(event) => changeLanguage(event.target.value as MeetingLanguage)} title="Meeting language">
              {LANGUAGES.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}
            </select>
            {state.cards.length > 0 && <button className="icon-button" onClick={() => socketRef.current && send(socketRef.current, { type: "board.curate" })} title="Curate board"><Sparkles size={17} /></button>}
            <button className={`icon-button ${state.voice_mode === "muted" ? "danger" : ""}`} onClick={() => socketRef.current && send(socketRef.current, { type: "voice.mode", mode: state.voice_mode === "muted" ? "active" : "muted" })} title={state.voice_mode === "muted" ? "Enable voice" : "Mute voice"}>{state.voice_mode === "muted" ? <VolumeX size={17} /> : <Volume2 size={17} />}</button>
            {state.session_id && <button className="icon-button" onClick={() => void renameSession(state.session_id!, state.title)} title="Rename session"><Pencil size={17} /></button>}
            {state.session_id && <button className="icon-button" onClick={() => exportMeetingSession(state.session_id!)} title="Export session"><Download size={17} /></button>}
            <button className="icon-button primary" onClick={() => void command(listening && capturing ? "pause" : "resume")} title={listening && capturing ? "Pause listening" : "Resume listening"}>
              {listening && capturing ? <Pause size={17} /> : <Play size={17} />}
            </button>
            <button className="icon-button danger" onClick={() => void command("stop")} title="End session"><Square size={16} /></button>
          </>}
          <div className={`connection ${connected ? "online" : ""}`}><i />{connected ? "live" : "offline"}</div>
        </div>
      </header>

      {state.session_status === "idle" || state.session_status === "closed" ? (
        <section className="launch">
          <p className="eyebrow">AN AGENT AT THE TABLE</p>
          <h1>Listen deeply.<br />Enter lightly.</h1>
          <p>Run beside any meeting. No bot joins the call.</p>
          <div className="launch-controls">
            <label>Wake name<input value={assistantName} onChange={(event) => setAssistantName(event.target.value)} /></label>
            <label>Language<select value={language} onChange={(event) => changeLanguage(event.target.value as MeetingLanguage)}>
              {LANGUAGES.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}
            </select></label>
            <label>Audio source<select value={captureMode} onChange={(event) => setCaptureMode(event.target.value as CaptureMode)}>
              <option value="microphone">Microphone</option>
              <option value="mixed">Microphone + system audio</option>
              <option value="system">System audio</option>
            </select></label>
           <button className="start" onClick={() => void start()}>New session</button>
          </div>
          {error && <p className="error">{error}</p>}
          {sessions.length > 0 && <div className="session-library">
            <div className="session-library-heading"><span>Previous sessions</span><b>{sessions.length}</b></div>
            <div className="session-list">
              {sessions.map((session) => <article className="session-item" key={session.session_id}>
                <button className="session-resume" onClick={() => void resumeSession(session.session_id)}>
                  <span><strong>{session.title}</strong><small>{session.preview || "No transcript yet"}</small></span>
                  <span className="session-meta"><small>{session.utterance_count} turns</small><time>{new Date(session.updated_at).toLocaleDateString()}</time></span>
                </button>
                <div className="session-tools">
                  <button onClick={() => void renameSession(session.session_id, session.title)}>Rename</button>
                  <button onClick={() => exportMeetingSession(session.session_id)}>Export</button>
                  <button className="danger" onClick={() => void deleteSession(session.session_id, session.title)}>Delete</button>
                </div>
              </article>)}
            </div>
          </div>}
        </section>
      ) : (
        <>
          <section className="health-row compact">
            {Object.entries(state.health).map(([name, health]) => (
              <span className={`health ${health.status}`} key={name}><i />{name}<b>{health.status}</b></span>
            ))}
          </section>
          <section className="view-stage">
              {view === "flow" && <WorkflowCanvas state={state} audioStatus={audioStatus} />}
              {view === "board" && <Board state={state} />}
              {view === "notes" && <NotesView state={state} />}
              {view === "transcript" && <Transcript state={state} partial={partial} />}
          </section>
          <section className="manual command-bar">
            <input value={manual} onChange={(event) => setManual(event.target.value)} onKeyDown={(event) => event.key === "Enter" && inject()} placeholder="Inject a sentence when testing without audio" />
            <button onClick={inject} title="Send"><Send size={17} /></button>
          </section>
          {error && <p className="error global-error">{error}</p>}
          <ActivityStrip state={state} partial={partial} audioStatus={audioStatus} audioLevel={audioLevel} />
        </>
      )}
    </main>
  );
}

async function playSpeech(
  socket: WebSocket,
  audio: AudioBridge,
  message: SpeechAuthorized,
  language: string,
  activeSpeech: { current: string | null },
): Promise<void> {
  activeSpeech.current = message.speech_id;
  send(socket, { type: "playback.started", speech_id: message.speech_id });
  try {
    if (message.audio) {
      await audio.playAudio(message.audio.data_base64, message.audio.format, message.audio.sample_rate);
    }
    if (activeSpeech.current === message.speech_id) {
      activeSpeech.current = null;
      send(socket, { type: "playback.finished", speech_id: message.speech_id });
    }
  } catch {
    activeSpeech.current = null;
    send(socket, { type: "playback.interrupted", speech_id: message.speech_id });
  }
}

function Board({ state }: { state: MeetingState }) {
  if (!state.cards.length) return <div className="empty"><span>01</span><h2>The board grows with the conversation.</h2><p>Ideas, decisions and sourced findings will appear here.</p></div>;
  return <div className="board">{state.cards.slice().reverse().map((card, index) => <article className={`card c${index % 4}`} key={card.id}><small>{card.kind}</small><h2>{card.title}</h2><p>{card.body}</p></article>)}</div>;
}

function Transcript({ state, partial }: { state: MeetingState; partial: string }) {
  return <div className="transcript">{state.transcript.slice().reverse().map((item) => <div className="line" key={item.id}><time>{item.committed_at ? new Date(item.committed_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "now"}</time><p>{item.text}</p></div>)}{partial && <div className="line partial"><time>live</time><p>{partial}</p></div>}</div>;
}

function ActivityStrip({ state, partial, audioStatus, audioLevel }: { state: MeetingState; partial: string; audioStatus: AudioStatus; audioLevel: number }) {
  const latest = state.activities.at(-1);
  return <footer>
    <div className="audio-monitor" aria-label={`Microphone ${audioStatus}`}>
      <span>mic {audioStatus}</span>
      <div className="wave" aria-hidden="true">
        {WAVE_SHAPE.map((shape, index) => <i key={index} style={{ height: `${4 + audioLevel * shape * 24}px` }} />)}
      </div>
    </div>
    <p>{partial || latest?.summary || "Waiting for the room."}</p>
  </footer>;
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
