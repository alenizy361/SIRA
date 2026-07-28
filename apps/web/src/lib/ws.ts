import { API_URL } from "./api";
import { useEventStore, WireEvent } from "@/store/eventStore";

const MAX_BACKOFF_MS = 30_000;
const BASE_BACKOFF_MS = 1_000;

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

  start() {
    this.closedByUser = false;
    this.connect();
  }

  stop() {
    this.closedByUser = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.socket?.close();
    this.socket = null;
  }

  private connect() {
    if (this.closedByUser) return;
    const wsBase = API_URL.replace(/^http/, "ws");
    const sinceSeq = useEventStore.getState().lastSeq;
    useEventStore.getState().setWsStatus(this.attempt === 0 ? "connecting" : "reconnecting");

    let socket: WebSocket;
    try {
      socket = new WebSocket(`${wsBase}/ws?since_seq=${sinceSeq}`);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;

    socket.onopen = () => {
      this.attempt = 0;
      useEventStore.getState().setWsStatus("open");
    };

    socket.onmessage = (msg) => {
      try {
        const data = JSON.parse(msg.data);
        if (data.type === "hello" || data.type === "heartbeat") return;
        if (typeof data.sequence === "number" && typeof data.type === "string") {
          useEventStore.getState().pushEvent(data as WireEvent);
        }
      } catch {
        // ignore malformed frames
      }
    };

    socket.onclose = () => {
      this.socket = null;
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
