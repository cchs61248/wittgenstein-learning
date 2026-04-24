import { useEffect, useRef, useState } from 'react';
import { useSessionStore } from './store/sessionStore';
import { AuthForm } from './components/AuthForm';
import { UploadModal } from './components/UploadModal';
import { StageMap } from './components/StageMap';
import { ExplanationPanel } from './components/ExplanationPanel';
import { QuestionPanel } from './components/QuestionPanel';
import { LearningWebSocket } from './api/websocket';
import type { ServerMessage, ProviderType, DepthType } from './types/messages';
import './App.css';

function generateSessionId() {
  return 'sess_' + Math.random().toString(36).slice(2, 11);
}

export default function App() {
  const { token, email, clearAuth } = useSessionStore();
  const {
    setSession,
    appendExplanationChunk,
    setExplanationComplete,
    setQuestion,
    setFeedback,
    setDecision,
    advanceStage,
    setConnected,
    setCourseCompleted,
    stages,
  } = useSessionStore();

  const [showUpload, setShowUpload] = useState(true);
  const wsRef = useRef<LearningWebSocket | null>(null);
  const sessionIdRef = useRef<string>(generateSessionId());

  useEffect(() => {
    return () => {
      wsRef.current?.close();
    };
  }, []);

  const handleMessage = (msg: ServerMessage) => {
    switch (msg.type) {
      case 'session_started':
        setSession(msg.payload.session_id, msg.payload.stages);
        break;
      case 'explanation_chunk':
        appendExplanationChunk(msg.payload.chunk);
        if (msg.payload.is_final) setExplanationComplete();
        break;
      case 'explanation_complete':
        setExplanationComplete();
        break;
      case 'question':
        setQuestion(msg.payload);
        break;
      case 'feedback':
        setFeedback(msg.payload);
        break;
      case 'stage_decision':
        setDecision(msg.payload);
        if (msg.payload.decision === 'advance' && msg.payload.next_stage_id !== null) {
          advanceStage(msg.payload.next_stage_id);
        }
        break;
      case 'course_completed':
        setCourseCompleted();
        break;
      case 'error':
        console.error('Server error:', msg.payload.message);
        break;
    }
  };

  const handleStart = (content: string, provider: ProviderType, depth: DepthType) => {
    if (!token) return;

    const sid = sessionIdRef.current;
    const ws = new LearningWebSocket(sid, token, {
      onMessage: handleMessage,
      onOpen: () => {
        setConnected(true);
        ws.send({
          type: 'start_session',
          payload: { content, provider, target_depth: depth },
        });
      },
      onClose: () => setConnected(false),
    });
    ws.connect();
    wsRef.current = ws;
    setShowUpload(false);
  };

  const handleSubmitAnswer = (questionId: string, answer: string) => {
    wsRef.current?.send({
      type: 'submit_answer',
      payload: {
        session_id: sessionIdRef.current,
        question_id: questionId,
        answer,
      },
    });
  };

  if (!token) {
    return <AuthForm />;
  }

  return (
    <div className="app-layout">
      <header className="app-header">
        <h1>維特根斯坦學習系統</h1>
        <div className="header-right">
          <span>{email}</span>
          <button
            onClick={() => {
              clearAuth();
              wsRef.current?.close();
            }}
            className="btn-ghost"
          >
            登出
          </button>
        </div>
      </header>

      <div className="app-body">
        <StageMap />

        <main className="main-content">
          <ExplanationPanel />
          <div className="divider" />
          <QuestionPanel onSubmit={handleSubmitAnswer} />
        </main>
      </div>

      {showUpload && stages.length === 0 && (
        <UploadModal onStart={handleStart} />
      )}
    </div>
  );
}
