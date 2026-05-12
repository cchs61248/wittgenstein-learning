export type Theme = 'light' | 'dark';

const STORAGE_KEY = 'wl_theme';
export const THEME_CHANGED_EVENT = 'wl-theme-changed';

export function getStoredTheme(): Theme {
  if (typeof window === 'undefined') return 'light';
  const raw = localStorage.getItem(STORAGE_KEY);
  return raw === 'dark' ? 'dark' : 'light';
}

export function applyTheme(theme: Theme) {
  if (typeof document === 'undefined') return;
  if (theme === 'dark') {
    document.documentElement.setAttribute('data-theme', 'dark');
  } else {
    document.documentElement.removeAttribute('data-theme');
  }
}

export function setTheme(theme: Theme) {
  if (typeof window === 'undefined') return;
  localStorage.setItem(STORAGE_KEY, theme);
  applyTheme(theme);
  window.dispatchEvent(new CustomEvent<Theme>(THEME_CHANGED_EVENT, { detail: theme }));
}

export function toggleTheme(): Theme {
  const next: Theme = getStoredTheme() === 'dark' ? 'light' : 'dark';
  setTheme(next);
  return next;
}
