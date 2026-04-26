import { useSessionStore, type StageStatus } from '../store/sessionStore';

const statusLabel: Record<StageStatus, string> = {
  pending: '待解鎖',
  current: '進行中',
  completed: '已完成',
};

export function StageMap() {
  const stages = useSessionStore((s) => s.stages);
  const selectedStageId = useSessionStore((s) => s.selectedStageId);
  const setSelectedStage = useSessionStore((s) => s.setSelectedStage);
  const stageExplanations = useSessionStore((s) => s.stageExplanations);
  const total = stages.length;
  const completed = stages.filter((s) => s.status === 'completed').length;
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;

  return (
    <aside className="stage-map">
      <h3>學習進度</h3>
      <div className="progress-bar">
        <div className="progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <p className="progress-label">{pct}% 完成</p>

      {selectedStageId !== null && (
        <button className="btn-ghost btn-sm stage-map-back" onClick={() => setSelectedStage(null)}>
          ← 返回當前
        </button>
      )}

      <ul className="stage-list">
        {stages.map((stage) => {
          const canReview = stage.status === 'completed' && stageExplanations[stage.stage_id];
          const isSelected = selectedStageId === stage.stage_id;
          return (
            <li
              key={stage.stage_id}
              className={`stage-item stage-${stage.status}${isSelected ? ' stage-selected' : ''}${canReview ? ' stage-clickable' : ''}`}
              onClick={canReview ? () => setSelectedStage(stage.stage_id) : undefined}
            >
              <span className="stage-dot" />
              <div className="stage-info">
                <span className="stage-title">{stage.title}</span>
                <span className="stage-status-label">
                  {isSelected ? '回顧中' : statusLabel[stage.status]}
                </span>
              </div>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
