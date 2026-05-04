/**
 * 預設空字串＝與目前網頁同源（後端掛 dist、Cloudflare 任一子網域、或 Vite proxy 皆正確）。
 * 本機直連後端開發：frontend/.env 設 VITE_API_BASE=http://127.0.0.1:8000
 */
export function getApiBase(): string {
  const raw = import.meta.env.VITE_API_BASE?.trim();
  if (raw) return raw.replace(/\/$/, '');
  return '';
}

/** WebSocket 基底：同源 wss/ws，避免外部裝置誤連自身 localhost */
export function getWsBase(): string {
  const raw = import.meta.env.VITE_WS_BASE?.trim();
  if (raw) return raw.replace(/\/$/, '');
  const api = import.meta.env.VITE_API_BASE?.trim();
  if (api) {
    try {
      const u = new URL(api);
      const wsScheme = u.protocol === 'https:' ? 'wss:' : 'ws:';
      return `${wsScheme}//${u.host}`;
    } catch {
      /* fall through */
    }
  }
  if (typeof window !== 'undefined' && window.location?.host) {
    const { protocol, host } = window.location;
    const wsScheme = protocol === 'https:' ? 'wss:' : 'ws:';
    return `${wsScheme}//${host}`;
  }
  return 'ws://127.0.0.1:8000';
}
