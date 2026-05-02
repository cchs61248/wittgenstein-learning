/** 每個 session（教材）獨立的版面狀態，存在 localStorage */

export const SESSION_LAYOUT_PREFS_KEY = 'wl_session_layout_prefs_v1';

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

function loadMap(): PrefsMap {
  try {
    const raw = localStorage.getItem(SESSION_LAYOUT_PREFS_KEY);
    if (!raw) return {};
    const o = JSON.parse(raw) as unknown;
    return o && typeof o === 'object' ? (o as PrefsMap) : {};
  } catch {
    return {};
  }
}

function saveMap(m: PrefsMap) {
  try {
    localStorage.setItem(SESSION_LAYOUT_PREFS_KEY, JSON.stringify(m));
  } catch {
    /* ignore quota */
  }
}

export function getSessionLayoutPrefs(sessionId: string | null): Partial<SessionLayoutPrefs> | undefined {
  if (!sessionId) return undefined;
  return loadMap()[sessionId];
}

export function patchSessionLayoutPrefs(sessionId: string, patch: Partial<SessionLayoutPrefs>) {
  if (!sessionId) return;
  const map = loadMap();
  const prev = map[sessionId] ?? {};
  const clean = Object.fromEntries(
    Object.entries(patch).filter(([, v]) => v !== undefined)
  ) as Partial<SessionLayoutPrefs>;
  map[sessionId] = { ...prev, ...clean };
  saveMap(map);
}

export function removeSessionLayoutPrefs(sessionId: string) {
  const map = loadMap();
  if (!(sessionId in map)) return;
  delete map[sessionId];
  saveMap(map);
}

export function clearAllSessionLayoutPrefs() {
  localStorage.removeItem(SESSION_LAYOUT_PREFS_KEY);
}

/** 首屏用：在 React 掛載前從 wl_session_id + prefs 讀回分頁／收合，避免重整後被預設值覆蓋 */
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
  const sid = localStorage.getItem('wl_session_id');
  if (!sid) {
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
