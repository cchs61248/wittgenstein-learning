import { useState, useEffect, useMemo, useReducer, forwardRef, useCallback, useRef, type MutableRefObject } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, PieChart, Pie, Cell,
} from 'recharts';
import { useSessionStore } from '../store/sessionStore';
import { fetchLearnerStats, type LearnerStats } from '../api/learner';
import { getSessionLayoutPrefs, patchSessionLayoutPrefs } from '../utils/sessionLayoutPrefs';
import { THEME_CHANGED_EVENT } from '../utils/theme';

// ── Design tokens：runtime 從 CSS 變數讀，主題切換時同步更新 ────────────────
const COLOR_VARS = {
  green:       '--green',
  greenBg:     '--green-bg',
  greenBorder: '--green-border',
  accent:      '--accent',
  accentBg:    '--accent-bg',
  yellow:      '--yellow',
  yellowBg:    '--yellow-bg',
  red:         '--red',
  redBg:       '--red-bg',
  redBorder:   '--red-border',
  border:      '--border',
  gridLine:    '--yellow-border',
  bg:          '--bg',
  bgCard:      '--bg-card',
  text:        '--text',
  textMuted:   '--text-muted',
  textSubtle:  '--text-subtle',
} as const;

type ColorKey = keyof typeof COLOR_VARS;
type ColorMap = Record<ColorKey, string>;

const LIGHT_FALLBACK: ColorMap = {
  green:'#15803d', greenBg:'#dcfce7', greenBorder:'#86efac',
  accent:'#d97706', accentBg:'#ffedd5',
  yellow:'#b45309', yellowBg:'#fffbeb',
  red:'#b91c1c', redBg:'#fee2e2', redBorder:'#fecaca',
  border:'#fcd34d', gridLine:'#fde68a',
  bg:'#fffbeb', bgCard:'#ffffff',
  text:'#422006', textMuted:'#92400e', textSubtle:'#b45309',
};

function readColors(): ColorMap {
  if (typeof window === 'undefined') return { ...LIGHT_FALLBACK };
  const cs = getComputedStyle(document.documentElement);
  const out = {} as ColorMap;
  for (const k of Object.keys(COLOR_VARS) as ColorKey[]) {
    out[k] = cs.getPropertyValue(COLOR_VARS[k]).trim() || LIGHT_FALLBACK[k];
  }
  return out;
}

const C: ColorMap = readColors();
const DECISION_COLORS: Record<string, string> = {
  advance:   C.green,
  retry:     C.yellow,
  remediate: C.accent,
  reteach:   C.red,
};

function syncColors() {
  Object.assign(C, readColors());
  DECISION_COLORS.advance = C.green;
  DECISION_COLORS.retry = C.yellow;
  DECISION_COLORS.remediate = C.accent;
  DECISION_COLORS.reteach = C.red;
}

function useThemeColorSync() {
  const [, force] = useReducer((x: number) => x + 1, 0);
  useEffect(() => {
    syncColors();
    force();
    const onChange = () => { syncColors(); force(); };
    window.addEventListener(THEME_CHANGED_EVENT, onChange);
    return () => window.removeEventListener(THEME_CHANGED_EVENT, onChange);
  }, []);
}

const DECISION_LABELS: Record<string, string> = {
  advance: '通過', retry: '重試', remediate: '補強', reteach: '重教',
};

function masteryColor(score: number) {
  if (score >= 0.75) return C.green;
  if (score >= 0.5)  return C.accent;
  return C.red;
}

// ── Recharts Tooltip 元件 ──────────────────────────────────────────────────
function ScoreTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload: { label: string; score: number; decision: string; stageTitle: string } }> }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  const dColor = DECISION_COLORS[d.decision] ?? C.textMuted;
  return (
    <div style={{ background: C.bgCard, border: `1px solid ${C.border}`, borderRadius: 10, padding: '10px 14px', fontFamily: 'var(--font-ui)', fontSize: 13 }}>
      <div style={{ color: C.textSubtle, fontSize: 11, marginBottom: 3 }}>{d.stageTitle || d.label}</div>
      <div style={{ fontWeight: 700, color: C.text, fontSize: 15 }}>{d.score}%</div>
      <div style={{ marginTop: 5 }}>
        <span style={{ background: dColor + '22', border: `1px solid ${dColor}`, color: dColor, borderRadius: 999, padding: '2px 8px', fontSize: 11, fontWeight: 700 }}>
          {DECISION_LABELS[d.decision] ?? d.decision}
        </span>
      </div>
    </div>
  );
}

// ── 子元件 ─────────────────────────────────────────────────────────────────
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <h2 style={{
        fontFamily: 'var(--font-display)', fontSize: '1.05rem', fontWeight: 700, color: C.text,
        paddingBottom: 8, borderBottom: `1px solid ${C.border}`, margin: 0,
      }}>{title}</h2>
      {children}
    </section>
  );
}

function EmptyNote({ text }: { text: string }) {
  return (
    <p style={{ textAlign: 'center', padding: '24px 0', color: C.textMuted, fontFamily: 'var(--font-ui)', fontSize: 14, margin: 0 }}>
      {text}
    </p>
  );
}

function ColorDot({ color }: { color: string }) {
  return <span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 2, background: color, flexShrink: 0 }} />;
}

// ── 主元件 ─────────────────────────────────────────────────────────────────
export const LearningStatsPage = forwardRef<HTMLDivElement, { token: string; sessionId: string | null }>(
  function LearningStatsPage({ token, sessionId }, ref) {
  useThemeColorSync();
  const { stages, stageQaHistories, decisionHistory } = useSessionStore();
  const [stats, setStats] = useState<LearnerStats | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [showEvidence, setShowEvidence] = useState(false);
  const [conceptPage, setConceptPage] = useState(1);
  const [misconceptionPage, setMisconceptionPage] = useState(1);
  const rootElRef = useRef<HTMLDivElement | null>(null);

  const setRootRef = useCallback(
    (node: HTMLDivElement | null) => {
      rootElRef.current = node;
      if (typeof ref === 'function') ref(node);
      else if (ref) (ref as MutableRefObject<HTMLDivElement | null>).current = node;
    },
    [ref]
  );

  useEffect(() => {
    fetchLearnerStats(token).then((data) => {
      setStats(data);
      setIsLoading(false);
    });
  }, [token]);

  useEffect(() => {
    if (!sessionId) return;
    const el = rootElRef.current;
    if (!el) return;
    const top = getSessionLayoutPrefs(sessionId)?.statsScrollTop ?? 0;
    const id = requestAnimationFrame(() => {
      if (rootElRef.current) rootElRef.current.scrollTop = top;
    });
    return () => cancelAnimationFrame(id);
  }, [sessionId, isLoading, stats, stages.length, decisionHistory.length]);

  useEffect(() => {
    if (!sessionId) return;
    const prefs = getSessionLayoutPrefs(sessionId);
    setShowEvidence(prefs?.statsEvidenceExpanded ?? false);
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) return;
    const el = rootElRef.current;
    if (!el) return;
    let tid: ReturnType<typeof setTimeout> | undefined;
    const onScroll = () => {
      clearTimeout(tid);
      tid = setTimeout(() => {
        patchSessionLayoutPrefs(sessionId, { statsScrollTop: el.scrollTop });
      }, 200);
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      el.removeEventListener('scroll', onScroll);
      clearTimeout(tid);
    };
  }, [sessionId, isLoading]);

  useEffect(() => {
    if (!sessionId) return;
    patchSessionLayoutPrefs(sessionId, { statsEvidenceExpanded: showEvidence });
  }, [sessionId, showEvidence]);

  // ── 派生資料 ─────────────────────────────────────────────────────────────
  const completedCount = stages.filter((s) => s.status === 'completed').length;
  const allQaItems = Object.values(stageQaHistories).flat();
  const avgScore =
    allQaItems.length > 0
      ? Math.round((allQaItems.reduce((s, i) => s + i.score, 0) / allQaItems.length) * 100)
      : null;

  // 答題分數趨勢
  const trendData = useMemo(() =>
    [...decisionHistory].map((d, i) => ({
      label: `#${i + 1}`,
      score: Math.round(d.bestScore * 100),
      decision: d.decision,
      stageTitle: d.stageTitle,
    })),
    [decisionHistory]
  );

  // 決策分布
  const decisionCounts = useMemo(() => {
    const counts: Record<string, number> = { advance: 0, retry: 0, remediate: 0, reteach: 0 };
    decisionHistory.forEach((d) => { if (d.decision in counts) counts[d.decision]++; });
    return counts;
  }, [decisionHistory]);

  const donutData = (Object.entries(decisionCounts) as [string, number][])
    .filter(([, v]) => v > 0)
    .map(([k, v]) => ({ name: DECISION_LABELS[k] ?? k, value: v, key: k }));

  const totalDecisions = decisionHistory.length;

  // 概念掌握度（由低到高）
  const conceptData = useMemo(() =>
    [...(stats?.concepts ?? [])]
      .sort((a, b) => a.mastery_score - b.mastery_score)
      .map((c) => ({
        concept: c.concept_name.length > 16 ? c.concept_name.slice(0, 14) + '…' : c.concept_name,
        fullConcept: c.concept_name,
        value: Math.round(c.mastery_score * 100),
        exposures: c.total_exposures,
        color: masteryColor(c.mastery_score),
      })),
    [stats]
  );

  const pagedConcepts = useMemo(() => {
    const pageSize = 20;
    const totalPages = Math.max(1, Math.ceil(conceptData.length / pageSize));
    const safePage = Math.min(conceptPage, totalPages);
    const start = (safePage - 1) * pageSize;
    return {
      items: conceptData.slice(start, start + pageSize),
      total: conceptData.length,
      pageSize,
      page: safePage,
      totalPages,
    };
  }, [conceptData, conceptPage]);

  // 各節點最高分
  const stageBarData = useMemo(() =>
    stages
      .filter((s) => (stageQaHistories[s.stage_id]?.length ?? 0) > 0)
      .map((s) => {
        const items = stageQaHistories[s.stage_id] ?? [];
        const best = items.length > 0 ? Math.max(...items.map((i) => i.score)) : 0;
        return {
          title: s.title,
          value: Math.round(best * 100),
          answers: items.length,
          color: masteryColor(best),
        };
      }),
    [stages, stageQaHistories]
  );

  const strongestCount = useMemo(
    () => (stats?.concepts ?? []).filter((c) => c.mastery_score >= 0.75).length,
    [stats]
  );

  const weakestConcept = useMemo(
    () =>
      [...(stats?.concepts ?? [])]
        .sort((a, b) => a.mastery_score - b.mastery_score)[0] ?? null,
    [stats]
  );

  const topMisconception = useMemo(() => {
    if (!stats?.misconceptions?.length) return null;
    const ranked = [...stats.misconceptions].sort((a, b) => {
      const rank = { high: 0, medium: 1, low: 2 };
      return rank[a.severity] - rank[b.severity];
    });
    return ranked[0] ?? null;
  }, [stats]);

  const statusCard = useMemo(() => {
    const weakCount = stats?.weak_count ?? 0;
    if (weakCount <= 1 && (avgScore ?? 0) >= 75) {
      return {
        title: '穩定前進中',
        desc: '你正在用對的方法前進，維持節奏就會越來越穩。',
        color: C.green,
        bg: C.greenBg,
      };
    }
    if (weakCount <= 3 && (avgScore ?? 0) >= 50) {
      return {
        title: '有基礎，補一點就更順',
        desc: '你已經有基礎了，補上 1 個關鍵點就會明顯變順。',
        color: C.accent,
        bg: C.accentBg,
      };
    }
    return {
      title: '建議先回顧再衝刺',
      desc: '先把核心概念補齊，接下來會更有掌控感。',
      color: C.red,
      bg: C.redBg,
    };
  }, [avgScore, stats?.weak_count]);

  const nextAction = useMemo(() => {
    if (topMisconception) {
      return {
        title: `補強「${topMisconception.concept_name}」`,
        detail: topMisconception.repair_strategy || `先釐清：${topMisconception.pattern}`,
        eta: '約 15 分鐘',
      };
    }
    if (weakestConcept) {
      return {
        title: `回顧「${weakestConcept.concept_name}」`,
        detail: '先看講解摘要，再做 2 題短答確認理解。',
        eta: '約 12 分鐘',
      };
    }
    return {
      title: '進行下一章學習',
      detail: '維持節奏，挑戰下一個節點。',
      eta: '約 10 分鐘',
    };
  }, [topMisconception, weakestConcept]);

  const topMisconceptionBadge = useMemo(() => {
    if (!topMisconception) return null;
    if (topMisconception.severity === 'high') return { label: '高影響', color: C.red, border: C.redBorder, bg: C.redBg };
    if (topMisconception.severity === 'medium') return { label: '中影響', color: C.yellow, border: '#fde68a', bg: C.yellowBg };
    return { label: '低影響', color: C.accent, border: '#fdba74', bg: C.accentBg };
  }, [topMisconception]);

  const pagedMisconceptions = useMemo(() => {
    const all = [...(stats?.misconceptions ?? [])];
    const rank: Record<'high' | 'medium' | 'low', number> = { high: 0, medium: 1, low: 2 };
    all.sort((a, b) => rank[a.severity] - rank[b.severity]);
    const pageSize = 20;
    const totalPages = Math.max(1, Math.ceil(all.length / pageSize));
    const safePage = Math.min(misconceptionPage, totalPages);
    const start = (safePage - 1) * pageSize;
    return {
      items: all.slice(start, start + pageSize),
      total: all.length,
      pageSize,
      page: safePage,
      totalPages,
    };
  }, [stats?.misconceptions, misconceptionPage]);

  useEffect(() => {
    setMisconceptionPage(1);
  }, [stats?.misconceptions]);

  useEffect(() => {
    setConceptPage(1);
  }, [stats?.concepts]);

  // 空狀態
  const hasAnyData = stages.length > 0 || (stats && stats.concepts.length > 0) || decisionHistory.length > 0;
  if (!isLoading && !hasAnyData) {
    return (
      <div ref={setRootRef} className="stats-page">
        <div className="stats-empty-guide">
          <p>上傳學習材料並開始學習後，這裡會顯示你的概念掌握度、答題成效與決策記錄。</p>
        </div>
      </div>
    );
  }

  return (
    <div ref={setRootRef} className="stats-page">
      <section
        style={{
          background: C.bgCard,
          border: `1px solid ${C.border}`,
          borderRadius: 16,
          padding: '20px 22px',
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
        }}
      >
        <div>
          <h2 style={{ margin: 0, fontFamily: 'var(--font-display)', fontSize: '1.2rem', color: C.text }}>
            你正在累積實力，今天把一個關鍵點補好就很棒
          </h2>
          <p style={{ margin: '6px 0 0', color: C.textMuted, fontFamily: 'var(--font-body)', fontSize: 14 }}>
            先看你做得好的地方，再聚焦今天最有回報的一步。
          </p>
        </div>

        <div className="stats-overview-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
          {[
            { label: '已掌握概念', value: `${strongestCount} 個`, sub: '熟悉度達 75% 以上', color: C.green },
            { label: '已完成階段', value: `${completedCount}/${stages.length}`, sub: '學習節點', color: C.text },
            { label: '平均作答', value: avgScore !== null ? `${avgScore}%` : '—', sub: '目前課程', color: avgScore !== null ? masteryColor(avgScore / 100) : C.text },
          ].map((item) => (
            <div
              key={item.label}
              style={{
                background: C.bg,
                border: `1px solid ${C.border}`,
                borderRadius: 12,
                padding: '14px 14px',
                display: 'flex',
                flexDirection: 'column',
                gap: 4,
              }}
            >
              <span style={{ fontFamily: 'var(--font-ui)', fontSize: 11, fontWeight: 700, color: C.textSubtle }}>{item.label}</span>
              <span style={{ fontFamily: 'var(--font-ui)', fontSize: '1.6rem', fontWeight: 800, color: item.color, lineHeight: 1.1 }}>{item.value}</span>
              <span style={{ fontFamily: 'var(--font-ui)', fontSize: 12, color: C.textMuted }}>{item.sub}</span>
            </div>
          ))}
        </div>

        <div style={{ background: statusCard.bg, border: `1px solid ${statusCard.color}55`, borderRadius: 12, padding: '12px 14px' }}>
          <div style={{ fontFamily: 'var(--font-ui)', color: statusCard.color, fontWeight: 800, fontSize: 14 }}>{statusCard.title}</div>
          <div style={{ fontFamily: 'var(--font-body)', color: C.text, fontSize: 14, marginTop: 4 }}>{statusCard.desc}</div>
        </div>

        <div style={{ border: `1px solid ${C.border}`, borderRadius: 12, padding: '12px 14px', display: 'grid', gap: 8 }}>
          <div style={{ fontFamily: 'var(--font-ui)', fontSize: 11, fontWeight: 700, color: C.textSubtle }}>今天建議任務</div>
          <div style={{ fontFamily: 'var(--font-body)', fontSize: 15, color: C.text, fontWeight: 700 }}>{nextAction.title}</div>
          <div style={{ fontFamily: 'var(--font-body)', fontSize: 14, color: C.textMuted }}>{nextAction.detail}</div>
          <div style={{ fontFamily: 'var(--font-ui)', fontSize: 12, color: C.textSubtle }}>{nextAction.eta}</div>
        </div>

        <div style={{ background: C.bg, border: `1px solid ${C.border}`, borderRadius: 12, padding: '12px 14px' }}>
          <div style={{ fontFamily: 'var(--font-ui)', fontSize: 11, fontWeight: 700, color: C.textSubtle, marginBottom: 6 }}>弱點摘要</div>
          {topMisconceptionBadge && (
            <span
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                marginBottom: 8,
                padding: '2px 8px',
                borderRadius: 999,
                border: `1px solid ${topMisconceptionBadge.border}`,
                background: topMisconceptionBadge.bg,
                color: topMisconceptionBadge.color,
                fontFamily: 'var(--font-ui)',
                fontSize: 11,
                fontWeight: 700,
              }}
            >
              {topMisconceptionBadge.label}
            </span>
          )}
          <div style={{ fontFamily: 'var(--font-body)', fontSize: 14, color: C.text }}>
            {topMisconception
              ? `${topMisconception.concept_name}：目前容易在「${topMisconception.pattern}」卡住。`
              : weakestConcept
              ? `${weakestConcept.concept_name} 熟悉度較低，建議優先回顧。`
              : '目前沒有明顯弱點，維持節奏即可。'}
          </div>
        </div>

        <button
          type="button"
          onClick={() => setShowEvidence((v) => !v)}
          style={{
            alignSelf: 'flex-start',
            minHeight: 44,
            padding: '0 14px',
            borderRadius: 999,
            border: `1px solid ${C.border}`,
            background: C.bgCard,
            color: C.text,
            fontFamily: 'var(--font-ui)',
            fontSize: 13,
            fontWeight: 700,
            cursor: 'pointer',
          }}
        >
          {showEvidence ? '收合詳細依據' : '展開詳細依據'}
        </button>
      </section>

      {showEvidence && (
        <>
      {/* ── Section 1：四格總覽 ──────────────────────────────────────────── */}
      <div className="stats-overview-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
        {([
          {
            label: '已完成階段',
            value: `${completedCount}/${stages.length}`,
            sub: '個學習節點',
          },
          {
            label: '平均答題分數',
            value: avgScore !== null ? `${avgScore}%` : '—',
            sub: '本課程',
            valueColor: avgScore !== null ? masteryColor(avgScore / 100) : undefined,
          },
          {
            label: '待補強概念',
            value: String(stats?.weak_count ?? '—'),
            sub: '掌握度未達 60%',
            valueColor: (stats?.weak_count ?? 0) > 0 ? C.red : C.green,
          },
          {
            label: '已回答題數',
            value: String(allQaItems.length),
            sub: '道題目',
          },
        ] as { label: string; value: string; sub: string; valueColor?: string }[]).map(({ label, value, sub, valueColor }) => (
          <div key={label} style={{
            background: C.bgCard, border: `1px solid ${C.border}`, borderRadius: 14, padding: '18px 20px',
            display: 'flex', flexDirection: 'column', gap: 5,
          }}>
            <span style={{ fontFamily: 'var(--font-ui)', fontSize: 11, fontWeight: 700, color: C.textSubtle, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</span>
            <span style={{ fontFamily: 'var(--font-ui)', fontSize: '2rem', fontWeight: 800, color: valueColor ?? C.text, lineHeight: 1, letterSpacing: '-0.03em' }}>{value}</span>
            <span style={{ fontFamily: 'var(--font-ui)', fontSize: 12, color: C.textMuted, fontWeight: 600 }}>{sub}</span>
          </div>
        ))}
      </div>

      {/* ── Section 2：答題分數趨勢 ─────────────────────────────────────── */}
      {trendData.length > 0 && (
        <Section title="答題分數趨勢">
          <div style={{ height: 210 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={trendData} margin={{ top: 8, right: 8, left: -22, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={C.gridLine} />
                <XAxis
                  dataKey="label"
                  tick={{ fontSize: 11, fill: C.textSubtle, fontFamily: 'var(--font-ui)' }}
                  axisLine={false} tickLine={false}
                />
                <YAxis
                  domain={[0, 100]} unit="%"
                  tick={{ fontSize: 11, fill: C.textSubtle, fontFamily: 'var(--font-ui)' }}
                  axisLine={false} tickLine={false}
                />
                <Tooltip content={<ScoreTooltip />} />
                <Line
                  type="monotone"
                  dataKey="score"
                  stroke={C.border}
                  strokeWidth={2}
                  dot={(props: { cx?: number; cy?: number; payload?: { decision: string }; index?: number }) => {
                    const { cx = 0, cy = 0, payload, index = 0 } = props;
                    const decision = payload?.decision ?? '';
                    const color = DECISION_COLORS[decision] ?? C.textMuted;
                    return (
                      <circle
                        key={`dot-${index}`}
                        cx={cx} cy={cy} r={5}
                        fill={color} stroke={C.bgCard} strokeWidth={2}
                      />
                    );
                  }}
                  activeDot={{ r: 7, stroke: C.bgCard, strokeWidth: 2, fill: C.accent }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
          {/* 圖例 */}
          <div style={{ display: 'flex', gap: '8px 18px', justifyContent: 'center', flexWrap: 'wrap', fontFamily: 'var(--font-ui)', fontSize: 12 }}>
            {Object.entries(DECISION_LABELS).map(([key, label]) => (
              <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <span style={{ width: 10, height: 10, borderRadius: '50%', background: DECISION_COLORS[key], flexShrink: 0 }} />
                <span style={{ color: C.textMuted }}>{label}</span>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* ── Section 3：決策分布 + 各節點表現（雙欄） ──────────────────── */}
      {(donutData.length > 0 || stageBarData.length > 0) && (
        <div className="stats-dual-col" style={{ display: 'grid', gridTemplateColumns: donutData.length > 0 && stageBarData.length > 0 ? '1fr 1fr' : '1fr', gap: 28 }}>

          {/* 決策分布甜甜圈 */}
          {donutData.length > 0 && (
            <Section title="決策分布">
              <div style={{ position: 'relative', height: 200 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={donutData}
                      cx="50%" cy="50%"
                      innerRadius={52} outerRadius={80}
                      paddingAngle={3}
                      dataKey="value"
                      startAngle={90} endAngle={-270}
                    >
                      {donutData.map((entry) => (
                        <Cell key={entry.key} fill={DECISION_COLORS[entry.key] ?? C.accent} />
                      ))}
                    </Pie>
                  </PieChart>
                </ResponsiveContainer>
                {/* 中心文字（CSS 疊加） */}
                <div style={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)', textAlign: 'center', pointerEvents: 'none' }}>
                  <div style={{ fontFamily: 'var(--font-ui)', fontSize: 22, fontWeight: 800, color: C.text, lineHeight: 1 }}>{totalDecisions}</div>
                  <div style={{ fontFamily: 'var(--font-ui)', fontSize: 11, color: C.textMuted, marginTop: 2 }}>筆決策</div>
                </div>
              </div>
              {/* 圖例 */}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 14px', justifyContent: 'center', fontFamily: 'var(--font-ui)' }}>
                {Object.entries(DECISION_LABELS).map(([key, label]) => {
                  const n = decisionCounts[key] ?? 0;
                  if (!n) return null;
                  const color = DECISION_COLORS[key];
                  return (
                    <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12 }}>
                      <ColorDot color={color} />
                      <span style={{ color: C.textMuted, fontWeight: 600 }}>{label}</span>
                      <span style={{ color: C.text, fontWeight: 800 }}>{n}</span>
                    </div>
                  );
                })}
              </div>
              <p style={{ textAlign: 'center', fontSize: 12, color: C.textSubtle, margin: 0, fontFamily: 'var(--font-ui)' }}>
                通過率 {totalDecisions > 0 ? Math.round((decisionCounts.advance / totalDecisions) * 100) : 0}%
              </p>
            </Section>
          )}

          {/* 各節點最高分橫條圖 */}
          {stageBarData.length > 0 && (
            <Section title="各節點最高分">
              <div
                style={{
                  height: 'clamp(320px, 50vh, 560px)',
                  overflowY: 'auto',
                  border: `1px solid ${C.border}`,
                  borderRadius: 12,
                  padding: '8px',
                  background: C.bgCard,
                }}
              >
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {stageBarData.map((entry, i) => (
                    <div
                      key={`${entry.title}-${i}`}
                      style={{
                        border: `1px solid ${C.border}`,
                        borderRadius: 10,
                        background: C.bg,
                        padding: '10px 12px',
                        display: 'grid',
                        gridTemplateColumns: '1fr auto',
                        gap: 10,
                        alignItems: 'center',
                      }}
                    >
                      <div style={{ minWidth: 0 }}>
                        <div
                          title={entry.title}
                          style={{
                            fontFamily: 'var(--font-body)',
                            fontSize: 14,
                            fontWeight: 700,
                            color: C.text,
                            lineHeight: 1.35,
                            wordBreak: 'break-word',
                          }}
                        >
                          {entry.title}
                        </div>
                        <div
                          style={{
                            marginTop: 8,
                            height: 10,
                            borderRadius: 999,
                            overflow: 'hidden',
                            background: '#fef3c7',
                            border: `1px solid ${C.border}`,
                          }}
                        >
                          <div
                            style={{
                              width: `${entry.value}%`,
                              height: '100%',
                              background: entry.color,
                              borderRadius: 999,
                            }}
                          />
                        </div>
                      </div>
                      <div style={{ textAlign: 'right', minWidth: 64 }}>
                        <div style={{ fontFamily: 'var(--font-ui)', fontSize: 15, fontWeight: 800, color: entry.color }}>
                          {entry.value}%
                        </div>
                        <div style={{ fontFamily: 'var(--font-ui)', fontSize: 11, color: C.textSubtle }}>
                          {entry.answers} 題
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
              <div style={{ display: 'flex', gap: '6px 14px', justifyContent: 'center', flexWrap: 'wrap', fontFamily: 'var(--font-ui)', fontSize: 11, color: C.textSubtle }}>
                {[
                  { color: C.green,  label: '精熟 ≥75%' },
                  { color: C.accent, label: '學習中 50–74%' },
                  { color: C.red,    label: '需加強 <50%' },
                ].map(({ color, label }) => (
                  <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                    <ColorDot color={color} />
                    <span>{label}</span>
                  </div>
                ))}
              </div>
            </Section>
          )}
        </div>
      )}

      {/* ── Section 4：概念掌握度 ────────────────────────────────────────── */}
      <Section title={`概念掌握度${stats ? ` · ${stats.concepts.length} 個概念` : ''}`}>
        {isLoading ? (
          <EmptyNote text="載入中…" />
        ) : !stats || stats.concepts.length === 0 ? (
          <EmptyNote text="尚無概念掌握度記錄" />
        ) : (
          <>
            <p style={{ margin: 0, fontFamily: 'var(--font-ui)', fontSize: 12, color: C.textMuted }}>
              第 {pagedConcepts.page} / {pagedConcepts.totalPages} 頁 · 每頁 20 筆 · 共 {pagedConcepts.total} 個概念
            </p>
            <div
              style={{
                height: 'clamp(420px, 58vh, 640px)',
                overflowY: 'auto',
                border: `1px solid ${C.border}`,
                borderRadius: 12,
                padding: '8px 12px',
                background: C.bgCard,
              }}
            >
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {pagedConcepts.items.map((entry) => (
                  <div
                    key={entry.fullConcept}
                    style={{
                      border: `1px solid ${C.border}`,
                      borderRadius: 10,
                      background: C.bg,
                      padding: '10px 12px',
                      display: 'grid',
                      gridTemplateColumns: '1fr auto',
                      gap: 10,
                      alignItems: 'center',
                    }}
                  >
                    <div style={{ minWidth: 0 }}>
                      <div
                        title={entry.fullConcept}
                        style={{
                          fontFamily: 'var(--font-body)',
                          fontSize: 14,
                          fontWeight: 700,
                          color: C.text,
                          lineHeight: 1.35,
                          wordBreak: 'break-word',
                        }}
                      >
                        {entry.fullConcept}
                      </div>
                      <div
                        style={{
                          marginTop: 8,
                          height: 10,
                          borderRadius: 999,
                          overflow: 'hidden',
                          background: '#fef3c7',
                          border: `1px solid ${C.border}`,
                        }}
                      >
                        <div
                          style={{
                            width: `${entry.value}%`,
                            height: '100%',
                            background: entry.color,
                            borderRadius: 999,
                          }}
                        />
                      </div>
                    </div>
                    <div style={{ textAlign: 'right', minWidth: 64 }}>
                      <div style={{ fontFamily: 'var(--font-ui)', fontSize: 15, fontWeight: 800, color: entry.color }}>
                        {entry.value}%
                      </div>
                      <div style={{ fontFamily: 'var(--font-ui)', fontSize: 11, color: C.textSubtle }}>
                        接觸 {entry.exposures} 次
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
            {pagedConcepts.totalPages > 1 && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'center' }}>
                <button
                  type="button"
                  onClick={() => setConceptPage((p) => Math.max(1, p - 1))}
                  disabled={pagedConcepts.page === 1}
                  style={{
                    minHeight: 36,
                    padding: '0 12px',
                    borderRadius: 999,
                    border: `1px solid ${C.border}`,
                    background: C.bgCard,
                    color: C.text,
                    fontFamily: 'var(--font-ui)',
                    fontSize: 12,
                    fontWeight: 700,
                    cursor: pagedConcepts.page === 1 ? 'not-allowed' : 'pointer',
                    opacity: pagedConcepts.page === 1 ? 0.5 : 1,
                  }}
                >
                  上一頁
                </button>
                <span style={{ fontFamily: 'var(--font-ui)', fontSize: 12, color: C.textMuted }}>
                  {pagedConcepts.page} / {pagedConcepts.totalPages}
                </span>
                <button
                  type="button"
                  onClick={() => setConceptPage((p) => Math.min(pagedConcepts.totalPages, p + 1))}
                  disabled={pagedConcepts.page === pagedConcepts.totalPages}
                  style={{
                    minHeight: 36,
                    padding: '0 12px',
                    borderRadius: 999,
                    border: `1px solid ${C.border}`,
                    background: C.bgCard,
                    color: C.text,
                    fontFamily: 'var(--font-ui)',
                    fontSize: 12,
                    fontWeight: 700,
                    cursor: pagedConcepts.page === pagedConcepts.totalPages ? 'not-allowed' : 'pointer',
                    opacity: pagedConcepts.page === pagedConcepts.totalPages ? 0.5 : 1,
                  }}
                >
                  下一頁
                </button>
              </div>
            )}
            <p style={{ fontSize: 11, color: C.textSubtle, fontFamily: 'var(--font-ui)', margin: 0, textAlign: 'right' }}>
              由弱到強排列 · 圓點顏色代表掌握程度
            </p>
          </>
        )}
      </Section>

      {/* ── Section 5：混淆模式診斷 ──────────────────────────────────────── */}
      {!isLoading && (
        <Section title="混淆模式診斷">
          {!stats || stats.misconceptions.length === 0 ? (
            <EmptyNote text="目前尚未發現明顯混淆模式" />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <p style={{ margin: 0, fontFamily: 'var(--font-ui)', fontSize: 12, color: C.textMuted }}>
                第 {pagedMisconceptions.page} / {pagedMisconceptions.totalPages} 頁 · 每頁 20 筆 · 共 {pagedMisconceptions.total} 筆
              </p>
              <div
                style={{
                  height: 'clamp(420px, 58vh, 640px)',
                  overflowY: 'auto',
                  border: `1px solid ${C.border}`,
                  borderRadius: 12,
                  padding: '8px',
                  background: C.bgCard,
                }}
              >
                <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
                  {pagedMisconceptions.items.map((m, i) => {
                    const cfg = {
                      high:   { label: '嚴重', bg: C.redBg, border: C.redBorder, text: C.red, dot: C.red },
                      medium: { label: '中等', bg: C.yellowBg, border: '#fde68a', text: C.yellow, dot: C.yellow },
                      low:    { label: '輕微', bg: C.accentBg, border: '#fdba74', text: C.accent, dot: C.accent },
                    }[m.severity];
                    return (
                      <div
                        key={`${m.concept_name}-${i}`}
                        style={{
                          background: cfg.bg,
                          border: `1px solid ${cfg.border}`,
                          borderRadius: 10,
                          padding: '10px 14px',
                          display: 'grid',
                          gridTemplateColumns: '1fr auto',
                          gap: 10,
                          alignItems: 'start',
                        }}
                      >
                        <div>
                          <div style={{ fontFamily: 'var(--font-ui)', fontSize: 11, fontWeight: 700, color: cfg.text, marginBottom: 4 }}>
                            {m.concept_name}
                          </div>
                          <div style={{ fontFamily: 'var(--font-body)', fontSize: 14, color: C.text, lineHeight: 1.5, marginBottom: m.repair_strategy ? 7 : 0 }}>
                            {m.pattern}
                          </div>
                          {m.repair_strategy && (
                            <div style={{ fontFamily: 'var(--font-ui)', fontSize: 12, color: C.textMuted, lineHeight: 1.45, borderLeft: `2px solid ${cfg.dot}`, paddingLeft: 8 }}>
                              修正策略：{m.repair_strategy}
                            </div>
                          )}
                        </div>
                        <span
                          style={{
                            fontFamily: 'var(--font-ui)',
                            fontSize: 10,
                            fontWeight: 700,
                            padding: '3px 8px',
                            borderRadius: 999,
                            whiteSpace: 'nowrap',
                            flexShrink: 0,
                            color: cfg.text,
                            background: 'rgba(255,255,255,0.55)',
                            border: `1px solid ${cfg.border}`,
                          }}
                        >
                          {cfg.label}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
              {pagedMisconceptions.totalPages > 1 && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'center' }}>
                  <button
                    type="button"
                    onClick={() => setMisconceptionPage((p) => Math.max(1, p - 1))}
                    disabled={pagedMisconceptions.page === 1}
                    style={{
                      minHeight: 36,
                      padding: '0 12px',
                      borderRadius: 999,
                      border: `1px solid ${C.border}`,
                      background: C.bgCard,
                      color: C.text,
                      fontFamily: 'var(--font-ui)',
                      fontSize: 12,
                      fontWeight: 700,
                      cursor: pagedMisconceptions.page === 1 ? 'not-allowed' : 'pointer',
                      opacity: pagedMisconceptions.page === 1 ? 0.5 : 1,
                    }}
                  >
                    上一頁
                  </button>
                  <span style={{ fontFamily: 'var(--font-ui)', fontSize: 12, color: C.textMuted }}>
                    {pagedMisconceptions.page} / {pagedMisconceptions.totalPages}
                  </span>
                  <button
                    type="button"
                    onClick={() =>
                      setMisconceptionPage((p) => Math.min(pagedMisconceptions.totalPages, p + 1))
                    }
                    disabled={pagedMisconceptions.page === pagedMisconceptions.totalPages}
                    style={{
                      minHeight: 36,
                      padding: '0 12px',
                      borderRadius: 999,
                      border: `1px solid ${C.border}`,
                      background: C.bgCard,
                      color: C.text,
                      fontFamily: 'var(--font-ui)',
                      fontSize: 12,
                      fontWeight: 700,
                      cursor:
                        pagedMisconceptions.page === pagedMisconceptions.totalPages
                          ? 'not-allowed'
                          : 'pointer',
                      opacity:
                        pagedMisconceptions.page === pagedMisconceptions.totalPages ? 0.5 : 1,
                    }}
                  >
                    下一頁
                  </button>
                </div>
              )}
            </div>
          )}
        </Section>
      )}

      {/* ── Section 6：學習歷程時間線 ──────────────────────────────────── */}
      {decisionHistory.length > 0 && (
        <Section title="學習歷程時間線">
          <div style={{ position: 'relative', paddingLeft: 22 }}>
            {/* 垂直軸線 */}
            <div style={{ position: 'absolute', left: 7, top: 14, bottom: 14, width: 2, background: C.border, borderRadius: 1 }} />
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              {[...decisionHistory].reverse().map((d, i) => {
                const color = DECISION_COLORS[d.decision] ?? C.textMuted;
                const label = DECISION_LABELS[d.decision] ?? d.decision;
                const isLast = i === decisionHistory.length - 1;
                return (
                  <div key={i} style={{
                    display: 'grid', gridTemplateColumns: 'auto 1fr auto', gap: '0 12px', alignItems: 'center',
                    padding: '10px 0', borderBottom: isLast ? 'none' : `1px dashed ${C.border}`, minHeight: 44,
                  }}>
                    <div style={{ width: 14, height: 14, borderRadius: '50%', background: color, border: `2px solid ${C.bgCard}`, outline: `2px solid ${color}`, marginLeft: -27, flexShrink: 0 }} />
                    <span style={{ fontFamily: 'var(--font-body)', fontSize: 14, color: C.text, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {d.stageTitle || `階段 ${d.stageId}`}
                    </span>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                      <span style={{ fontFamily: 'var(--font-ui)', fontSize: 13, fontWeight: 700, color: masteryColor(d.bestScore) }}>
                        {Math.round(d.bestScore * 100)}%
                      </span>
                      <span style={{
                        fontFamily: 'var(--font-ui)', fontSize: 11, fontWeight: 700, padding: '3px 9px', borderRadius: 999,
                        color, background: color + '18', border: `1px solid ${color}`, whiteSpace: 'nowrap',
                      }}>
                        {label}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </Section>
      )}
      </>
      )}

    </div>
  );
});
