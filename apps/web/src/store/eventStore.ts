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

interface EventStoreState {
  wsStatus: WsStatus;
  lastSeq: number;
  events: WireEvent[];
  coreState: CoreState;
  setWsStatus: (status: WsStatus) => void;
  pushEvent: (event: WireEvent) => void;
  reset: () => void;
}

const MAX_EVENTS = 200;

export const useEventStore = create<EventStoreState>((set) => ({
  wsStatus: "idle",
  lastSeq: 0,
  events: [],
  coreState: "connecting",
  setWsStatus: (status) =>
    set((state) => ({
      wsStatus: status,
      coreState:
        status === "open"
          ? state.coreState === "connecting"
            ? "idle"
            : state.coreState
          : status === "reconnecting" || status === "connecting"
            ? "connecting"
            : status === "closed"
              ? "offline"
              : state.coreState,
    })),
  pushEvent: (event) =>
    set((state) => {
      const nextSeq = Math.max(state.lastSeq, event.sequence || 0);
      let coreState = state.coreState;
      if (event.type === "core.state.changed") {
        const candidate = event.payload?.state;
        if (typeof candidate === "string") {
          coreState = candidate as CoreState;
        }
      }
      const events = [event, ...state.events].slice(0, MAX_EVENTS);
      return { events, lastSeq: nextSeq, coreState };
    }),
  reset: () => set({ wsStatus: "idle", lastSeq: 0, events: [], coreState: "connecting" }),
}));
