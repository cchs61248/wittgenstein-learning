import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useSessionStore } from '../store/sessionStore';

export function ExplanationPanel() {
  const explanationText = useSessionStore((s) => s.explanationText);
  const isStreaming = useSessionStore((s) => s.isStreaming);
  const selectedStageId = useSessionStore((s) => s.selectedStageId);
  const currentStageId = useSessionStore((s) => s.currentStageId);
  const stageExplanations = useSessionStore((s) => s.stageExplanations);
  const stageSourceChunks = useSessionStore((s) => s.stageSourceChunks);
  const setSelectedStage = useSessionStore((s) => s.setSelectedStage);

  const reviewText = selectedStageId !== null ? (stageExplanations[selectedStageId] ?? null) : null;
  // 切換 session 後 explanationText 被清空，但已完成章節的文字仍在 stageExplanations
  const currentStageStoredText = currentStageId !== null ? (stageExplanations[currentStageId] ?? '') : '';
  const displayText = reviewText ?? (explanationText || currentStageStoredText);
  const stageIdForDisplay = selectedStageId ?? currentStageId;
  const chunks = stageIdForDisplay !== null ? (stageSourceChunks[stageIdForDisplay] ?? []) : [];
  const refs = Array.from(new Set((displayText.match(/\[([A-Za-z0-9_.:-]+)\]/g) ?? []).map((m) => m.slice(1, -1))));
  const referencedChunks = refs
    .map((id) => ({ id, chunk: chunks.find((c) => c.chunk_id === id) }))
    .filter((x) => x.chunk);

  if (!displayText && !isStreaming) {
    return (
      <div className="explanation-panel empty">
        <div className="empty-ornament" aria-hidden="true" />
        <p className="empty-lead">等待學習開始</p>
        <p className="empty-hint">上傳材料後，講解與提問會依序出現在這裡</p>
      </div>
    );
  }

  return (
    <div className="explanation-panel">
      {reviewText !== null && (
        <div className="review-banner">
          <span>回顧模式</span>
          <button className="btn-ghost btn-sm" onClick={() => setSelectedStage(null)}>
            返回當前學習 →
          </button>
        </div>
      )}
      <div className="markdown-content">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{displayText}</ReactMarkdown>
        {isStreaming && reviewText === null && <span className="cursor-blink">▋</span>}
      </div>
      {referencedChunks.length > 0 && (
        <div className="source-reference-panel">
          <div className="source-reference-title">來源追溯（滑鼠移上查看原文）</div>
          <div className="source-reference-list">
            {referencedChunks.map(({ id, chunk }) => (
              <span key={id} className="source-chip">
                [{id}]
                <span className="source-tooltip">{chunk?.quote}</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
