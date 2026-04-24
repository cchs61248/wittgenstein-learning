import ReactMarkdown from 'react-markdown';
import { useSessionStore } from '../store/sessionStore';

export function ExplanationPanel() {
  const explanationText = useSessionStore((s) => s.explanationText);
  const isStreaming = useSessionStore((s) => s.isStreaming);
  const stages = useSessionStore((s) => s.stages);
  const currentStageId = useSessionStore((s) => s.currentStageId);

  const currentStage = stages.find((s) => s.stage_id === currentStageId);

  if (!explanationText && !isStreaming) {
    return (
      <div className="explanation-panel empty">
        <p>等待學習開始...</p>
      </div>
    );
  }

  return (
    <div className="explanation-panel">
      {currentStage && (
        <div className="stage-header">
          <span className="stage-badge">階段 {currentStageId}</span>
          <h2>{currentStage.title}</h2>
        </div>
      )}

      <div className="markdown-content">
        <ReactMarkdown>{explanationText}</ReactMarkdown>
        {isStreaming && <span className="cursor-blink">▋</span>}
      </div>
    </div>
  );
}
