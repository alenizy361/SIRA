import { create } from "zustand";

export type CoreState =
  | "offline"
  | "connecting"
  | "idle"
  | "listening"
  | "understanding"
  | "planning"
  | "delegating"
  | "coding"
  | "testing"
  | "reviewing"
  | "waiting_for_approval"
  | "deploying"
  | "monitoring"
  | "learning"
  | "speaking"
  | "warning"
  | "incident"
  | "paused";

export interface EntityRef {
  type: string;
  id: string;
}

export interface WireEvent {
  event_id: string;
  sequence: number;
  type: string;
  timestamp: string;
  organization_id: string;
  correlation_id: string;
  actor: string;
  entities: EntityRef[];
  payload: Record<string, unknown>;
}

export type WsStatus = "idle" | "connecting" | "open" | "reconnecting" | "closed";

/** Live per-agent activity, derived from the real event stream so the board
 *  can show an agent as WORKING for as long as it works - not just flash once.
 *  - received: a task/command just reached this agent
 *  - working:  it is actively running (started / tool use / progress)
 *  - done:     it finished (completed / reviewed)
 *  - blocked:  it is stuck / raised an incident */
export type AgentPhase = "received" | "working" | "done" | "blocked";

export interface AgentActivity {
  phase: AgentPhase;
  type: string; // last event type (consumer localizes the verb)
  detail: string; // short payload snippet (task title / stage / tool)
  since: number; // Date.now() of the last update
}

// Which event types move an agent into which phase.
const RECEIVED_EVENTS = new Set(["task.created", "task.assigned"]);
const WORKING_EVENTS = new Set([
  "task.started",
  "task.progress",
  "run.started",
  "run.tool.started",
  "run.tool.completed",
  "run.output.delta",
  "review.requested",
]);
const DONE_EVENTS = new Set(["task.completed", "review.completed", "run.completed"]);
const BLOCKED_EVENTS = new Set(["task.blocked", "incident.opened"]);

function phaseForEvent(type: string): AgentPhase | null {
  if (WORKING_EVENTS.has(type)) return "working";
  if (RECEIVED_EVENTS.has(type)) return "received";
  if (DONE_EVENTS.has(type)) return "done";
  if (BLOCKED_EVENTS.has(type)) return "blocked";
  return null;
}

function detailForEvent(e: WireEvent): string {
  const p = e.payload || {};
  const pick = (p.title ?? p.stage ?? p.tool_name ?? p.reason ?? p.decision ?? "") as unknown;
  return typeof pick === "string" ? pick : "";
}

interface EventStoreState {
  wsStatus: WsStatus;
  lastSeq: number;
  epoch: string | null;
  events: WireEvent[];
  coreState: CoreState;
  /** actor agent_key -> its latest live activity. */
  agentActivity: Record<string, AgentActivity>;
  setWsStatus: (status: WsStatus) => void;
  pushEvent: (event: WireEvent) => void;
  /** Apply the server's epoch from the hello frame; a change means the Redis
   *  counter rewound, so the high-water mark must reset or the feed goes mute. */
  applyEpoch: (epoch: string | null) => void;
  reset: () => void;
}

const MAX_EVENTS = 200;

export const useEventStore = create<EventStoreState>((set) => ({
  wsStatus: "idle",
  lastSeq: 0,
  epoch: null,
  events: [],
  coreState: "connecting",
  agentActivity: {},
  setWsStatus: (status) =>
    set((state) => ({
      wsStatus: status,
      // The connection status drives the header dot; it must NOT clobber the
      // real agent core state. A 1s network blip during a "deploying" run used
      // to reset the core to "connecting" -> "idle" and leave it wrong for the
      // rest of the run (replay only resends events with seq > since_seq, so
      // the last core.state.changed is never re-sent on a mid-session
      // reconnect). Preserve the last known real state; only fall back to a
      // placeholder when we genuinely have none.
      coreState:
        status === "open"
          ? state.coreState === "connecting" || state.coreState === "offline"
            ? "idle"
            : state.coreState
          : status === "closed"
            ? "offline"
            : state.coreState,
    })),
  pushEvent: (event) =>
    set((state) => {
      // De-dup by event_id: replay + live (and any duplicate socket during a
      // React StrictMode double-mount) can deliver the same event twice.
      if (state.events.some((e) => e.event_id === event.event_id)) {
        return state;
      }
      const nextSeq = Math.max(state.lastSeq, event.sequence || 0);
      let coreState = state.coreState;
      if (event.type === "core.state.changed") {
        const candidate = event.payload?.state;
        if (typeof candidate === "string") {
          coreState = candidate as CoreState;
        }
      }
      const events = [event, ...state.events].slice(0, MAX_EVENTS);

      // Maintain live per-agent activity so the board can hold an agent in a
      // "working" state for the whole run, and light the moment a task reaches
      // it - not just a single flash. Only real agent actors count (skip the
      // system bus and raw user ids so a capsule never lights for "the user").
      let agentActivity = state.agentActivity;
      const phase = phaseForEvent(event.type);
      const actor = event.actor;
      const isAgentActor = !!actor && actor !== "system" && actor.length <= 40;
      if (phase && isAgentActor) {
        agentActivity = {
          ...state.agentActivity,
          [actor]: { phase, type: event.type, detail: detailForEvent(event), since: Date.now() },
        };
      }
      return { events, lastSeq: nextSeq, coreState, agentActivity };
    }),
  applyEpoch: (epoch) =>
    set((state) => {
      if (epoch && state.epoch && epoch !== state.epoch) {
        // Counter rewound: forget the high-water mark and the stale backlog.
        return { epoch, lastSeq: 0, events: [], agentActivity: {} };
      }
      return { epoch: epoch ?? state.epoch };
    }),
  reset: () =>
    set({ wsStatus: "idle", lastSeq: 0, epoch: null, events: [], coreState: "connecting", agentActivity: {} }),
}));
