import type { ServerMessage, StartSessionMessage, SubmitAnswerMessage } from '../types/messages';
import { getWsBase } from './apiBase';
const CLIENT_ID_KEY = 'wl_client_id';

function getOrCreateClientId(): string {
  const existing = localStorage.getItem(CLIENT_ID_KEY);
  if (existing) return existing;
  const generated = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `cid_${Math.random().toString(36).slice(2, 12)}`;
  localStorage.setItem(CLIENT_ID_KEY, generated);
  return generated;
}

export class LearningWebSocket {
  private ws: WebSocket | null = null;
  private sessionId: string;
  private token: string;
  private onMessage: (msg: ServerMessage) => void;
  private onOpen: () => void;
  private onClose: () => void;
  private clientId: string;

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
    this.clientId = getOrCreateClientId();
    this.onMessage = handlers.onMessage;
    this.onOpen = handlers.onOpen ?? (() => {});
    this.onClose = handlers.onClose ?? (() => {});
  }

  connect(): void {
    const wsBase = getWsBase();
    this.ws = new WebSocket(
      `${wsBase}/ws/${this.sessionId}?token=${encodeURIComponent(this.token)}&client_id=${encodeURIComponent(this.clientId)}`
    );
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
