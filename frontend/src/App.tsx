import { useEffect, useRef, useState } from 'react';
import { useSessionStore } from './store/sessionStore';
import { AuthForm } from './components/AuthForm';
import { UploadModal } from './components/UploadModal';
import { KnowledgeMapModal } from './components/KnowledgeMapModal';
import { ExplanationPanel } from './components/ExplanationPanel';
import { QuestionPanel } from './components/QuestionPanel';
import { AskTutorPanel } from './components/AskTutorPanel';
import { LearningWebSocket } from './api/websocket';
import { getActiveSession, listSessions, getSessionDetail, renameSession, deleteSession } from './api/session';
import type { BookEntry } from './api/session';
import type { ServerMessage, ProviderType, DepthType } from './types/messages';
import { LearningStatsPage } from './components/LearningStatsPage';
import { BookshelfPanel } from './components/BookshelfPanel';
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
    setQuestionImmediate,
    setFeedback,
    setRecoveredFeedback,
    setDecision,
    pushDecisionHistory,
    setPendingAdvance,
    setPendingCourseComplete,
    setConnected,
    setPendingMap,
    pendingMap,
    resetExplanation,
    clearSession,
    stages,
    setAwaitingFeedback,
    storeStageExplanation,
    setPendingAnswer,
    setQaHistory,
    addTutorMessage,
    setTutorLoading,
    isTutorLoading,
    hydrateSnapshot,
    hydrateDecisionHistory,
  } = useSessionStore();

  const [bookshelf, setBookshelf] = useState<BookEntry[]>([]);
  const [showUpload, setShowUpload] = useState(false);
  const [kickedMessage, setKickedMessage] = useState<string | null>(null);
  const [activePage, setActivePage] = useState<'learn' | 'stats'>('learn');
  const [isStageSidebarCollapsed, setIsStageSidebarCollapsed] = useState(false);
  const [isAskTutorCollapsed, setIsAskTutorCollapsed] = useState(false);
  const [isQuestionPanelCollapsed, setIsQuestionPanelCollapsed] = useState(false);
  const [isSessionLoading, setIsSessionLoading] = useState(false);
  const wsRef = useRef<LearningWebSocket | null>(null);
  const sessionIdRef = useRef<string>(generateSessionId());
  const activeProviderRef = useRef<string>('claude');
  const activeModelRef = useRef<string | undefined>(undefined);
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

    Promise.all([getActiveSession(token), listSessions(token)]).then(([session, books]) => {
      if (cancelled) return;
      setIsSessionLoading(false);
      setBookshelf(books);
      if (!session) {
        setShowUpload(true);
        return;
      }

      const savedSessionId = session.session_id;
      activeProviderRef.current = session.provider || localStorage.getItem('wl_provider') || 'claude';
      activeModelRef.current = session.model || localStorage.getItem('wl_model') || undefined;
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
            ws.send({
              type: 'resume_session',
              payload: { session_id: savedSessionId, provider: activeProviderRef.current, model: activeModelRef.current },
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
      case 'resume_state':
        if (msg.payload.last_feedback) {
          setRecoveredFeedback(msg.payload.last_feedback);
        }
        if (msg.payload.current_question) {
          setQuestionImmediate(msg.payload.current_question);
        }
        break;
      case 'stage_decision':
        pushDecisionHistory(msg.payload);
        setDecision(msg.payload);
        if (msg.payload.decision === 'advance') {
          if (msg.payload.next_stage_id !== null) {
            setPendingAdvance(msg.payload.next_stage_id);
          }
          // next_stage_id === null (最後一章) 等 course_completed 處理
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
      case 'session_snapshot':
        hydrateSnapshot({
          stageExplanations: Object.fromEntries(
            Object.entries(msg.payload.stage_explanations).map(([k, v]) => [Number(k), v])
          ),
          stageQaHistories: Object.fromEntries(
            Object.entries(msg.payload.stage_qa_histories).map(([stageId, records]) => [
              Number(stageId),
              records.map((r) => ({
                questionId: r.question_id,
                questionText: r.question_text,
                questionType: r.question_type,
                userAnswer: r.user_answer,
                score: r.score,
                feedbackText: r.feedback_text,
              })),
            ])
          ),
        });
        if (msg.payload.decision_history) {
          hydrateDecisionHistory(
            msg.payload.decision_history.map((h) => ({
              at: h.created_at,
              decision: h.decision,
              stageId: h.stage_id,
              stageTitle: h.strategy_snapshot?.current_stage_title ?? '',
              bestScore: h.best_score,
              nextStageId: h.next_stage_id,
              nextStageScore: h.next_stage_score,
              candidates: h.strategy_snapshot?.next_stage_candidates ?? [],
            }))
          );
        }
        break;
      case 'tutor_reply':
        addTutorMessage(msg.payload);
        break;
      case 'kicked':
        wsRef.current?.close();
        wsRef.current = null;
        setKickedMessage(msg.payload.message);
        break;
      case 'course_completed':
        setPendingCourseComplete(true);
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
    questionMode: 'short_answer' | 'multiple_choice',
    uploadedFileId?: string,
    content?: string
  ) => {
    if (!token) return;

    localStorage.setItem('wl_provider', provider);
    localStorage.setItem('wl_model', model);
    activeProviderRef.current = provider;
    activeModelRef.current = model || undefined;

    wsRef.current?.close();
    clearSession();
    const newSid = generateSessionId();
    sessionIdRef.current = newSid;

    const ws = new LearningWebSocket(newSid, token, {
      onMessage: handleMessage,
      onOpen: () => {
        setConnected(true);
        ws.send({
          type: 'start_session',
          payload: { content, uploaded_file_id: uploadedFileId, provider, target_depth: depth, question_mode: questionMode, model },
        });
      },
      onClose: () => setConnected(false),
    });
    ws.connect();
    wsRef.current = ws;
    setShowUpload(false);
    listSessions(token).then(setBookshelf);
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

  const handleAskTutor = (question: string) => {
    setTutorLoading(true);
    wsRef.current?.send({
      type: 'ask_tutor',
      payload: { session_id: sessionIdRef.current, question },
    });
  };

  const handleSwitchSession = async (entry: BookEntry) => {
    if (entry.sessionId === sessionIdRef.current) return;
    wsRef.current?.close();
    clearSession();
    const session = await getSessionDetail(token!, entry.sessionId);
    if (!session) return;

    const sid = session.session_id;
    activeProviderRef.current = session.provider || 'claude';
    activeModelRef.current = session.model || undefined;
    sessionIdRef.current = sid;
    localStorage.setItem('wl_session_id', sid);

    if (session.status === 'pending_confirmation' && session.pending_map) {
      setPendingMap(session.pending_map);
      const ws = new LearningWebSocket(sid, token!, {
        onMessage: handleMessage,
        onOpen: () => setConnected(true),
        onClose: () => setConnected(false),
      });
      ws.connect();
      wsRef.current = ws;
    } else {
      setSession(sid, session.stages, session.stage_statuses);
      const ws = new LearningWebSocket(sid, token!, {
        onMessage: handleMessage,
        onOpen: () => {
          setConnected(true);
          ws.send({
            type: 'resume_session',
            payload: { session_id: sid, provider: activeProviderRef.current, model: activeModelRef.current },
          });
        },
        onClose: () => setConnected(false),
      });
      ws.connect();
      wsRef.current = ws;
    }
  };

  const handleDeleteBook = async (sessionId: string) => {
    await deleteSession(token!, sessionId);
    setBookshelf((prev) => prev.filter((b) => b.sessionId !== sessionId));
    if (sessionId === sessionIdRef.current) {
      wsRef.current?.close();
      clearSession();
      setShowUpload(true);
    }
  };

  const handleRenameBook = async (sessionId: string, title: string) => {
    await renameSession(token!, sessionId, title);
    setBookshelf((prev) =>
      prev.map((b) => (b.sessionId === sessionId ? { ...b, title } : b))
    );
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
            <span className="stage-sidebar-toggle-label">書櫃</span>
            <span className="stage-sidebar-toggle-value">{bookshelf.length}</span>
            <span className="stage-sidebar-toggle-icon" aria-hidden="true">{isStageSidebarCollapsed ? '▸' : '◂'}</span>
          </button>
          {!isStageSidebarCollapsed && (
            <div id="stage-map-panel" className="stage-map-panel-inner">
              <BookshelfPanel
                books={bookshelf}
                activeSessionId={sessionIdRef.current}
                onSwitch={handleSwitchSession}
                onNewMaterial={() => setShowUpload(true)}
                onRename={handleRenameBook}
                onDelete={handleDeleteBook}
              />
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

        <main className="main-content">
          <div className="page-tabs" role="tablist">
            <button
              role="tab"
              aria-selected={activePage === 'learn'}
              className={`page-tab${activePage === 'learn' ? ' is-active' : ''}`}
              onClick={() => setActivePage('learn')}
            >
              學習
            </button>
            <button
              role="tab"
              aria-selected={activePage === 'stats'}
              className={`page-tab${activePage === 'stats' ? ' is-active' : ''}`}
              onClick={() => setActivePage('stats')}
            >
              學習成效
            </button>
          </div>
          {activePage === 'learn' ? (
            <>
              <ExplanationPanel />
              <AskTutorPanel
                onAskTutor={handleAskTutor}
                isCollapsed={isAskTutorCollapsed}
                onToggle={() => setIsAskTutorCollapsed((v) => !v)}
                isLoading={isTutorLoading}
              />
              <QuestionPanel
                onSubmit={handleSubmitAnswer}
                isCollapsed={isQuestionPanelCollapsed}
                onToggle={() => setIsQuestionPanelCollapsed((v) => !v)}
              />
            </>
          ) : (
            <LearningStatsPage token={token!} />
          )}
        </main>
      </div>

      {showUpload && !pendingMap && (
        <UploadModal onStart={handleStart} onClose={() => setShowUpload(false)} />
      )}

      {pendingMap && (
        <KnowledgeMapModal
          nodes={pendingMap.nodes}
          summary={pendingMap.summary}
          onConfirm={() => {
            setPendingMap(null);
            setShowUpload(false);
            wsRef.current?.send({
              type: 'confirm_map',
              payload: { provider: activeProviderRef.current, model: activeModelRef.current },
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
