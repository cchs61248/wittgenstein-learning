import { useState } from 'react';
import { useSessionStore } from '../store/sessionStore';
import type { QaHistoryItem } from '../store/sessionStore';

interface Props {
  onSubmit: (questionId: string, answer: string) => void;
}

const typeLabel: Record<string, string> = {
  apply: '應用型',
  understand: '理解型',
  create: '創作型',
};

function HistoryDetail({ item }: { item: QaHistoryItem }) {
  return (
    <div className="qa-history-detail">
      <div className="qa-history-detail-block">
        <span className="detail-label">題目</span>
        <p>{item.questionText}</p>
      </div>
      <div className="qa-history-detail-block">
        <span className="detail-label">你的回答</span>
        <p>{item.userAnswer}</p>
      </div>
      <div className={`qa-history-detail-block qa-history-feedback ${item.score >= 0.75 ? 'feedback-good' : 'feedback-low'}`}>
        <div className="qa-history-feedback-header">
          <span className="detail-label">評語</span>
          <span className={`score-badge ${item.score >= 0.75 ? 'score-pass' : 'score-fail'}`}>
            {(item.score * 100).toFixed(0)} 分
          </span>
        </div>
        <p className="feedback-text">{item.feedbackText}</p>
        {item.clarificationQuestion && (
          <p className="clarification">💬 {item.clarificationQuestion}</p>
        )}
      </div>
    </div>
  );
}

export function QuestionPanel({ onSubmit }: Props) {
  const currentQuestion = useSessionStore((s) => s.currentQuestion);
  const lastFeedback = useSessionStore((s) => s.lastFeedback);
  const lastDecision = useSessionStore((s) => s.lastDecision);
  const courseCompleted = useSessionStore((s) => s.courseCompleted);
  const isAwaitingFeedback = useSessionStore((s) => s.isAwaitingFeedback);
  const pendingNextQuestion = useSessionStore((s) => s.pendingNextQuestion);
  const proceedToNextQuestion = useSessionStore((s) => s.proceedToNextQuestion);
  const qaHistory = useSessionStore((s) => s.qaHistory);
  const [answer, setAnswer] = useState('');
  const [showHistory, setShowHistory] = useState(false);
  const [selectedHistoryIdx, setSelectedHistoryIdx] = useState<number | null>(null);

  if (courseCompleted) {
    return (
      <div className="question-panel completed">
        <h3>恭喜完成所有學習階段！</h3>
        <p>你已透過蘇格拉底式問答，深入理解了這份學習材料的所有內容。</p>
      </div>
    );
  }

  if (!currentQuestion && !lastDecision && !lastFeedback) {
    return (
      <div className="question-panel waiting">
        <p>講解完成後將出現問題...</p>
      </div>
    );
  }

  const handleSubmit = () => {
    if (!answer.trim() || !currentQuestion || isAwaitingFeedback) return;
    onSubmit(currentQuestion.question_id, answer.trim());
    setAnswer('');
  };

  const toggleHistoryItem = (idx: number) => {
    setSelectedHistoryIdx(selectedHistoryIdx === idx ? null : idx);
  };

  return (
    <div className="question-panel">
      {qaHistory.length > 0 && (
        <div className="qa-history-section">
          <button
            className="qa-history-toggle"
            onClick={() => {
              setShowHistory(!showHistory);
              if (showHistory) setSelectedHistoryIdx(null);
            }}
          >
            <span>已回答 {qaHistory.length} 題</span>
            <span>{showHistory ? '▲' : '▼'}</span>
          </button>

          {showHistory && (
            <div className="qa-history-list">
              {qaHistory.map((item, idx) => (
                <div key={item.questionId}>
                  <button
                    className={`qa-history-item ${selectedHistoryIdx === idx ? 'qa-history-item-selected' : ''}`}
                    onClick={() => toggleHistoryItem(idx)}
                  >
                    <span className="history-idx">{idx + 1}</span>
                    <span className="question-type" style={{ fontSize: '11px' }}>
                      {typeLabel[item.questionType] ?? '問題'}
                    </span>
                    <span className="history-question-text">
                      {item.questionText.length > 45
                        ? item.questionText.slice(0, 45) + '…'
                        : item.questionText}
                    </span>
                    <span className={`score-badge ${item.score >= 0.75 ? 'score-pass' : 'score-fail'}`} style={{ fontSize: '13px' }}>
                      {(item.score * 100).toFixed(0)} 分
                    </span>
                  </button>
                  {selectedHistoryIdx === idx && <HistoryDetail item={item} />}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {lastDecision && !lastFeedback && (
        <div className={`decision-banner decision-${lastDecision.decision}`}>
          {lastDecision.message}
        </div>
      )}

      {lastFeedback ? (
        <div className={`feedback-card ${lastFeedback.score >= 0.75 ? 'feedback-good' : 'feedback-low'}`}>
          <div className="feedback-card-header">
            <span className="feedback-label">評分結果</span>
            <span className={`score-badge ${lastFeedback.score >= 0.75 ? 'score-pass' : 'score-fail'}`}>
              {(lastFeedback.score * 100).toFixed(0)} 分
            </span>
          </div>
          <p className="feedback-text">{lastFeedback.feedback_text}</p>
          {lastFeedback.clarification_question && (
            <p className="clarification">💬 {lastFeedback.clarification_question}</p>
          )}
          <div className="feedback-card-footer">
            {pendingNextQuestion ? (
              <button className="btn-primary btn-proceed" onClick={proceedToNextQuestion}>
                繼續下一題 →
              </button>
            ) : (
              <span className="feedback-hint">評估進度中，請稍候...</span>
            )}
          </div>
        </div>
      ) : currentQuestion ? (
        <>
          <div className="question-header">
            <span className="question-type">{typeLabel[currentQuestion.type] ?? '問題'}</span>
            <span className="attempt-badge">第 {currentQuestion.attempt_number} 次</span>
          </div>
          <div className="question-text">{currentQuestion.text}</div>

          <textarea
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            placeholder="請用自己的話回答..."
            rows={4}
            disabled={isAwaitingFeedback}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && e.ctrlKey) handleSubmit();
            }}
          />

          <div className="answer-actions">
            <span className="hint-text">
              {isAwaitingFeedback ? 'AI 正在評估中，請稍候...' : 'Ctrl + Enter 提交'}
            </span>
            <button
              className="btn-primary"
              onClick={handleSubmit}
              disabled={!answer.trim() || isAwaitingFeedback}
            >
              {isAwaitingFeedback ? '評估中...' : '提交答案'}
            </button>
          </div>
        </>
      ) : null}
    </div>
  );
}
