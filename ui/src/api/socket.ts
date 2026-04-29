import type { Event, InboundMessage } from "./events";

export type EventHandler = (event: Event) => void;

export interface SocketOptions {
  userId: string;
  sessionId: string;
  onEvent: EventHandler;
  onStatus?: (status: "connecting" | "open" | "closed") => void;
}

// Reconnects on close and resumes via `?last_event_id=` so a laptop sleep
// or network blip does not lose anything emitted while away.
export class PishkarSocket {
  private ws: WebSocket | null = null;
  private lastEventId: string | null = null;
  private closed = false;
  private reconnectDelay = 500;

  constructor(private opts: SocketOptions) {}

  connect(): void {
    this.opts.onStatus?.("connecting");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const qs = this.lastEventId ? `?last_event_id=${encodeURIComponent(this.lastEventId)}` : "";
    const url = `${proto}://${location.host}/ws/${this.opts.userId}/${this.opts.sessionId}${qs}`;
    const ws = new WebSocket(url);
    this.ws = ws;

    ws.onopen = () => {
      this.reconnectDelay = 500;
      this.opts.onStatus?.("open");
    };
    ws.onmessage = (msg) => {
      let event: Event;
      try {
        event = JSON.parse(msg.data) as Event;
      } catch {
        return;
      }
      if (event.event_id) this.lastEventId = event.event_id;
      this.opts.onEvent(event);
    };
    ws.onclose = () => {
      this.opts.onStatus?.("closed");
      if (this.closed) return;
      setTimeout(() => this.connect(), this.reconnectDelay);
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, 10_000);
    };
  }

  send(content: string): void {
    const payload: InboundMessage = { content };
    this.ws?.send(JSON.stringify(payload));
  }

  close(): void {
    this.closed = true;
    this.ws?.close();
  }
}
