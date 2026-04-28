import type { StageDecisionPayload } from '../types/messages';
import { useSessionStore } from '../store/sessionStore';

interface Props {
  decision: StageDecisionPayload;
}

export function LearningCoachPanel({ decision }: Props) {
  const snapshot = decision.strategy_snapshot;
  const decisionHistory = useSessionStore((s) => s.decisionHistory);
  if (!snapshot) return null;

  const trend = snapshot.score_trend ?? [];
  const avg = trend.length > 0 ? trend.reduce((a, b) => a + b, 0) / trend.length : null;
  const weakConcepts = snapshot.weak_concepts ?? [];
  const candidates = snapshot.next_stage_candidates ?? [];
  const recentDecisions = decisionHistory.slice(-8).reverse();

  return (
    <div className="coach-panel">
      <div className="coach-header">
        <span className="detail-label">學習教練面板</span>
        <span className={`question-type ${snapshot.stable_high ? 'score-pass' : 'score-fail'}`}>
          {snapshot.stable_high ? '掌握穩定' : '需持續補強'}
        </span>
      </div>

      <div className="coach-grid">
        <div className="coach-block">
          <span className="detail-label">掌握趨勢（近 5 題）</span>
          {trend.length > 0 ? (
            <>
              <p>{trend.map((v) => `${Math.round(v * 100)}%`).join(' → ')}</p>
              {avg !== null && <p>平均：{Math.round(avg * 100)}%</p>}
            </>
          ) : (
            <p>尚無足夠資料</p>
          )}
        </div>

        <div className="coach-block">
          <span className="detail-label">目前弱點概念</span>
          {weakConcepts.length > 0 ? <p>{weakConcepts.join('、')}</p> : <p>暫無明確弱點</p>}
        </div>
      </div>

      <div className="coach-block">
        <span className="detail-label">下一節候選分數</span>
        {candidates.length > 0 ? (
          <div className="coach-candidate-list">
            {candidates.map((c, idx) => (
              <div key={`${c.stage_id}-${idx}`} className="coach-candidate-item">
                <span>{idx + 1}. {c.title}</span>
                <span className="score-badge">{c.score.toFixed(2)}</span>
              </div>
            ))}
          </div>
        ) : (
          <p>目前無可選候選（可能即將完課或新增節點）</p>
        )}
        {decision.next_stage_score !== undefined && decision.next_stage_score !== null && (
          <p>最終採用分數：{decision.next_stage_score.toFixed(2)}</p>
        )}
      </div>

      <div className="coach-block">
        <span className="detail-label">決策歷史趨勢</span>
        {recentDecisions.length > 0 ? (
          <div className="coach-candidate-list">
            {recentDecisions.map((h, idx) => (
              <div key={`${h.at}-${idx}`} className="coach-candidate-item">
                <span>
                  {new Date(h.at).toLocaleTimeString()} | {h.stageTitle || '未命名節點'} | {h.decision}
                </span>
                <span className="score-badge">{Math.round(h.bestScore * 100)}%</span>
              </div>
            ))}
          </div>
        ) : (
          <p>尚無歷史決策資料</p>
        )}
      </div>
    </div>
  );
}
