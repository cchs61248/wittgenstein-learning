import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useSessionStore } from '../store/sessionStore';

export function ExplanationPanel() {
  const explanationText = useSessionStore((s) => s.explanationText);
  const isStreaming = useSessionStore((s) => s.isStreaming);

  if (!explanationText && !isStreaming) {
    return (
      <div className="explanation-panel empty">
        <p>等待學習開始...</p>
      </div>
    );
  }

  return (
    <div className="explanation-panel">
      <div className="markdown-content">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{explanationText}</ReactMarkdown>
        {isStreaming && <span className="cursor-blink">▋</span>}
      </div>
    </div>
  );
}
