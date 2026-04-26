import { useState } from 'react';
import { useSessionStore } from '../store/sessionStore';

interface Props {
  onSubmit: (questionId: string, answer: string) => void;
}

const typeLabel: Record<string, string> = {
  apply: '應用型',
  understand: '理解型',
  create: '創作型',
};

export function QuestionPanel({ onSubmit }: Props) {
  const currentQuestion = useSessionStore((s) => s.currentQuestion);
  const lastFeedback = useSessionStore((s) => s.lastFeedback);
  const lastDecision = useSessionStore((s) => s.lastDecision);
  const courseCompleted = useSessionStore((s) => s.courseCompleted);
  const isAwaitingFeedback = useSessionStore((s) => s.isAwaitingFeedback);
  const pendingNextQuestion = useSessionStore((s) => s.pendingNextQuestion);
  const proceedToNextQuestion = useSessionStore((s) => s.proceedToNextQuestion);
  const [answer, setAnswer] = useState('');

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

  return (
    <div className="question-panel">
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
