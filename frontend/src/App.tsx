import { useEffect, useRef, useState } from 'react';
import { useSessionStore } from './store/sessionStore';
import { AuthForm } from './components/AuthForm';
import { UploadModal } from './components/UploadModal';
import { KnowledgeMapModal } from './components/KnowledgeMapModal';
import { StageMap } from './components/StageMap';
import { ExplanationPanel } from './components/ExplanationPanel';
import { QuestionPanel } from './components/QuestionPanel';
import { LearningWebSocket } from './api/websocket';
import { getActiveSession } from './api/session';
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
    setPendingMap,
    pendingMap,
    resetExplanation,
    clearSession,
    stages,
    setAwaitingFeedback,
    storeStageExplanation,
    setPendingAnswer,
    setQaHistory,
  } = useSessionStore();

  const [showUpload, setShowUpload] = useState(false);
  const [kickedMessage, setKickedMessage] = useState<string | null>(null);
  const [isStageSidebarCollapsed, setIsStageSidebarCollapsed] = useState(false);
  const [isQuestionPanelCollapsed, setIsQuestionPanelCollapsed] = useState(false);
  const [isSessionLoading, setIsSessionLoading] = useState(false);
  const wsRef = useRef<LearningWebSocket | null>(null);
  const sessionIdRef = useRef<string>(generateSessionId());
  const stagesRef = useRef(stages);
  stagesRef.current = stages;

  useEffect(() => {
    const media = window.matchMedia('(max-width: 768px)');
    const applyDefault = (matches: boolean) => setIsStageSidebarCollapsed(matches);
    applyDefault(media.matches);
    const onChange = (e: MediaQueryListEvent) => applyDefault(e.matches);
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }, []);

  // 掛載時：若有 token，先查詢是否有活躍會話；沒有才顯示上傳 modal
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    setIsSessionLoading(true);

    getActiveSession(token).then((session) => {
      if (cancelled) return;
      setIsSessionLoading(false);
      if (!session) {
        setShowUpload(true);
        return;
      }

      const savedSessionId = session.session_id;
      sessionIdRef.current = savedSessionId;
      localStorage.setItem('wl_session_id', savedSessionId);

      if (session.status === 'pending_confirmation' && session.pending_map) {
        // 知識地圖已生成但用戶尚未確認，直接顯示地圖讓用戶確認
        setPendingMap(session.pending_map);
        // 建立 WebSocket 連線，等待用戶確認後發送 confirm_map
        const ws = new LearningWebSocket(savedSessionId, token, {
          onMessage: handleMessage,
          onOpen: () => setConnected(true),
          onClose: () => setConnected(false),
        });
        ws.connect();
        wsRef.current = ws;
      } else {
        // 先從 REST 回應預填 stages，讓新裝置立即看到進度，不卡在空白畫面
        setSession(savedSessionId, session.stages, session.stage_statuses);

        // 正常恢復進行中的學習
        const ws = new LearningWebSocket(savedSessionId, token, {
          onMessage: handleMessage,
          onOpen: () => {
            setConnected(true);
            const savedProvider = localStorage.getItem('wl_provider') || 'claude';
            const savedModel = localStorage.getItem('wl_model') || undefined;
            ws.send({
              type: 'resume_session',
              payload: { session_id: savedSessionId, provider: savedProvider, model: savedModel },
            });
          },
          onClose: () => setConnected(false),
        });
        ws.connect();
        wsRef.current = ws;
      }
    });

    return () => {
      cancelled = true;
      setIsSessionLoading(false);
      wsRef.current?.close();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const handleMessage = (msg: ServerMessage) => {
    switch (msg.type) {
      case 'knowledge_map':
        setPendingMap({ nodes: msg.payload.nodes, summary: msg.payload.summary });
        break;
      case 'session_started':
        setSession(msg.payload.session_id, msg.payload.stages, msg.payload.stage_statuses);
        break;
      case 'explanation_chunk':
        appendExplanationChunk(msg.payload.chunk);
        if (msg.payload.is_final) setExplanationComplete();
        break;
      case 'explanation_complete':
        setExplanationComplete();
        storeStageExplanation(msg.payload.stage_id, msg.payload.full_explanation);
        break;
      case 'explanation_reset':
        resetExplanation();
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
      case 'qa_history':
        setQaHistory(msg.payload.records.map((r) => ({
          questionId: r.question_id,
          questionText: r.question_text,
          questionType: r.question_type,
          userAnswer: r.user_answer,
          score: r.score,
          feedbackText: r.feedback_text,
        })));
        break;
      case 'kicked':
        wsRef.current?.close();
        wsRef.current = null;
        setKickedMessage(msg.payload.message);
        break;
      case 'course_completed':
        setCourseCompleted();
        break;
      case 'error':
        console.error('Server error:', msg.payload.message);
        // resume 或啟動失敗且尚未進入任何 stage，退回上傳畫面
        if (!stagesRef.current.length) {
          setShowUpload(true);
        }
        break;
    }
  };

  const handleStart = (
    provider: ProviderType,
    depth: DepthType,
    model: string,
    uploadedFileId?: string,
    content?: string
  ) => {
    if (!token) return;

    localStorage.setItem('wl_provider', provider);
    localStorage.setItem('wl_model', model);

    wsRef.current?.close();
    const newSid = generateSessionId();
    sessionIdRef.current = newSid;

    const ws = new LearningWebSocket(newSid, token, {
      onMessage: handleMessage,
      onOpen: () => {
        setConnected(true);
        ws.send({
          type: 'start_session',
          payload: { content, uploaded_file_id: uploadedFileId, provider, target_depth: depth, model },
        });
      },
      onClose: () => setConnected(false),
    });
    ws.connect();
    wsRef.current = ws;
    setShowUpload(false);
  };

  const handleSubmitAnswer = (questionId: string, answer: string) => {
    setPendingAnswer(answer);
    setAwaitingFeedback(true);
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

  if (isSessionLoading) {
    return (
      <div className="session-loading">
        <div className="session-loading-spinner" />
        <p>正在載入學習進度…</p>
      </div>
    );
  }

  return (
    <div className="app-layout">
      <header className="app-header">
        <div className="header-brand">
          <span className="brand-mark" aria-hidden="true" />
          <div className="header-brand-text">
            <h1>維特根斯坦學習系統</h1>
            <p className="header-tagline">蘇格拉底式問答 · 陪你真正讀懂</p>
          </div>
        </div>
        <div className="header-right">
          <span className="header-email">{email}</span>
          <button
            onClick={() => {
              wsRef.current?.close();
              wsRef.current = null;
              sessionIdRef.current = generateSessionId();
              clearSession();
              setShowUpload(true);
            }}
            className="btn-ghost"
          >
            新學習
          </button>
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
        <section className={`stage-sidebar${isStageSidebarCollapsed ? ' is-collapsed' : ''}`}>
          <button
            className="stage-sidebar-toggle"
            onClick={() => setIsStageSidebarCollapsed((v) => !v)}
            aria-expanded={!isStageSidebarCollapsed}
            aria-controls="stage-map-panel"
          >
            <span className="stage-sidebar-toggle-label">學習進度</span>
            <span className="stage-sidebar-toggle-value">{stages.filter((s) => s.status === 'completed').length}/{stages.length || 0}</span>
            <span className="stage-sidebar-toggle-icon" aria-hidden="true">{isStageSidebarCollapsed ? '▸' : '◂'}</span>
          </button>
          {!isStageSidebarCollapsed && (
            <div id="stage-map-panel">
              <StageMap hideHeading />
            </div>
          )}
        </section>
        {!isStageSidebarCollapsed && (
          <button
            className="stage-sidebar-backdrop"
            aria-label="關閉學習進度側欄"
            onClick={() => setIsStageSidebarCollapsed(true)}
          />
        )}

        <main className={`main-content${isQuestionPanelCollapsed ? ' is-question-collapsed' : ''}`}>
          <ExplanationPanel />
          <div className="content-splitter">
            <button
              className="question-panel-toggle"
              onClick={() => setIsQuestionPanelCollapsed((v) => !v)}
              aria-expanded={!isQuestionPanelCollapsed}
            >
              {isQuestionPanelCollapsed ? '展開答題區' : '收起答題區'}
            </button>
          </div>
          {!isQuestionPanelCollapsed && <QuestionPanel onSubmit={handleSubmitAnswer} />}
        </main>
      </div>

      {showUpload && stages.length === 0 && !pendingMap && (
        <UploadModal onStart={handleStart} />
      )}

      {pendingMap && (
        <KnowledgeMapModal
          nodes={pendingMap.nodes}
          summary={pendingMap.summary}
          onConfirm={() => {
            setPendingMap(null);
            setShowUpload(false);
            const savedProvider = localStorage.getItem('wl_provider') || 'claude';
            const savedModel = localStorage.getItem('wl_model') || undefined;
            wsRef.current?.send({
              type: 'confirm_map',
              payload: { provider: savedProvider, model: savedModel },
            });
          }}
        />
      )}

      {kickedMessage && (
        <div className="modal-overlay kicked-overlay">
          <div className="modal-card kicked-card">
            <h2>連線已中斷</h2>
            <p>{kickedMessage}</p>
            <button
              className="btn-primary btn-large"
              onClick={() => {
                setKickedMessage(null);
                clearAuth();
              }}
            >
              前往登入頁面
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
