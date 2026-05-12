import { useEffect, useState } from 'react';
import { getStoredTheme, toggleTheme, THEME_CHANGED_EVENT, type Theme } from '../utils/theme';

export function ThemeToggle() {
  const [theme, setLocalTheme] = useState<Theme>(() => getStoredTheme());

  useEffect(() => {
    const handler = (e: Event) => {
      const next = (e as CustomEvent<Theme>).detail;
      if (next === 'light' || next === 'dark') setLocalTheme(next);
    };
    window.addEventListener(THEME_CHANGED_EVENT, handler);
    return () => window.removeEventListener(THEME_CHANGED_EVENT, handler);
  }, []);

  const isDark = theme === 'dark';
  const label = isDark ? '切換為淺色模式' : '切換為深色模式';

  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={() => setLocalTheme(toggleTheme())}
      aria-label={label}
      title={label}
    >
      {isDark ? (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M21 12.79A9 9 0 1 1 11.21 3a7 7 0 0 0 9.79 9.79z" />
        </svg>
      )}
    </button>
  );
}
