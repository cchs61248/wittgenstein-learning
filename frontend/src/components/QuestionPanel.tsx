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
  const [answer, setAnswer] = useState('');
  const [submitting, setSubmitting] = useState(false);

  if (courseCompleted) {
    return (
      <div className="question-panel completed">
        <h3>恭喜完成所有學習階段！</h3>
        <p>你已透過蘇格拉底式問答，深入理解了這份學習材料的所有內容。</p>
      </div>
    );
  }

  if (!currentQuestion && !lastDecision) {
    return (
      <div className="question-panel waiting">
        <p>講解完成後將出現問題...</p>
      </div>
    );
  }

  const handleSubmit = async () => {
    if (!answer.trim() || !currentQuestion) return;
    setSubmitting(true);
    await onSubmit(currentQuestion.question_id, answer.trim());
    setAnswer('');
    setSubmitting(false);
  };

  return (
    <div className="question-panel">
      {lastDecision && (
        <div className={`decision-banner decision-${lastDecision.decision}`}>
          {lastDecision.message}
        </div>
      )}

      {currentQuestion && (
        <>
          <div className="question-header">
            <span className="question-type">{typeLabel[currentQuestion.type] ?? '問題'}</span>
            <span className="attempt-badge">第 {currentQuestion.attempt_number} 次</span>
          </div>
          <div className="question-text">{currentQuestion.text}</div>

          {lastFeedback && (
            <div className={`feedback-box score-${lastFeedback.score >= 0.75 ? 'good' : 'low'}`}>
              <div className="feedback-score">
                得分：{(lastFeedback.score * 100).toFixed(0)}%
              </div>
              <p>{lastFeedback.feedback_text}</p>
              {lastFeedback.clarification_question && (
                <p className="clarification">追問：{lastFeedback.clarification_question}</p>
              )}
            </div>
          )}

          <textarea
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            placeholder="請用自己的話回答..."
            rows={4}
            disabled={submitting}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && e.ctrlKey) handleSubmit();
            }}
          />

          <div className="answer-actions">
            <span className="hint-text">Ctrl + Enter 提交</span>
            <button
              className="btn-primary"
              onClick={handleSubmit}
              disabled={!answer.trim() || submitting}
            >
              {submitting ? '評估中...' : '提交答案'}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
