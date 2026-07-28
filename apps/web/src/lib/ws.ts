import { API_URL } from "./api";
import { useEventStore, WireEvent } from "@/store/eventStore";

const MAX_BACKOFF_MS = 30_000;
const BASE_BACKOFF_MS = 1_000;
// The server sends a heartbeat every 20s. If we go this long with no frame at
// all (data OR heartbeat), the connection is half-open (mobile flap, proxy
// idle-cut, laptop sleep) - onclose may never fire, so we force it and let the
// backoff reconnect run. ~2.25x the heartbeat interval.
const STALE_CONNECTION_MS = 45_000;

/**
 * The WebSocket constructor requires an absolute ws(s):// URL - it cannot
 * take a relative path the way fetch() can. API_URL defaults to the
 * relative "/api" (see api.ts), so the common case here builds an absolute
 * URL from the current page's own origin, hitting Nginx's plain "/ws"
 * location (infra/nginx/rabit-os.conf.template) - deliberately NOT under
 * "/api", since that's where the FastAPI container's own unprefixed /ws
 * route actually lives. When API_URL is an explicit absolute override
 * (local dev without Nginx, e.g. http://localhost:8000), fall back to the
 * old same-origin-as-API-host behavior instead.
 */
function wsUrl(): string {
  if (/^https?:\/\//.test(API_URL)) {
    return `${API_URL.replace(/^http/, "ws")}/ws`;
  }
  if (typeof window === "undefined") return "";
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws`;
}

/**
 * Manages a single reconnecting WebSocket connection to the /ws realtime
 * feed. Reconnects with exponential backoff and always reconnects with
 * ?since_seq=<lastSeq> so the server replays anything missed.
 */
export class RealtimeClient {
  private socket: WebSocket | null = null;
  private attempt = 0;
  private closedByUser = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private watchdogTimer: ReturnType<typeof setInterval> | null = null;
  private lastFrameAt = 0;
  private generation = 0;

  start() {
    this.closedByUser = false;
    this.connect();
  }

  stop() {
    this.closedByUser = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.clearWatchdog();
    this.socket?.close();
    this.socket = null;
  }

  private markFrame() {
    this.lastFrameAt = Date.now();
  }

  private startWatchdog() {
    this.clearWatchdog();
    this.markFrame();
    this.watchdogTimer = setInterval(() => {
      if (this.socket && Date.now() - this.lastFrameAt > STALE_CONNECTION_MS) {
        // Force the socket shut so onclose fires and backoff-reconnect runs.
        this.socket.close();
      }
    }, 5_000);
  }

  private clearWatchdog() {
    if (this.watchdogTimer) {
      clearInterval(this.watchdogTimer);
      this.watchdogTimer = null;
    }
  }

  private connect() {
    if (this.closedByUser) return;
    const sinceSeq = useEventStore.getState().lastSeq;
    useEventStore.getState().setWsStatus(this.attempt === 0 ? "connecting" : "reconnecting");

    let socket: WebSocket;
    try {
      socket = new WebSocket(`${wsUrl()}?since_seq=${sinceSeq}`);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;
    // Each socket gets a generation. A stale socket's late-firing handlers
    // (e.g. a React StrictMode start->stop->start double-mount closes socket A
    // while B connects) must not hijack B's state or spawn a third socket.
    const gen = ++this.generation;
    const isCurrent = () => this.generation === gen && this.socket === socket;

    socket.onopen = () => {
      if (!isCurrent()) return;
      this.attempt = 0;
      useEventStore.getState().setWsStatus("open");
      this.startWatchdog();
    };

    socket.onmessage = (msg) => {
      if (!isCurrent()) return;
      // Any frame - data OR heartbeat - proves the connection is alive.
      this.markFrame();
      try {
        const data = JSON.parse(msg.data);
        if (data.type === "hello") {
          useEventStore.getState().applyEpoch(typeof data.epoch === "string" ? data.epoch : null);
          return;
        }
        if (data.type === "heartbeat") return;
        if (typeof data.sequence === "number" && typeof data.type === "string") {
          useEventStore.getState().pushEvent(data as WireEvent);
        }
      } catch {
        // ignore malformed frames
      }
    };

    socket.onclose = () => {
      if (!isCurrent()) return; // a superseded socket closing is not our concern
      this.socket = null;
      this.clearWatchdog();
      if (this.closedByUser) {
        useEventStore.getState().setWsStatus("closed");
        return;
      }
      useEventStore.getState().setWsStatus("reconnecting");
      this.scheduleReconnect();
    };

    socket.onerror = () => {
      socket.close();
    };
  }

  private scheduleReconnect() {
    if (this.closedByUser) return;
    this.attempt += 1;
    const delay = Math.min(BASE_BACKOFF_MS * 2 ** (this.attempt - 1), MAX_BACKOFF_MS);
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }
}

let sharedClient: RealtimeClient | null = null;

export function getRealtimeClient(): RealtimeClient {
  if (!sharedClient) sharedClient = new RealtimeClient();
  return sharedClient;
}
