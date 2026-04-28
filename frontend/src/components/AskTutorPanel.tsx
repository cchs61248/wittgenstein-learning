import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useSessionStore } from '../store/sessionStore';

const normalizeText = (text: string) => text.replace(/\\n/g, '\n');

interface Props {
  onAskTutor: (question: string) => void;
  isCollapsed: boolean;
  onToggle: () => void;
}

export function AskTutorPanel({ onAskTutor, isCollapsed, onToggle }: Props) {
  const tutorReply = useSessionStore((s) => s.tutorReply);
  const [question, setQuestion] = useState('');

  const handleSend = () => {
    if (!question.trim()) return;
    onAskTutor(question.trim());
    setQuestion('');
  };

  return (
    <div className={`ask-tutor-panel${isCollapsed ? ' is-collapsed' : ''}`}>
      <div className="collapsible-header">
        <span className="collapsible-title">想追問老師</span>
        <button className="collapsible-toggle" onClick={onToggle} aria-expanded={!isCollapsed}>
          {isCollapsed ? '展開 ▼' : '收起 ▲'}
        </button>
      </div>
      {!isCollapsed && (
        <div className="ask-tutor-body">
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="可詢問教材內容，超出教材會標註並以外部知識補充"
            rows={2}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && e.ctrlKey) handleSend();
            }}
          />
          <div className="answer-actions">
            <button className="btn-ghost" onClick={handleSend} disabled={!question.trim()}>
              發問
            </button>
          </div>
          {tutorReply && (
            <div className="feedback-text markdown-content">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{normalizeText(tutorReply.answer)}</ReactMarkdown>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
