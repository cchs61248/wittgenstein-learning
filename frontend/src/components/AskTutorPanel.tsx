import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import { useSessionStore } from '../store/sessionStore';
import { deleteTutorRecord } from '../api/session';

const normalizeText = (text: string) => text.replace(/\\n/g, '\n');

interface Props {
  onAskTutor: (question: string) => void;
  onCancel: () => void;
  isCollapsed: boolean;
  onToggle: () => void;
  isLoading?: boolean;
  currentStageId: number | null;
}

function HistoryNote({
  item,
  index,
  defaultOpen,
  onDelete,
}: {
  item: { id?: number; question: string; answer: string; in_scope?: boolean; scope?: string };
  index: number;
  defaultOpen: boolean;
  onDelete?: () => void;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [deleting, setDeleting] = useState(false);

  const handleDelete = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!onDelete) return;
    setDeleting(true);
    await onDelete();
    setDeleting(false);
  };

  return (
    <div className="tutor-note">
      <button className="tutor-note-header" onClick={() => setOpen((v) => !v)}>
        <span className="tutor-note-idx">#{index + 1}</span>
        <span className="tutor-note-question">
          {item.question.length > 60 ? item.question.slice(0, 60) + '…' : item.question}
        </span>
        {(item.scope === 'out_of_scope' || (item.scope === undefined && item.in_scope === false)) && (
          <span className="tutor-note-scope-badge">教材外</span>
        )}
        {item.scope === 'other_chapter' && (
          <span className="tutor-note-scope-badge tutor-note-scope-badge--other">其他章節</span>
        )}
        <span className="tutor-note-toggle-icon">{open ? '▲' : '▼'}</span>
        {onDelete && item.id !== undefined && (
          <span
            className="tutor-note-delete"
            role="button"
            aria-label="刪除此問答"
            title="刪除此問答"
            onClick={handleDelete}
            style={{ opacity: deleting ? 0.5 : 1, pointerEvents: deleting ? 'none' : 'auto' }}
          >
            ×
          </span>
        )}
      </button>
      {open && (
        <div className="tutor-note-body">
          <p className="tutor-note-q-full">{item.question}</p>
          <div className="feedback-text markdown-content">
            <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>{normalizeText(item.answer)}</ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  );
}

export function AskTutorPanel({ onAskTutor, onCancel, isCollapsed, onToggle, isLoading = false, currentStageId }: Props) {
  const tutorHistoryMap = useSessionStore((s) => s.tutorHistory);
  const clearTutorHistory = useSessionStore((s) => s.clearTutorHistory);
  const deleteTutorMessage = useSessionStore((s) => s.deleteTutorMessage);
  const token = useSessionStore((s) => s.token);
  const sessionId = useSessionStore((s) => s.sessionId);
  const streamingTutorQuestion = useSessionStore((s) => s.streamingTutorQuestion);
  const streamingTutorStageId = useSessionStore((s) => s.streamingTutorStageId);
  const streamingTutorAnswer = useSessionStore((s) => s.streamingTutorAnswer);
  const stageHistory = currentStageId !== null && currentStageId !== undefined
    ? (tutorHistoryMap[currentStageId] ?? [])
    : [];
  const [question, setQuestion] = useState('');
  const streamingBodyRef = useRef<HTMLDivElement | null>(null);
  const [stickyToBottom, setStickyToBottom] = useState(true);

  // 偵測使用者是否手動 scroll 離底；50px 容差讓「快回到底」仍視為 sticky
  const handleStreamingScroll = () => {
    const el = streamingBodyRef.current;
    if (!el) return;
    const dist = el.scrollHeight - (el.scrollTop + el.clientHeight);
    setStickyToBottom(dist < 50);
  };

  // 新 chunk 進來時 auto-scroll 到底（但只在 sticky 狀態）
  useEffect(() => {
    if (streamingTutorQuestion === null) {
      // 串流結束：reset sticky，下次串流重新啟用 auto-scroll
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setStickyToBottom(true);
      return;
    }
    if (!stickyToBottom) return;
    const el = streamingBodyRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [streamingTutorAnswer, streamingTutorQuestion, stickyToBottom]);

  const handleDeleteItem = async (recordId: number) => {
    if (!token || !sessionId || currentStageId === null) return;
    const ok = await deleteTutorRecord(token, sessionId, recordId);
    if (ok) deleteTutorMessage(currentStageId, recordId);
  };

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
          {stageHistory.length > 0 && (
            <span className="tutor-history-count">{stageHistory.length}</span>
          )}
        </span>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {stageHistory.length > 0 && !isCollapsed && (
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
          {streamingTutorQuestion !== null && streamingTutorStageId === currentStageId && (
            <div className="tutor-note tutor-note--streaming">
              <div className="tutor-note-header">
                <span className="tutor-note-idx">…</span>
                <span className="tutor-note-question">
                  {streamingTutorQuestion.length > 60 ? streamingTutorQuestion.slice(0, 60) + '…' : streamingTutorQuestion}
                </span>
                <span className="tutor-note-toggle-icon">輸入中</span>
                <button
                  className="btn-ghost btn-sm tutor-note-cancel"
                  onClick={onCancel}
                  type="button"
                  aria-label="停止生成"
                >
                  停止生成
                </button>
              </div>
              <div
                className="tutor-note-body"
                ref={streamingBodyRef}
                onScroll={handleStreamingScroll}
              >
                <p className="tutor-note-q-full">{streamingTutorQuestion}</p>
                <div className="feedback-text markdown-content">
                  <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
                    {normalizeText(streamingTutorAnswer)}
                  </ReactMarkdown>
                </div>
              </div>
            </div>
          )}
          {stageHistory.length > 0 && (
            <div className="tutor-history-list">
              {[...stageHistory].reverse().map((item, reversedIdx) => (
                <HistoryNote
                  key={item.id ?? reversedIdx}
                  item={item}
                  index={stageHistory.length - 1 - reversedIdx}
                  defaultOpen={false}
                  onDelete={item.id !== undefined ? () => handleDeleteItem(item.id!) : undefined}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
