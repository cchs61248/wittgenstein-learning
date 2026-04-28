import { useState } from 'react';
import { register, login } from '../api/auth';
import { useSessionStore } from '../store/sessionStore';

function AuthHeroIllustration() {
  return (
    <svg
      className="auth-hero-svg"
      viewBox="0 0 400 280"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden
    >
      <defs>
        <linearGradient id="auth-sun" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#fde047" />
          <stop offset="100%" stopColor="#fbbf24" />
        </linearGradient>
        <linearGradient id="auth-hill" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="#fef3c7" />
          <stop offset="100%" stopColor="#fde68a" />
        </linearGradient>
      </defs>
      <circle cx="318" cy="58" r="34" fill="url(#auth-sun)" opacity="0.95" />
      <path
        d="M0 200 Q120 150 220 175 T400 165 V280 H0Z"
        fill="url(#auth-hill)"
        opacity="0.9"
      />
      <path
        d="M0 230 Q100 200 200 215 T400 205 V280 H0Z"
        fill="#fcd34d"
        opacity="0.5"
      />
      <g transform="translate(48 120)">
        <rect x="0" y="20" width="88" height="12" rx="4" fill="#ea580c" opacity="0.85" />
        <rect x="8" y="0" width="72" height="88" rx="6" fill="#fffbeb" stroke="#b45309" strokeWidth="2.5" />
        <line x1="20" y1="22" x2="68" y2="22" stroke="#fde68a" strokeWidth="3" strokeLinecap="round" />
        <line x1="20" y1="38" x2="62" y2="38" stroke="#fef3c7" strokeWidth="3" strokeLinecap="round" />
        <line x1="20" y1="54" x2="58" y2="54" stroke="#fef3c7" strokeWidth="3" strokeLinecap="round" />
      </g>
      <g transform="translate(200 72)">
        <path
          d="M8 16 Q90 -4 172 16 L172 76 Q90 96 8 76Z"
          fill="#ffffff"
          stroke="#d97706"
          strokeWidth="2.5"
        />
        <circle cx="52" cy="44" r="6" fill="#f59e0b" />
        <circle cx="90" cy="44" r="6" fill="#f59e0b" />
        <circle cx="128" cy="44" r="6" fill="#f59e0b" />
      </g>
      <g transform="translate(248 148)">
        <rect x="0" y="12" width="120" height="64" rx="12" fill="#fff7ed" stroke="#ea580c" strokeWidth="2" />
        <path d="M24 12 L24 0 L44 12Z" fill="#fff7ed" stroke="#ea580c" strokeWidth="2" />
        <line x1="20" y1="36" x2="100" y2="36" stroke="#fdba74" strokeWidth="3" strokeLinecap="round" />
        <line x1="20" y1="52" x2="76" y2="52" stroke="#ffedd5" strokeWidth="3" strokeLinecap="round" />
      </g>
      <g fill="#f59e0b" opacity="0.9">
        <path d="M32 48l4 8 8 2-8 4-4 8-4-8-8-4 8-2z" />
        <path d="M360 120l3 6 6 1.5-6 3-3 6-3-6-6-3 6-1.5z" />
        <path d="M140 32l2.5 5 5 1.2-5 2.5-2.5 5-2.5-5-5-2.5 5-1.2z" />
      </g>
    </svg>
  );
}

function IconSparkles() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M12 3v2M12 19v2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M3 12h2M19 12h2M5.6 18.4l1.4-1.4M17 7l1.4-1.4" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function IconChat() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
    </svg>
  );
}

function IconBooks() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
      <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
      <path d="M8 7h8M8 11h6" />
    </svg>
  );
}

export function AuthForm() {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const setAuth = useSessionStore((s) => s.setAuth);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const fn = mode === 'login' ? login : register;
      const res = await fn(email, password);
      setAuth(res.access_token, res.user_id, res.email);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '發生錯誤');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-container">
      <span className="auth-blob auth-blob--1" aria-hidden />
      <span className="auth-blob auth-blob--2" aria-hidden />
      <span className="auth-blob auth-blob--3" aria-hidden />

      <div className="auth-grid">
        <div className="auth-hero">
          <div className="auth-hero-art">
            <AuthHeroIllustration />
          </div>
          <p className="auth-hero-tagline">一起用問答，把知識變成真正屬於你的理解。</p>
          <ul className="auth-perk-list">
            <li>
              <span className="auth-perk-icon">
                <IconChat />
              </span>
              蘇格拉底式對話，慢慢想清楚
            </li>
            <li>
              <span className="auth-perk-icon">
                <IconBooks />
              </span>
              依你的材料分段，穩穩前進
            </li>
            <li>
              <span className="auth-perk-icon">
                <IconSparkles />
              </span>
              即時回饋，越答越有把握
            </li>
          </ul>
        </div>

        <div className="auth-card">
          <div className="auth-brand">
            <span className="brand-mark auth-brand-mark" aria-hidden="true" />
            <h1>維特根斯坦學習系統</h1>
          </div>
          <p className="auth-subtitle">透過蘇格拉底式問答，確保真正理解每個概念</p>

          <div className="auth-tabs">
            <button
              type="button"
              className={mode === 'login' ? 'active' : ''}
              onClick={() => setMode('login')}
            >
              登入
            </button>
            <button
              type="button"
              className={mode === 'register' ? 'active' : ''}
              onClick={() => setMode('register')}
            >
              註冊
            </button>
          </div>

          <form onSubmit={handleSubmit}>
            <input
              type="email"
              placeholder="Email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
            <input
              type="password"
              placeholder="密碼"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={6}
            />
            {error && <p className="error-msg">{error}</p>}
            <button type="submit" disabled={loading} className="btn-primary">
              {loading ? '請稍候...' : mode === 'login' ? '登入' : '註冊'}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
