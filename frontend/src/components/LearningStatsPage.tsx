import { useState, useEffect } from 'react';
import { useSessionStore } from '../store/sessionStore';
import { fetchLearnerStats, type LearnerStats } from '../api/learner';

function masteryLevel(score: number) {
  return score >= 0.75 ? 'high' : score >= 0.5 ? 'mid' : 'low';
}

function decisionLabel(d: string) {
  const map: Record<string, string> = {
    advance: '通過',
    retry: '重試',
    remediate: '補強',
    reteach: '重教',
  };
  return map[d] ?? d;
}

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

  const completedCount = stages.filter((s) => s.status === 'completed').length;
  const allQaItems = Object.values(stageQaHistories).flat();
  const avgScore =
    allQaItems.length > 0
      ? Math.round(
          (allQaItems.reduce((s, i) => s + i.score, 0) / allQaItems.length) * 100,
        )
      : null;

  if (!isLoading && stages.length === 0 && (!stats || stats.concepts.length === 0)) {
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
      {/* Section 1：總覽 */}
      <div className="stat-cards-row">
        <div className="stat-card">
          <span className="stat-card-label">已完成階段</span>
          <span className="stat-card-value">
            {completedCount}
            <span style={{ fontSize: '1rem', fontWeight: 600 }}>/{stages.length}</span>
          </span>
          <span className="stat-card-sub">個學習階段</span>
        </div>
        <div className="stat-card">
          <span className="stat-card-label">平均答題分數</span>
          <span className="stat-card-value">{avgScore !== null ? `${avgScore}%` : '—'}</span>
          <span className="stat-card-sub">本課程</span>
        </div>
        <div className="stat-card">
          <span className="stat-card-label">已回答題數</span>
          <span className="stat-card-value">{allQaItems.length}</span>
          <span className="stat-card-sub">題</span>
        </div>
      </div>

      {/* Section 2：概念掌握度 */}
      <div className="stats-section">
        <h2 className="stats-section-title">概念掌握度</h2>
        {isLoading ? (
          <div className="stats-empty">載入中...</div>
        ) : !stats || stats.concepts.length === 0 ? (
          <div className="stats-empty">尚無概念掌握度記錄</div>
        ) : (
          stats.concepts.map((c) => {
            const level = masteryLevel(c.mastery_score);
            const pct = Math.round(c.mastery_score * 100);
            return (
              <div key={c.concept_name} className="mastery-concept-row">
                <span className="mastery-concept-name">{c.concept_name}</span>
                <div className="mastery-bar-track">
                  <div
                    className={`mastery-bar-fill level-${level}`}
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <span className={`mastery-pct level-${level}`}>{pct}%</span>
                <span className="mastery-exposures">{c.total_exposures} 次</span>
              </div>
            );
          })
        )}
      </div>

      {/* Section 3：Stage 表現 */}
      {stages.length > 0 && (
        <div className="stats-section">
          <h2 className="stats-section-title">階段表現</h2>
          <div className="stage-perf-table-scroll">
            <table className="stage-perf-table">
              <thead>
                <tr>
                  <th>階段</th>
                  <th>答題數</th>
                  <th>最高分</th>
                  <th>狀態</th>
                </tr>
              </thead>
              <tbody>
                {stages.map((s) => {
                  const items = stageQaHistories[s.stage_id] ?? [];
                  const best =
                    items.length > 0 ? Math.max(...items.map((i) => i.score)) : null;
                  const statusLabel =
                    s.status === 'completed'
                      ? '已完成'
                      : s.status === 'current'
                        ? '進行中'
                        : '待解鎖';
                  const statusColor =
                    s.status === 'completed'
                      ? 'var(--green)'
                      : s.status === 'current'
                        ? 'var(--accent)'
                        : 'var(--text-subtle)';
                  return (
                    <tr key={s.stage_id}>
                      <td>{s.title}</td>
                      <td>{items.length}</td>
                      <td>{best !== null ? `${Math.round(best * 100)}%` : '—'}</td>
                      <td>
                        <span style={{ color: statusColor, fontWeight: 700 }}>
                          {statusLabel}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Section 4：混淆模式 */}
      <div className="stats-section">
        <h2 className="stats-section-title">混淆模式</h2>
        {isLoading ? null : !stats || stats.misconceptions.length === 0 ? (
          <div className="stats-empty">目前尚未發現明顯混淆模式</div>
        ) : (
          stats.misconceptions.map((m, i) => (
            <div key={i} className="misconception-item">
              <div>
                <div className="misconception-concept">{m.concept_name}</div>
                <div className="misconception-pattern">{m.pattern}</div>
              </div>
              <span className={`severity-badge severity-${m.severity}`}>
                {m.severity === 'high' ? '嚴重' : m.severity === 'medium' ? '中等' : '輕微'}
              </span>
            </div>
          ))
        )}
      </div>

      {/* Section 5：決策記錄 */}
      <div className="stats-section">
        <h2 className="stats-section-title">決策記錄</h2>
        {decisionHistory.length === 0 ? (
          <div className="stats-empty">尚無決策記錄</div>
        ) : (
          [...decisionHistory].reverse().map((d, i) => (
            <div key={i} className="decision-timeline-item">
              <span className={`decision-badge decision-${d.decision}`}>
                {decisionLabel(d.decision)}
              </span>
              <span className="decision-stage-title">{d.stageTitle}</span>
              <span className="decision-score">{Math.round(d.bestScore * 100)}%</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
