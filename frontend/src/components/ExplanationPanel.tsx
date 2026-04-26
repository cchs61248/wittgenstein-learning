import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useSessionStore } from '../store/sessionStore';

export function ExplanationPanel() {
  const explanationText = useSessionStore((s) => s.explanationText);
  const isStreaming = useSessionStore((s) => s.isStreaming);
  const selectedStageId = useSessionStore((s) => s.selectedStageId);
  const stageExplanations = useSessionStore((s) => s.stageExplanations);
  const setSelectedStage = useSessionStore((s) => s.setSelectedStage);

  const reviewText = selectedStageId !== null ? (stageExplanations[selectedStageId] ?? null) : null;
  const displayText = reviewText ?? explanationText;

  if (!displayText && !isStreaming) {
    return (
      <div className="explanation-panel empty">
        <p>等待學習開始...</p>
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
    </div>
  );
}
