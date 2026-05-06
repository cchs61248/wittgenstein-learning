import { useSessionStore, type StageStatus } from '../store/sessionStore';

const statusLabel: Record<StageStatus, string> = {
  pending: '待解鎖',
  current: '進行中',
  completed: '已完成',
};

interface StageMapProps {
  hideHeading?: boolean;
}

export function StageMap({ hideHeading = false }: StageMapProps) {
  const stages = useSessionStore((s) => s.stages);
  const selectedStageId = useSessionStore((s) => s.selectedStageId);
  const setSelectedStage = useSessionStore((s) => s.setSelectedStage);
  const total = stages.length;
  const completed = stages.filter((s) => s.status === 'completed').length;
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;

  // 將 reteach/remediation 子章節依 source_stage_id 分組，其餘為根章節
  const childrenMap = new Map<number, typeof stages>();
  const rootStages: typeof stages = [];
  for (const stage of stages) {
    if ((stage.kind === 'reteach' || stage.kind === 'remediation') && stage.source_stage_id != null) {
      const arr = childrenMap.get(stage.source_stage_id) ?? [];
      arr.push(stage);
      childrenMap.set(stage.source_stage_id, arr);
    } else {
      rootStages.push(stage);
    }
  }

  const renderItem = (stage: (typeof stages)[0], isChild: boolean) => {
    const canReview = stage.status === 'completed';
    const isSelected = selectedStageId === stage.stage_id;
    const kindBadge =
      stage.kind === 'reteach' ? '重教' :
      stage.kind === 'remediation' ? '補強' :
      stage.kind === 'enrichment' ? '整合挑戰' :
      null;
    const cls = [
      'stage-item',
      `stage-${stage.status}`,
      isSelected ? 'stage-selected' : '',
      canReview ? 'stage-clickable' : '',
      isChild ? 'stage-child' : '',
    ].filter(Boolean).join(' ');

    return (
      <li
        key={stage.stage_id}
        className={cls}
        onClick={canReview ? () => setSelectedStage(stage.stage_id) : undefined}
      >
        <span className="stage-dot" />
        <div className="stage-info">
          <span className="stage-title">
            {kindBadge && <span className="stage-kind-badge">{kindBadge}</span>}
            {stage.title}
          </span>
          <span className="stage-status-label">
            {isSelected ? '回顧中' : statusLabel[stage.status]}
          </span>
        </div>
      </li>
    );
  };

  return (
    <aside className="stage-map">
      {!hideHeading && <h3>學習進度</h3>}
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
        {rootStages.flatMap((stage) => [
          renderItem(stage, false),
          ...(childrenMap.get(stage.stage_id) ?? []).map((child) => renderItem(child, true)),
        ])}
      </ul>
    </aside>
  );
}
