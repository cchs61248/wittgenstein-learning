import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useSessionStore } from '../store/sessionStore';

const normalizeText = (text: string) => text.replace(/\\n/g, '\n');

interface Props {
  onAskTutor: (question: string) => void;
  isCollapsed: boolean;
  onToggle: () => void;
  isLoading?: boolean;
}

function HistoryNote({
  item,
  index,
  defaultOpen,
}: {
  item: { question: string; answer: string; in_scope?: boolean };
  index: number;
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="tutor-note">
      <button className="tutor-note-header" onClick={() => setOpen((v) => !v)}>
        <span className="tutor-note-idx">#{index + 1}</span>
        <span className="tutor-note-question">
          {item.question.length > 60 ? item.question.slice(0, 60) + '…' : item.question}
        </span>
        {item.in_scope === false && (
          <span className="tutor-note-scope-badge">教材外</span>
        )}
        <span className="tutor-note-toggle-icon">{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <div className="tutor-note-body">
          <p className="tutor-note-q-full">{item.question}</p>
          <div className="feedback-text markdown-content">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{normalizeText(item.answer)}</ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  );
}

export function AskTutorPanel({ onAskTutor, isCollapsed, onToggle, isLoading = false }: Props) {
  const tutorHistory = useSessionStore((s) => s.tutorHistory);
  const clearTutorHistory = useSessionStore((s) => s.clearTutorHistory);
  const [question, setQuestion] = useState('');

  const handleSend = () => {
    if (!question.trim() || isLoading) return;
    onAskTutor(question.trim());
    setQuestion('');
  };

  return (
    <div className={`ask-tutor-panel${isCollapsed ? ' is-collapsed' : ''}`}>
      <div className="collapsible-header">
        <span className="collapsible-title">
          想追問老師
          {tutorHistory.length > 0 && (
            <span className="tutor-history-count">{tutorHistory.length}</span>
          )}
        </span>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {tutorHistory.length > 0 && !isCollapsed && (
            <button
              className="collapsible-toggle"
              style={{ fontSize: 11 }}
              onClick={clearTutorHistory}
              title="清除所有問答記錄"
            >
              清除
            </button>
          )}
          <button className="collapsible-toggle" onClick={onToggle} aria-expanded={!isCollapsed}>
            {isCollapsed ? '展開 ▼' : '收起 ▲'}
          </button>
        </div>
      </div>
      {!isCollapsed && (
        <div className="ask-tutor-body">
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder={isLoading ? '等待老師回覆中…' : '可詢問教材內容，超出教材會標註並以外部知識補充'}
            rows={3}
            disabled={isLoading}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && e.ctrlKey) handleSend();
            }}
          />
          <div className="answer-actions" style={{ marginTop: 0 }}>
            <span className="hint-text">{isLoading ? '等待回覆中…' : 'Ctrl + Enter 發問'}</span>
            <button className="btn-ghost" onClick={handleSend} disabled={!question.trim() || isLoading}>
              {isLoading ? '發問中…' : '發問'}
            </button>
          </div>
          {tutorHistory.length > 0 && (
            <div className="tutor-history-list">
              {[...tutorHistory].reverse().map((item, reversedIdx) => (
                <HistoryNote
                  key={reversedIdx}
                  item={item}
                  index={tutorHistory.length - 1 - reversedIdx}
                  defaultOpen={false}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
