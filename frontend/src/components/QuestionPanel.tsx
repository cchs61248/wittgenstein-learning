import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useSessionStore } from '../store/sessionStore';
import type { QaHistoryItem } from '../store/sessionStore';
import { LearningCoachPanel } from './LearningCoachPanel';

interface Props {
  onSubmit: (questionId: string, answer: string) => void;
  isCollapsed: boolean;
  onToggle: () => void;
}

const normalizeText = (text: string) => text.replace(/\\n/g, '\n');

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
        <div className="feedback-text markdown-content">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{normalizeText(item.feedbackText)}</ReactMarkdown>
        </div>
        {item.clarificationQuestion && (
          <p className="clarification">💬 {item.clarificationQuestion}</p>
        )}
      </div>
    </div>
  );
}

export function QuestionPanel({ onSubmit, isCollapsed, onToggle }: Props) {
  const currentQuestion = useSessionStore((s) => s.currentQuestion);
  const lastFeedback = useSessionStore((s) => s.lastFeedback);
  const lastDecision = useSessionStore((s) => s.lastDecision);
  const courseCompleted = useSessionStore((s) => s.courseCompleted);
  const isAwaitingFeedback = useSessionStore((s) => s.isAwaitingFeedback);
  const pendingNextQuestion = useSessionStore((s) => s.pendingNextQuestion);
  const proceedToNextQuestion = useSessionStore((s) => s.proceedToNextQuestion);
  const qaHistory = useSessionStore((s) => s.qaHistory);
  const selectedStageId = useSessionStore((s) => s.selectedStageId);
  const stageQaHistories = useSessionStore((s) => s.stageQaHistories);
  const stageSourceChunks = useSessionStore((s) => s.stageSourceChunks);
  const [answer, setAnswer] = useState('');
  const [selectedOption, setSelectedOption] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [selectedHistoryIdx, setSelectedHistoryIdx] = useState<number | null>(null);

  const reviewHistory = selectedStageId !== null ? (stageQaHistories[selectedStageId] ?? []) : null;
  const currentStageChunks = currentQuestion ? (stageSourceChunks[currentQuestion.stage_id] ?? []) : [];
  const evidenceDetails = (currentQuestion?.evidence_chunk_ids ?? []).map((chunkId) => ({
    chunkId,
    chunk: currentStageChunks.find((c) => c.chunk_id === chunkId),
  }));

  const handleSubmit = () => {
    if (!currentQuestion || isAwaitingFeedback) return;
    const isMultipleChoice = currentQuestion.answer_mode === 'multiple_choice';
    const finalAnswer = isMultipleChoice ? (selectedOption ?? '') : answer.trim();
    if (!finalAnswer) return;
    onSubmit(currentQuestion.question_id, finalAnswer);
    setAnswer('');
    setSelectedOption(null);
  };

  const toggleHistoryItem = (idx: number) => {
    setSelectedHistoryIdx(selectedHistoryIdx === idx ? null : idx);
  };

  const renderBody = () => {
    if (reviewHistory !== null) {
      if (reviewHistory.length === 0) {
        return <p className="panel-placeholder">此節點無答題記錄</p>;
      }
      return (
        <div className="qa-history-section">
          <div className="qa-history-toggle" style={{ cursor: 'default' }}>
            <span>答題記錄（共 {reviewHistory.length} 題）</span>
          </div>
          <div className="qa-history-list">
            {reviewHistory.map((item, idx) => (
              <div key={item.questionId}>
                <button
                  className={`qa-history-item ${selectedHistoryIdx === idx ? 'qa-history-item-selected' : ''}`}
                  onClick={() => setSelectedHistoryIdx(selectedHistoryIdx === idx ? null : idx)}
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
        </div>
      );
    }

    if (courseCompleted) {
      return (
        <div className="question-panel-completed">
          <h3>恭喜完成所有學習階段！</h3>
          <p>你已透過蘇格拉底式問答，深入理解了這份學習材料的所有內容。</p>
        </div>
      );
    }

    if (!currentQuestion && !lastDecision && !lastFeedback) {
      return <p className="panel-placeholder">講解完成後將出現問題...</p>;
    }

    return (
      <>
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
          <>
            <div className={`decision-banner decision-${lastDecision.decision}`}>
              <p>{lastDecision.message}</p>
              {(lastDecision.reason_lines ?? []).length > 0 && (
                <div className="qa-history-detail-block" style={{ marginTop: 8 }}>
                  <span className="detail-label">路徑判斷依據</span>
                  {(lastDecision.reason_lines ?? []).map((line, idx) => (
                    <p key={`${idx}-${line}`}>- {line}</p>
                  ))}
                </div>
              )}
            </div>
            <LearningCoachPanel decision={lastDecision} />
          </>
        )}

        {lastFeedback ? (
          <div className={`feedback-card ${lastFeedback.score >= 0.75 ? 'feedback-good' : 'feedback-low'}`}>
            <div className="feedback-card-header">
              <span className="feedback-label">評分結果</span>
              <span className={`score-badge ${lastFeedback.score >= 0.75 ? 'score-pass' : 'score-fail'}`}>
                {(lastFeedback.score * 100).toFixed(0)} 分
              </span>
            </div>
            <div className="feedback-text markdown-content">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{normalizeText(lastFeedback.feedback_text)}</ReactMarkdown>
            </div>
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
            {evidenceDetails.length > 0 && (
              <div className="evidence-row">
                <span className="detail-label">來源依據</span>
                <div className="evidence-chip-list">
                  {evidenceDetails.map(({ chunkId, chunk }) => (
                    <span key={chunkId} className="evidence-chip" title={chunk?.quote ?? '此 chunk 尚無摘要'}>
                      [{chunkId}]
                    </span>
                  ))}
                </div>
              </div>
            )}
            {currentQuestion.answer_mode === 'multiple_choice' ? (
              <div className="choice-list">
                {(currentQuestion.options ?? []).map((opt) => (
                  <button
                    key={opt.id}
                    className={`choice-item ${selectedOption === opt.id ? 'choice-item-selected' : ''}`}
                    onClick={() => setSelectedOption(opt.id)}
                    disabled={isAwaitingFeedback}
                  >
                    <span className="choice-id">{opt.id}</span>
                    <span className="choice-text">{opt.text}</span>
                  </button>
                ))}
              </div>
            ) : (
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
            )}
            <div className="answer-actions">
              <span className="hint-text">
                {isAwaitingFeedback ? 'AI 正在評估中，請稍候...' : 'Ctrl + Enter 提交'}
              </span>
              <button
                className="btn-primary"
                onClick={handleSubmit}
                disabled={
                  isAwaitingFeedback ||
                  (currentQuestion.answer_mode === 'multiple_choice'
                    ? !selectedOption
                    : !answer.trim())
                }
              >
                {isAwaitingFeedback ? '評估中...' : '提交答案'}
              </button>
            </div>
          </>
        ) : null}
      </>
    );
  };

  return (
    <div className={`question-panel${isCollapsed ? ' is-collapsed' : ''}`}>
      <div className="collapsible-header">
        <span className="collapsible-title">答題區</span>
        <button className="collapsible-toggle" onClick={onToggle} aria-expanded={!isCollapsed}>
          {isCollapsed ? '展開 ▼' : '收起 ▲'}
        </button>
      </div>
      {!isCollapsed && (
        <div className="question-panel-body">
          {renderBody()}
        </div>
      )}
    </div>
  );
}
