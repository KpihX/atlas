import {
  AudioLines,
  Bot,
  BrainCircuit,
  Database,
  FileText,
  Lightbulb,
  Mic2,
  Search,
  Volume2,
  X,
} from "lucide-react";
import { useState, type ComponentType } from "react";
import type { AtlasState } from "../protocol";

type AudioStatus = "idle" | "requesting" | "active" | "paused" | "error";
type NodeKey = "room" | "stt" | "memory" | "jev" | "speaker" | "worker" | "notes" | "board" | "voice";

type FlowNode = {
  key: NodeKey;
  label: string;
  detail: string;
  status: string;
  icon: ComponentType<{ size?: number }>;
  active: boolean;
};

const POSITIONS: Record<NodeKey, { x: number; y: number }> = {
  room: { x: 28, y: 220 },
  stt: { x: 222, y: 220 },
  memory: { x: 440, y: 205 },
  jev: { x: 440, y: 52 },
  speaker: { x: 700, y: 42 },
  worker: { x: 700, y: 190 },
  notes: { x: 700, y: 338 },
  board: { x: 440, y: 430 },
  voice: { x: 930, y: 42 },
};

const EDGES: Array<[NodeKey, NodeKey, string]> = [
  ["room", "stt", "audio"],
  ["stt", "memory", "heard"],
  ["memory", "jev", "decision context"],
  ["jev", "speaker", "direct response"],
  ["jev", "worker", "background mission"],
  ["worker", "memory", "worker"],
  ["memory", "notes", "notes"],
  ["memory", "board", "board"],
  ["speaker", "voice", "voice"],
  ["voice", "room", "voice"],
];

export function WorkflowCanvas({ state, audioStatus }: { state: AtlasState; audioStatus: AudioStatus }) {
  const [selected, setSelected] = useState<NodeKey | null>(null);
  const agentRuns = state.agent_runs ?? [];
  const running = agentRuns.filter((run) => run.status === "running");
  const tasks = state.tasks ?? [];
  const runningTasks = tasks.filter((task) => task.status === "running" || task.status === "queued");
  const lastTask = tasks.at(-1);
  const speaker = running.find((run) => run.agent === "speaker");
  const worker = running.find((run) => run.agent === "worker" || run.agent === "coordinator");
  const notes = running.find((run) => run.agent === "notes");
  const naming = running.find((run) => run.agent === "naming");
  const speech = state.speeches.at(-1);
  const decision = state.decisions?.at(-1);
  const decisionActive = Boolean(
    decision?.decided_at && Date.now() - new Date(decision.decided_at).getTime() < 3000,
  );
  const recentBoardActivity = state.activities
    .slice()
    .reverse()
    .find((item) => item.kind.startsWith("board."));
  const recentBoardActivityAt = recentBoardActivity?.created_at;
  const boardActive = Boolean(
    worker?.agent === "coordinator"
    || (recentBoardActivityAt && Date.now() - new Date(recentBoardActivityAt).getTime() < 3000),
  );
  const pipeline = state.pipeline;
  const nodes: FlowNode[] = [
    { key: "room", label: "Room", detail: `${pipeline?.audio_frames ?? 0} audio frames`, status: audioStatus, icon: Mic2, active: audioStatus === "active" },
    { key: "stt", label: "Gradium STT", detail: `${pipeline?.stt_fragments ?? 0} fragments, ${pipeline?.stt_turns ?? 0} turns`, status: pipeline?.stt_last_event || "waiting", icon: AudioLines, active: state.session_status === "listening" },
    { key: "memory", label: "Shared memory", detail: `${state.transcript.length} turns, ${tasks.length} tasks`, status: "synced", icon: Database, active: running.length > 0 || runningTasks.length > 0 },
    { key: "jev", label: "Jev router", detail: decision ? `${decision.result.route} / ${decision.result.addressee} / ${decision.result.initiative}` : "Waiting for a turn", status: decisionActive ? "deciding" : "idle", icon: BrainCircuit, active: decisionActive },
    { key: "speaker", label: "Speaker", detail: speaker?.summary || "Ready when addressed", status: speaker?.status || (state.voice_mode === "muted" ? "muted" : "idle"), icon: Bot, active: Boolean(speaker) },
    { key: "worker", label: "Workers", detail: worker?.summary || lastTask?.summary || "No mission running", status: runningTasks.length ? `${runningTasks.length} running` : lastTask?.status || "idle", icon: Search, active: Boolean(worker || runningTasks.length) },
    { key: "notes", label: "Notes agent", detail: notes?.summary || `${state.notes_cursor ?? 0}/${state.transcript.length} turns integrated`, status: notes?.status || `v${state.notes_version ?? 0}`, icon: FileText, active: Boolean(notes) },
    { key: "board", label: "Board curator", detail: `${state.cards.length} durable concepts`, status: boardActive ? "curating" : naming ? "naming" : "synced", icon: Lightbulb, active: boardActive },
    { key: "voice", label: "Voice", detail: speech?.text || "Waiting for a useful moment", status: speech?.status || "idle", icon: Volume2, active: Boolean(speech && ["waiting_gap", "authorized", "playing"].includes(speech.status)) },
  ];
  const active = new Set<NodeKey>(nodes.filter((node) => node.active).map((node) => node.key));

  return <section className="workflow-shell">
    <header className="workflow-heading">
      <div><BrainCircuit /><span>Agent workflow</span></div>
      <p>{running.length} agents and {runningTasks.length} workers active</p>
    </header>
    <div className="workflow-viewport">
      <div className="workflow-canvas">
        <svg viewBox="0 0 1120 570" aria-hidden="true">
          <defs><filter id="glow"><feGaussianBlur stdDeviation="3" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter></defs>
          {EDGES.map(([from, to, channel]) => {
            const a = POSITIONS[from];
            const b = POSITIONS[to];
            const lit = active.has(from) && active.has(to);
            return <g key={`${from}-${to}`} className={`flow-edge ${lit ? "active" : ""}`}>
              <path d={`M ${a.x + 75} ${a.y + 48} C ${(a.x + b.x) / 2 + 75} ${a.y + 48}, ${(a.x + b.x) / 2 + 75} ${b.y + 48}, ${b.x + 75} ${b.y + 48}`} />
              <circle r="4" filter="url(#glow)"><animateMotion dur="1.4s" repeatCount="indefinite" path={`M ${a.x + 75} ${a.y + 48} C ${(a.x + b.x) / 2 + 75} ${a.y + 48}, ${(a.x + b.x) / 2 + 75} ${b.y + 48}, ${b.x + 75} ${b.y + 48}`} /></circle>
              <title>{channel}</title>
            </g>;
          })}
        </svg>
        {nodes.map((node) => {
          const Icon = node.icon;
          const position = POSITIONS[node.key];
          return <button className={`flow-node ${node.active ? "active" : ""}`} data-node={node.key} key={node.key} style={{ left: position.x, top: position.y }} onClick={() => setSelected(node.key)}>
            <div className="node-icon"><Icon size={18} /></div>
            <div className="node-copy"><strong>{node.label}</strong><span>{node.detail}</span></div>
            <small>{node.status}</small>
          </button>;
        })}
      </div>
    </div>
    {selected && <NodeInspector node={selected} state={state} close={() => setSelected(null)} />}
  </section>;
}

function NodeInspector({ node, state, close }: { node: NodeKey; state: AtlasState; close: () => void }) {
  const runs = (state.agent_runs ?? []).filter((run) => {
    if (node === "speaker") return run.agent === "speaker";
    if (node === "worker") return run.agent === "worker" || run.agent === "coordinator";
    return run.agent === node;
  }).slice(-8).reverse();
  const title = node === "jev" ? "Jev decision universe" : `${node[0].toUpperCase()}${node.slice(1)} universe`;
  return <aside className="node-inspector">
    <header><div><span>Inspecting</span><h2>{title}</h2></div><button onClick={close}><X size={18} /></button></header>
    {node === "jev" && <div className="inspector-stack">
      {(state.decisions ?? []).slice(-6).reverse().map((decision) => <article className="inspection-card" key={decision.id}>
        <div><b>{decision.result.route}</b><time>{decision.decided_at ? new Date(decision.decided_at).toLocaleTimeString() : "now"}</time></div>
        <p>{decision.context.new_utterance as string}</p>
        <dl><dt>Addressee</dt><dd>{decision.result.addressee}</dd><dt>Initiative</dt><dd>{decision.result.initiative}</dd><dt>Memory</dt><dd>{decision.result.memory}</dd><dt>Timing</dt><dd>{decision.result.timing}</dd></dl>
        <details><summary>Context sent to Jev</summary><pre>{JSON.stringify(decision.context, null, 2)}</pre></details>
      </article>)}
      {!state.decisions?.length && <p className="muted">No Jev decision yet.</p>}
    </div>}
    {node === "worker" && <div className="inspector-stack">
      {state.tasks.slice().reverse().map((task) => <article className="inspection-card" key={task.id}><div><b>{task.tool}</b><span className={task.status}>{task.phase}</span></div><p>{task.summary}</p>{task.result && <details><summary>Result projection</summary><pre>{JSON.stringify(task.result, null, 2)}</pre></details>}</article>)}
      {!state.tasks.length && <p className="muted">No worker mission yet.</p>}
    </div>}
    {node === "memory" && <div className="memory-inspector"><Stat label="Transcript" value={state.transcript.length} /><Stat label="Cards" value={state.cards.length} /><Stat label="Tasks" value={state.tasks.length} /><Stat label="Notes version" value={state.notes_version ?? 0} /><Stat label="Speech turns" value={state.speeches.length} /><Stat label="Activities" value={state.activities.length} /></div>}
    {node === "room" && <div className="memory-inspector"><Stat label="Audio frames" value={state.pipeline?.audio_frames ?? 0} /><Stat label="Audio bytes" value={state.pipeline?.audio_bytes ?? 0} /><Stat label="Committed turns" value={state.pipeline?.stt_turns ?? 0} /></div>}
    {node === "stt" && <div className="memory-inspector"><Stat label="Messages" value={state.pipeline?.stt_messages ?? 0} /><Stat label="Fragments" value={state.pipeline?.stt_fragments ?? 0} /><Stat label="Final turns" value={state.pipeline?.stt_turns ?? 0} /><Stat label="Silence" value={`${Math.round((state.pipeline?.stt_inactivity_probability ?? 0) * 100)}%`} /></div>}
    {node === "notes" && <div className="memory-inspector"><Stat label="Version" value={state.notes_version ?? 0} /><Stat label="Integrated" value={`${state.notes_cursor ?? 0}/${state.transcript.length}`} /><Stat label="Characters" value={state.notes.length} /></div>}
    {node === "board" && <div className="inspector-stack">{state.activities.filter((item) => item.kind.startsWith("board.")).slice(-10).reverse().map((item) => <article className="inspection-card" key={item.id}><b>{item.kind}</b><p>{item.summary}</p></article>)}</div>}
    {node === "voice" && <div className="inspector-stack">{state.speeches.slice(-8).reverse().map((speech) => <article className="inspection-card" key={speech.id}><div><b>{speech.reason}</b><span className={speech.status}>{speech.status}</span></div><p>{speech.text}</p></article>)}</div>}
    {(node === "speaker" || node === "worker" || node === "notes") && <div className="inspector-stack">{runs.map((run) => <article className="inspection-card" key={run.id}><div><b>{run.summary}</b><span className={run.status}>{run.status}</span></div><p>{run.duration_ms ? `${(run.duration_ms / 1000).toFixed(1)} seconds` : "Running now"}</p>{run.error && <code>{run.error}</code>}</article>)}{!runs.length && <p className="muted">No run recorded.</p>}</div>}
  </aside>;
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return <div><span>{label}</span><b>{value}</b></div>;
}
