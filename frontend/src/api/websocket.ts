import type { ServerMessage, StartSessionMessage, SubmitAnswerMessage } from '../types/messages';
import { getWsBase } from './apiBase';

const CLIENT_ID_KEY = 'wl_client_id';
const KICKED_CLOSE_CODE = 4002;
const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 16000, 32000];

function getOrCreateClientId(): string {
  const existing = localStorage.getItem(CLIENT_ID_KEY);
  if (existing) return existing;
  const generated = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `cid_${Math.random().toString(36).slice(2, 12)}`;
  localStorage.setItem(CLIENT_ID_KEY, generated);
  return generated;
}

export type LearningWsHandlers = {
  onMessage: (msg: ServerMessage) => void;
  onOpen?: () => void;
  onClose?: () => void;
  /** Called BEFORE each reconnect attempt with attempt number (1-based). */
  onReconnecting?: (attempt: number) => void;
  /** Called AFTER ws.onopen fires when reconnectAttempt > 0. Use it to replay resume_session. */
  onReconnected?: () => void;
  /** Called when all reconnect attempts exhausted (6 tries). */
  onGiveUp?: () => void;
};

export class LearningWebSocket {
  private ws: WebSocket | null = null;
  private sessionId: string;
  private token: string;
  private handlers: LearningWsHandlers;
  private clientId: string;
  private reconnectAttempt = 0;
  private manuallyClosed = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(sessionId: string, token: string, handlers: LearningWsHandlers) {
    this.sessionId = sessionId;
    this.token = token;
    this.handlers = handlers;
    this.clientId = getOrCreateClientId();
  }

  connect(): void {
    this.manuallyClosed = false;
    this._open();
  }

  private _open(): void {
    const wsBase = getWsBase();
    this.ws = new WebSocket(
      `${wsBase}/ws/${this.sessionId}?token=${encodeURIComponent(this.token)}&client_id=${encodeURIComponent(this.clientId)}`
    );
    this.ws.onopen = () => {
      const wasReconnecting = this.reconnectAttempt > 0;
      this.reconnectAttempt = 0;
      if (wasReconnecting) {
        this.handlers.onReconnected?.();
      } else {
        this.handlers.onOpen?.();
      }
    };
    this.ws.onclose = (event) => {
      this.handlers.onClose?.();
      // 4002: 被其他裝置踢掉 — 不重連
      if (event.code === KICKED_CLOSE_CODE) {
        this.manuallyClosed = true;
        return;
      }
      if (this.manuallyClosed) return;
      this._scheduleReconnect();
    };
    this.ws.onmessage = (e) => {
      try {
        const msg: ServerMessage = JSON.parse(e.data);
        this.handlers.onMessage(msg);
      } catch {
        console.error('Invalid WS message', e.data);
      }
    };
  }

  private _scheduleReconnect(): void {
    if (this.reconnectAttempt >= RECONNECT_DELAYS_MS.length) {
      this.handlers.onGiveUp?.();
      return;
    }
    const delay = RECONNECT_DELAYS_MS[this.reconnectAttempt];
    this.reconnectAttempt += 1;
    this.handlers.onReconnecting?.(this.reconnectAttempt);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (this.manuallyClosed) return;
      this._open();
    }, delay);
  }

  send(msg: StartSessionMessage | SubmitAnswerMessage | { type: string; payload: object }): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    }
  }

  close(): void {
    this.manuallyClosed = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
  }
}
