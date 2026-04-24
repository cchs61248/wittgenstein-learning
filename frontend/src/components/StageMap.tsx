import { useSessionStore, type StageStatus } from '../store/sessionStore';

const statusLabel: Record<StageStatus, string> = {
  pending: '待解鎖',
  current: '進行中',
  completed: '已完成',
};

export function StageMap() {
  const stages = useSessionStore((s) => s.stages);
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

      <ul className="stage-list">
        {stages.map((stage) => (
          <li key={stage.stage_id} className={`stage-item stage-${stage.status}`}>
            <span className="stage-dot" />
            <span className="stage-title">{stage.title}</span>
            <span className="stage-status-label">{statusLabel[stage.status]}</span>
          </li>
        ))}
      </ul>
    </aside>
  );
}
