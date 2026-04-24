import type { ServerMessage, StartSessionMessage, SubmitAnswerMessage } from '../types/messages';

const WS_BASE = 'ws://localhost:8000';

export class LearningWebSocket {
  private ws: WebSocket | null = null;
  private sessionId: string;
  private token: string;
  private onMessage: (msg: ServerMessage) => void;
  private onOpen: () => void;
  private onClose: () => void;

  constructor(
    sessionId: string,
    token: string,
    handlers: {
      onMessage: (msg: ServerMessage) => void;
      onOpen?: () => void;
      onClose?: () => void;
    }
  ) {
    this.sessionId = sessionId;
    this.token = token;
    this.onMessage = handlers.onMessage;
    this.onOpen = handlers.onOpen ?? (() => {});
    this.onClose = handlers.onClose ?? (() => {});
  }

  connect(): void {
    this.ws = new WebSocket(`${WS_BASE}/ws/${this.sessionId}?token=${this.token}`);
    this.ws.onopen = () => this.onOpen();
    this.ws.onclose = () => this.onClose();
    this.ws.onmessage = (e) => {
      try {
        const msg: ServerMessage = JSON.parse(e.data);
        this.onMessage(msg);
      } catch {
        console.error('Invalid WS message', e.data);
      }
    };
  }

  send(msg: StartSessionMessage | SubmitAnswerMessage | { type: string; payload: object }): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    }
  }

  close(): void {
    this.ws?.close();
  }
}
