/** 每個 session（教材）獨立的版面狀態；依帳號（wl_user_id）分開存在 localStorage，登出再登入仍保留 */

export const LEGACY_SESSION_LAYOUT_PREFS_KEY = 'wl_session_layout_prefs_v1';

export type SessionLayoutPrefs = {
  askTutorCollapsed?: boolean;
  questionCollapsed?: boolean;
  learnScrollTop?: number;
  /** 學習成效分頁 .stats-page 捲動 */
  statsScrollTop?: number;
  /** 書櫃：點入單一教材後的章節列表（map）或書櫃列表（list） */
  bookshelfPanelView?: 'list' | 'map';
  /** 書櫃章節列表區 .bookshelf-map-body 捲動 */
  bookshelfMapScrollTop?: number;
  stageSidebarCollapsed?: boolean;
  activePage?: 'learn' | 'stats';
  selectedStageId?: number | null;
};

type PrefsMap = Record<string, SessionLayoutPrefs>;

function storageUserId(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('wl_user_id');
}

function prefsKey(userId: string): string {
  return `wl_session_layout_prefs_acc_${userId}`;
}

function loadMap(): PrefsMap {
  const uid = storageUserId();
  if (!uid) return {};
  const key = prefsKey(uid);
  try {
    let raw = localStorage.getItem(key);
    if (!raw) {
      const legacy = localStorage.getItem(LEGACY_SESSION_LAYOUT_PREFS_KEY);
      if (legacy) {
        localStorage.setItem(key, legacy);
        localStorage.removeItem(LEGACY_SESSION_LAYOUT_PREFS_KEY);
        raw = localStorage.getItem(key);
      }
    }
    if (!raw) return {};
    const o = JSON.parse(raw) as unknown;
    return o && typeof o === 'object' ? (o as PrefsMap) : {};
  } catch {
    return {};
  }
}

function saveMap(m: PrefsMap, skipCloudPush = false) {
  const uid = storageUserId();
  if (!uid) return;
  try {
    localStorage.setItem(prefsKey(uid), JSON.stringify(m));
  } catch {
    /* ignore quota */
  }
  if (!skipCloudPush && typeof window !== 'undefined') {
    void import('./userUiStateSync').then((mod) => mod.schedulePushUserUiState());
  }
}

export function getAllLayoutPrefs(): PrefsMap {
  return { ...loadMap() };
}

/** 與伺服器同步：同 session_id 以伺服器為準，其餘保留本機。 */
export function mergeLayoutPrefsFromServer(server: Record<string, SessionLayoutPrefs>) {
  const uid = storageUserId();
  if (!uid) return;
  const local = loadMap();
  const merged: PrefsMap = { ...local };
  for (const [k, v] of Object.entries(server)) {
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      merged[k] = v as SessionLayoutPrefs;
    }
  }
  saveMap(merged, true);
}

export function getSessionLayoutPrefs(sessionId: string | null): Partial<SessionLayoutPrefs> | undefined {
  if (!sessionId) return undefined;
  return loadMap()[sessionId];
}

export function patchSessionLayoutPrefs(sessionId: string, patch: Partial<SessionLayoutPrefs>) {
  if (!sessionId || !storageUserId()) return;
  const map = loadMap();
  const prev = map[sessionId] ?? {};
  const clean = Object.fromEntries(
    Object.entries(patch).filter(([, v]) => v !== undefined)
  ) as Partial<SessionLayoutPrefs>;
  map[sessionId] = { ...prev, ...clean };
  saveMap(map);
}

export function removeSessionLayoutPrefs(sessionId: string) {
  if (!storageUserId()) return;
  const map = loadMap();
  if (!(sessionId in map)) return;
  delete map[sessionId];
  saveMap(map);
}

/** 首屏用：在 React 掛載前從 wl_user_id + wl_session_id + prefs 讀回分頁／收合 */
export function readInitialChromeFromStorage(): {
  askTutorCollapsed: boolean;
  questionCollapsed: boolean;
  stageSidebarCollapsed: boolean;
  activePage: 'learn' | 'stats';
} {
  if (typeof window === 'undefined') {
    return {
      askTutorCollapsed: false,
      questionCollapsed: false,
      stageSidebarCollapsed: false,
      activePage: 'learn',
    };
  }
  const uid = storageUserId();
  const sid = localStorage.getItem('wl_session_id');
  if (!uid || !sid) {
    return {
      askTutorCollapsed: false,
      questionCollapsed: false,
      stageSidebarCollapsed: window.matchMedia('(max-width: 768px)').matches,
      activePage: 'learn',
    };
  }
  const p = getSessionLayoutPrefs(sid);
  return {
    askTutorCollapsed: p?.askTutorCollapsed ?? false,
    questionCollapsed: p?.questionCollapsed ?? false,
    stageSidebarCollapsed:
      p?.stageSidebarCollapsed !== undefined
        ? p.stageSidebarCollapsed
        : window.matchMedia('(max-width: 768px)').matches,
    activePage: p?.activePage === 'stats' ? 'stats' : 'learn',
  };
}
