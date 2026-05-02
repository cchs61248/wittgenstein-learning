import { saveUserUiState } from '../api/userUiState';
import type { SessionLayoutPrefs } from './sessionLayoutPrefs';
import { getAllLayoutPrefs, mergeLayoutPrefsFromServer } from './sessionLayoutPrefs';
import { loadBookOrder, replaceBookOrderFromServer } from './bookshelfOrder';

export const UI_STATE_SYNCED_EVENT = 'wl-ui-state-synced';

let debTimer: ReturnType<typeof setTimeout> | null = null;

export function dispatchUiStateSyncedFromServer() {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(UI_STATE_SYNCED_EVENT));
}

/** 伺服器回傳的 UI 狀態合併進本機；若兩者皆空（新帳號預設）則不覆寫書櫃順序。 */
export function applyServerUiStateToLocal(data: {
  layoutBySession?: unknown;
  bookshelfOrder?: unknown;
}) {
  const rawLayouts = data.layoutBySession;
  const layouts: Record<string, SessionLayoutPrefs> =
    rawLayouts && typeof rawLayouts === 'object' && !Array.isArray(rawLayouts)
      ? (rawLayouts as Record<string, SessionLayoutPrefs>)
      : {};
  const order = Array.isArray(data.bookshelfOrder) ? data.bookshelfOrder.map(String) : [];
  const hasLayouts = Object.keys(layouts).length > 0;
  const hasOrder = order.length > 0;
  if (hasLayouts) mergeLayoutPrefsFromServer(layouts);
  if (hasLayouts || hasOrder) replaceBookOrderFromServer(order);
}

export function schedulePushUserUiState() {
  if (typeof window === 'undefined') return;
  const token = localStorage.getItem('wl_token');
  if (!token || !localStorage.getItem('wl_user_id')) return;
  if (debTimer) clearTimeout(debTimer);
  debTimer = setTimeout(async () => {
    debTimer = null;
    const t = localStorage.getItem('wl_token');
    if (!t) return;
    await saveUserUiState(t, {
      layoutBySession: getAllLayoutPrefs() as Record<string, unknown>,
      bookshelfOrder: loadBookOrder(),
    });
  }, 800);
}
