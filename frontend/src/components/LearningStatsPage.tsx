import { useState, useEffect, useMemo } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, PieChart, Pie, Cell, BarChart, Bar,
} from 'recharts';
import { useSessionStore } from '../store/sessionStore';
import { fetchLearnerStats, type LearnerStats } from '../api/learner';

// ── Design tokens（對應 App.css CSS 變數）──────────────────────────────────
const C = {
  green:       '#15803d',
  greenBg:     '#dcfce7',
  greenBorder: '#86efac',
  accent:      '#d97706',
  accentBg:    '#ffedd5',
  yellow:      '#b45309',
  yellowBg:    '#fffbeb',
  red:         '#b91c1c',
  redBg:       '#fee2e2',
  redBorder:   '#fecaca',
  border:      '#fcd34d',
  gridLine:    '#fde68a',
  bg:          '#fffbeb',
  bgCard:      '#ffffff',
  text:        '#422006',
  textMuted:   '#92400e',
  textSubtle:  '#b45309',
};

const DECISION_COLORS: Record<string, string> = {
  advance:   C.green,
  retry:     C.yellow,
  remediate: C.accent,
  reteach:   C.red,
};

const DECISION_LABELS: Record<string, string> = {
  advance: '通過', retry: '重試', remediate: '補強', reteach: '重教',
};

function masteryColor(score: number) {
  if (score >= 0.75) return C.green;
  if (score >= 0.5)  return C.accent;
  return C.red;
}

function masteryLabel(score: number) {
  if (score >= 0.75) return '精熟';
  if (score >= 0.5)  return '學習中';
  return '需加強';
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

function MasteryTooltip({ active, payload }: { active?: boolean; payload?: Array<{ value: number; payload: { fullConcept: string; exposures: number } }> }) {
  if (!active || !payload?.length) return null;
  const val = payload[0].value;
  const { fullConcept, exposures } = payload[0].payload;
  return (
    <div style={{ background: C.bgCard, border: `1px solid ${C.border}`, borderRadius: 10, padding: '10px 14px', fontFamily: 'var(--font-ui)', fontSize: 13 }}>
      <div style={{ fontWeight: 700, color: C.text, marginBottom: 3 }}>{fullConcept}</div>
      <div style={{ color: masteryColor(val / 100), fontWeight: 700 }}>{val}% · {masteryLabel(val / 100)}</div>
      <div style={{ color: C.textSubtle, fontSize: 11, marginTop: 2 }}>接觸 {exposures} 次</div>
    </div>
  );
}

function StageBarTooltip({ active, payload }: { active?: boolean; payload?: Array<{ value: number; payload: { fullTitle: string; answers: number } }> }) {
  if (!active || !payload?.length) return null;
  const val = payload[0].value;
  const { fullTitle, answers } = payload[0].payload;
  return (
    <div style={{ background: C.bgCard, border: `1px solid ${C.border}`, borderRadius: 10, padding: '10px 14px', fontFamily: 'var(--font-ui)', fontSize: 13 }}>
      <div style={{ color: C.textSubtle, fontSize: 11, marginBottom: 2 }}>{fullTitle}</div>
      <div style={{ fontWeight: 700, color: masteryColor(val / 100), fontSize: 15 }}>{val}%</div>
      <div style={{ color: C.textSubtle, fontSize: 11, marginTop: 2 }}>{answers} 題</div>
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
export function LearningStatsPage({ token }: { token: string }) {
  const { stages, stageQaHistories, decisionHistory } = useSessionStore();
  const [stats, setStats] = useState<LearnerStats | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    fetchLearnerStats(token).then((data) => {
      setStats(data);
      setIsLoading(false);
    });
  }, [token]);

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

  // 各節點最高分
  const stageBarData = useMemo(() =>
    stages
      .filter((s) => (stageQaHistories[s.stage_id]?.length ?? 0) > 0)
      .map((s) => {
        const items = stageQaHistories[s.stage_id] ?? [];
        const best = items.length > 0 ? Math.max(...items.map((i) => i.score)) : 0;
        return {
          title: s.title.length > 14 ? s.title.slice(0, 12) + '…' : s.title,
          fullTitle: s.title,
          value: Math.round(best * 100),
          answers: items.length,
          color: masteryColor(best),
        };
      }),
    [stages, stageQaHistories]
  );

  // 空狀態
  const hasAnyData = stages.length > 0 || (stats && stats.concepts.length > 0) || decisionHistory.length > 0;
  if (!isLoading && !hasAnyData) {
    return (
      <div className="stats-page">
        <div className="stats-empty-guide">
          <p>上傳學習材料並開始學習後，這裡會顯示你的概念掌握度、答題成效與決策記錄。</p>
        </div>
      </div>
    );
  }

  return (
    <div className="stats-page">

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
              <div style={{ height: Math.max(200, stageBarData.length * 36 + 20), minHeight: 200, maxHeight: 360, overflowY: 'auto' }}>
                <div style={{ height: Math.max(200, stageBarData.length * 36) }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={stageBarData}
                      layout="vertical"
                      margin={{ top: 0, right: 48, left: 4, bottom: 0 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" stroke={C.gridLine} horizontal={false} />
                      <XAxis
                        type="number" domain={[0, 100]} unit="%"
                        tick={{ fontSize: 11, fill: C.textSubtle, fontFamily: 'var(--font-ui)' }}
                        axisLine={false} tickLine={false}
                      />
                      <YAxis
                        type="category" dataKey="title" width={84}
                        tick={{ fontSize: 11, fill: C.textMuted, fontFamily: 'var(--font-ui)' }}
                        axisLine={false} tickLine={false}
                      />
                      <Tooltip content={<StageBarTooltip />} />
                      <Bar dataKey="value" radius={[0, 4, 4, 0]} maxBarSize={18} label={{ position: 'right', fontSize: 11, fontFamily: 'var(--font-ui)', fill: C.textMuted, formatter: (v: unknown) => `${v}%` }}>
                        {stageBarData.map((entry, i) => (
                          <Cell key={i} fill={entry.color} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
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
            <div style={{ height: Math.min(Math.max(conceptData.length * 32, 100), 400) }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={conceptData}
                  layout="vertical"
                  margin={{ top: 0, right: 52, left: 8, bottom: 0 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke={C.gridLine} horizontal={false} />
                  <XAxis
                    type="number" domain={[0, 100]} unit="%"
                    tick={{ fontSize: 11, fill: C.textSubtle, fontFamily: 'var(--font-ui)' }}
                    axisLine={false} tickLine={false}
                  />
                  <YAxis
                    type="category" dataKey="concept" width={108}
                    tick={{ fontSize: 11, fill: C.textMuted, fontFamily: 'var(--font-ui)' }}
                    axisLine={false} tickLine={false}
                  />
                  <Tooltip content={<MasteryTooltip />} />
                  <Bar
                    dataKey="value"
                    radius={[0, 4, 4, 0]}
                    maxBarSize={16}
                    label={{ position: 'right', fontSize: 11, fontFamily: 'var(--font-ui)', fill: C.textMuted, formatter: (v: unknown) => `${v}%` }}
                  >
                    {conceptData.map((entry, i) => (
                      <Cell key={i} fill={entry.color} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
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
              {(['high', 'medium', 'low'] as const).map((severity) => {
                const items = stats.misconceptions.filter((m) => m.severity === severity);
                if (!items.length) return null;
                const cfg = {
                  high:   { label: '嚴重誤解', bg: C.redBg,   border: C.redBorder,          text: C.red,    dot: C.red },
                  medium: { label: '中度混淆', bg: C.yellowBg, border: '#fde68a',             text: C.yellow, dot: C.yellow },
                  low:    { label: '輕微疑點', bg: C.accentBg, border: '#fdba74',             text: C.accent, dot: C.accent },
                }[severity];
                return (
                  <div key={severity}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 8 }}>
                      <span style={{ width: 8, height: 8, borderRadius: '50%', background: cfg.dot, flexShrink: 0 }} />
                      <span style={{ fontFamily: 'var(--font-ui)', fontSize: 11, fontWeight: 700, color: cfg.text, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{cfg.label}</span>
                      <span style={{ fontFamily: 'var(--font-ui)', fontSize: 11, color: C.textSubtle }}>{items.length} 筆</span>
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
                      {items.map((m, i) => (
                        <div key={i} style={{
                          background: cfg.bg, border: `1px solid ${cfg.border}`, borderRadius: 10, padding: '10px 14px',
                          display: 'grid', gridTemplateColumns: '1fr auto', gap: 10, alignItems: 'start',
                        }}>
                          <div>
                            <div style={{ fontFamily: 'var(--font-ui)', fontSize: 11, fontWeight: 700, color: cfg.text, marginBottom: 4 }}>{m.concept_name}</div>
                            <div style={{ fontFamily: 'var(--font-body)', fontSize: 14, color: C.text, lineHeight: 1.5, marginBottom: m.repair_strategy ? 7 : 0 }}>{m.pattern}</div>
                            {m.repair_strategy && (
                              <div style={{ fontFamily: 'var(--font-ui)', fontSize: 12, color: C.textMuted, lineHeight: 1.45, borderLeft: `2px solid ${cfg.dot}`, paddingLeft: 8 }}>
                                修正策略：{m.repair_strategy}
                              </div>
                            )}
                          </div>
                          <span style={{
                            fontFamily: 'var(--font-ui)', fontSize: 10, fontWeight: 700, padding: '3px 8px', borderRadius: 999, whiteSpace: 'nowrap', flexShrink: 0,
                            color: cfg.text, background: 'rgba(255,255,255,0.55)', border: `1px solid ${cfg.border}`,
                          }}>
                            {severity === 'high' ? '嚴重' : severity === 'medium' ? '中等' : '輕微'}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
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

    </div>
  );
}
