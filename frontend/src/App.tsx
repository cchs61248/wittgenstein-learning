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
import { verifyAuth } from './api/auth';
import type { BookEntry } from './api/session';
import type { ServerMessage, ProviderType, DepthType } from './types/messages';
import { LearningStatsPage } from './components/LearningStatsPage';
import { BookshelfPanel } from './components/BookshelfPanel';
import './App.css';

function generateSessionId() {
  return 'sess_' + Math.random().toString(36).slice(2, 11);
}

// 穩定合併書櫃：既有項目保持原位，只把真正新的推到最上面
function mergeBookshelf(existing: BookEntry[], fresh: BookEntry[]): BookEntry[] {
  const freshMap = new Map(fresh.map(e => [e.sessionId, e]));
  const existingIds = new Set(existing.map(e => e.sessionId));
  const newItems = fresh.filter(e => !existingIds.has(e.sessionId));
  const updatedExisting = existing
    .filter(e => freshMap.has(e.sessionId) || e.status === 'generating')
    .map(e => {
      const freshEntry = freshMap.get(e.sessionId);
      if (!freshEntry) return e; // 仍在生成中，DB 尚無記錄
      // generating 期間保留本地描述性標題，避免被 DB stub 的占位標題覆蓋
      if (freshEntry.status === 'generating') return { ...freshEntry, title: e.title };
      return freshEntry;
    });
  return [...newItems, ...updatedExisting];
}

export default function App() {
  const { token, email, clearAuth } = useSessionStore();
  const {
    setSession,
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
    finalizeStageExplanation,
    endExplanationLoading,
    setPendingAnswer,
    setQaHistory,
    addTutorMessage,
    setTutorLoading,
    isTutorLoading,
    hydrateSnapshot,
    hydrateDecisionHistory,
  } = useSessionStore();

  const isExplanationLoading = useSessionStore((s) => s.isExplanationLoading);

  const [bookshelf, setBookshelf] = useState<BookEntry[]>([]);
  const [showUpload, setShowUpload] = useState(false);
  const [kickedMessage, setKickedMessage] = useState<string | null>(null);
  const [activePage, setActivePage] = useState<'learn' | 'stats'>('learn');
  const [isStageSidebarCollapsed, setIsStageSidebarCollapsed] = useState(false);
  const [isAskTutorCollapsed, setIsAskTutorCollapsed] = useState(false);
  const [isQuestionPanelCollapsed, setIsQuestionPanelCollapsed] = useState(false);
  const [isSessionLoading, setIsSessionLoading] = useState(false);
  const [isWaitingForCurrentGeneration, setIsWaitingForCurrentGeneration] = useState(false);
  const [bgPendingMap, setBgPendingMap] = useState<{
    nodes: { node_id: string; stage_id: number; title: string }[];
    summary: string;
  } | null>(null);
  const wsRef = useRef<LearningWebSocket | null>(null);
  const bgWsRef = useRef<LearningWebSocket | null>(null);
  const bgSessionIdRef = useRef<string | null>(null);
  const sessionIdRef = useRef<string>(generateSessionId());
  const activeProviderRef = useRef<string>('claude');
  const activeModelRef = useRef<string | undefined>(undefined);
  const bgProviderRef = useRef<string>('claude');
  const bgModelRef = useRef<string | undefined>(undefined);
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
    if (!token) {
      setBookshelf([]);
      clearSession();
      return;
    }
    // 必須在 clearSession 之前讀取，clearSession 會移除 wl_session_id
    const lastSessionId = localStorage.getItem('wl_session_id');
    setBookshelf([]);
    clearSession(); // 切換帳號前清空舊帳號的 session state
    let cancelled = false;
    setIsSessionLoading(true);
    const getSession = lastSessionId
      ? getSessionDetail(token, lastSessionId).then(s => s ?? getActiveSession(token))
      : getActiveSession(token);

    Promise.all([getSession, listSessions(token)]).then(([session, books]) => {
      if (cancelled) return;
      setIsSessionLoading(false);
      setBookshelf(books);
      if (!session) {
        const generatingEntry = books.find((b) => b.status === 'generating');
        if (generatingEntry) {
          sessionIdRef.current = generatingEntry.sessionId;
          localStorage.setItem('wl_session_id', generatingEntry.sessionId);
          setIsWaitingForCurrentGeneration(true);
          setShowUpload(false);
          return;
        }
        setShowUpload(true);
        return;
      }

      const savedSessionId = session.session_id;
      activeProviderRef.current = session.provider || localStorage.getItem('wl_provider') || 'claude';
      activeModelRef.current = session.model || localStorage.getItem('wl_model') || undefined;
      sessionIdRef.current = savedSessionId;
      localStorage.setItem('wl_session_id', savedSessionId);

      if (session.status === 'generating') {
        // ContentSplitter 仍在執行，輪詢會偵測轉換；isWaitingForCurrentGeneration 觸發自動顯示地圖
        setIsWaitingForCurrentGeneration(true);
      } else if (session.status === 'pending_confirmation' && session.pending_map) {
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
        useSessionStore.getState().beginExplanationLoading(useSessionStore.getState().currentStageId);

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
      bgWsRef.current?.close();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // 週期驗證 token：若其他裝置重新登入造成 token 失效，這端自動回登入頁
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    const runCheck = async () => {
      const ok = await verifyAuth(token);
      if (cancelled || ok) return;
      const logoutMessage = '你已在其他裝置登入，此裝置已登出。';
      setKickedMessage(logoutMessage);
      wsRef.current?.close();
      bgWsRef.current?.close();
      wsRef.current = null;
      bgWsRef.current = null;
      clearSession();
      clearAuth();
    };
    runCheck();
    const timer = setInterval(runCheck, 8000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [token, clearAuth, clearSession]);

  // 書櫃有「生成中」項目時輪詢，頁面重整後也能自動追蹤狀態轉換
  const hasGenerating = bookshelf.some(b => b.status === 'generating');
  useEffect(() => {
    if (!hasGenerating || !token) return;
    const id = setInterval(() => {
      listSessions(token).then(fresh =>
        setBookshelf(prev => mergeBookshelf(prev, fresh))
      );
    }, 5000);
    return () => clearInterval(id);
  }, [hasGenerating, token]);

  // 當前 session 從「生成中」轉為「待確認」時，自動顯示知識地圖（處理重整後的情境）
  useEffect(() => {
    if (!isWaitingForCurrentGeneration || !token) return;
    const currentSid = sessionIdRef.current;
    const entry = bookshelf.find(b => b.sessionId === currentSid);
    if (!entry) return;

    if (entry.status === 'pending_confirmation') {
      setIsWaitingForCurrentGeneration(false);
      getSessionDetail(token, currentSid).then(session => {
        if (!session || session.status !== 'pending_confirmation' || !session.pending_map) return;
        activeProviderRef.current = session.provider || 'claude';
        activeModelRef.current = session.model || undefined;
        setPendingMap(session.pending_map);
        wsRef.current?.close();
        const ws = new LearningWebSocket(currentSid, token, {
          onMessage: handleMessage,
          onOpen: () => setConnected(true),
          onClose: () => setConnected(false),
        });
        ws.connect();
        wsRef.current = ws;
      });
    } else if (entry.status !== 'generating') {
      // 生成失敗（abandoned 等），退回上傳畫面
      setIsWaitingForCurrentGeneration(false);
      setShowUpload(true);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bookshelf, isWaitingForCurrentGeneration, token]);

  const handleMessage = (msg: ServerMessage) => {
    switch (msg.type) {
      case 'session_generating':
        // 與當前前景 session 一致時維持 loading（雙重保險，避免極短 race 漏設）
        if (msg.payload.session_id === sessionIdRef.current) {
          setIsWaitingForCurrentGeneration(true);
        }
        // 前景模式：stub 已建立，若書櫃中尚無此項目（例如使用者直接發送 start_session）則補上
        setBookshelf(prev => {
          const sid = msg.payload.session_id;
          if (prev.some(b => b.sessionId === sid)) return prev;
          return [{
            sessionId: sid,
            title: '生成中…',
            status: 'generating' as const,
            totalStages: 0,
            completedStages: 0,
            updatedAt: null,
          }, ...prev];
        });
        break;
      case 'knowledge_map':
        setPendingMap({ nodes: msg.payload.nodes, summary: msg.payload.summary });
        listSessions(token!).then(fresh => setBookshelf(prev => mergeBookshelf(prev, fresh)));
        break;
      case 'session_started':
        setSession(msg.payload.session_id, msg.payload.stages, msg.payload.stage_statuses);
        useSessionStore.getState().beginExplanationLoading(useSessionStore.getState().currentStageId);
        listSessions(token!).then(fresh => setBookshelf(prev => mergeBookshelf(prev, fresh)));
        break;
      case 'explanation_chunk': {
        const st = useSessionStore.getState();
        if (!msg.payload.is_final) {
          if (!st.isExplanationLoading) {
            st.beginExplanationLoading(st.currentStageId);
          }
        } else {
          setExplanationComplete();
        }
        break;
      }
      case 'explanation_complete':
        finalizeStageExplanation(msg.payload.stage_id, msg.payload.full_explanation);
        break;
      case 'explanation_reset':
        resetExplanation();
        useSessionStore.getState().beginExplanationLoading(useSessionStore.getState().currentStageId);
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
        {
          const dec = msg.payload.decision;
          // 在後端送出任何 explanation_chunk 之前就先進入 loading，避免仍看到問老師／答題區
          if (dec === 'retry' || dec === 'remediate' || dec === 'reteach') {
            useSessionStore.getState().beginExplanationLoading(useSessionStore.getState().currentStageId);
          }
          if (dec === 'advance' && msg.payload.next_stage_id !== null) {
            useSessionStore.getState().beginExplanationLoading(msg.payload.next_stage_id);
          }
        }
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
      case 'session_snapshot': {
        const curStageId = useSessionStore.getState().currentStageId;
        const filteredExpl: Record<number, string> = {};
        for (const [k, v] of Object.entries(msg.payload.stage_explanations)) {
          const sid = Number(k);
          if (curStageId === null || sid !== curStageId) {
            filteredExpl[sid] = v;
          }
        }
        hydrateSnapshot({
          stageExplanations: filteredExpl,
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
      }
      case 'tutor_reply':
        addTutorMessage(msg.payload);
        break;
      case 'kicked':
        wsRef.current?.close();
        bgWsRef.current?.close();
        wsRef.current = null;
        bgWsRef.current = null;
        setKickedMessage(msg.payload.message);
        clearSession();
        clearAuth();
        break;
      case 'course_completed':
        endExplanationLoading();
        setPendingCourseComplete(true);
        break;
      case 'error':
        console.error('Server error:', msg.payload.message);
        endExplanationLoading();
        // resume 或啟動失敗且尚未進入任何 stage，退回上傳畫面
        if (!stagesRef.current.length) {
          setIsWaitingForCurrentGeneration(false);
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

    // 背景模式：當前已有學習中的 session，新材料在背景生成，不中斷現有學習
    if (stagesRef.current.length > 0) {
      const newSid = generateSessionId();
      bgWsRef.current?.close();
      bgSessionIdRef.current = newSid;
      setBgPendingMap(null);
      bgProviderRef.current = provider;
      bgModelRef.current = model || undefined;

      const bgWs = new LearningWebSocket(newSid, token, {
        onMessage: (msg) => {
          if (msg.type === 'session_generating') {
            // stub 已建立，樂觀佔位已存在，輪詢會自動追蹤後續狀態
          } else if (msg.type === 'knowledge_map') {
            const kmap = { nodes: msg.payload.nodes, summary: msg.payload.summary };
            setBgPendingMap(kmap);
            // 此時 session 已在 DB，以真實資料取代樂觀佔位
            listSessions(token!).then(fresh => setBookshelf(prev => mergeBookshelf(prev, fresh)));
            // 若用戶已主動切換到這個 bg session（點了書本後等待），直接顯示知識地圖
            if (sessionIdRef.current === newSid) {
              setPendingMap(kmap);
              setBgPendingMap(null);
              const ws = new LearningWebSocket(newSid, token!, {
                onMessage: handleMessage,
                onOpen: () => setConnected(true),
                onClose: () => setConnected(false),
              });
              ws.connect();
              wsRef.current = ws;
              bgWsRef.current?.close();
              bgWsRef.current = null;
              bgSessionIdRef.current = null;
              setIsWaitingForCurrentGeneration(false);
            }
          } else if (msg.type === 'session_started') {
            listSessions(token!).then(fresh => setBookshelf(prev => mergeBookshelf(prev, fresh)));
          } else if (msg.type === 'error') {
            bgWsRef.current?.close();
            bgWsRef.current = null;
            bgSessionIdRef.current = null;
            setBgPendingMap(null);
            // 移除樂觀佔位
            setBookshelf((prev) => prev.filter((b) => b.sessionId !== newSid));
          } else if (msg.type === 'kicked') {
            wsRef.current?.close();
            bgWsRef.current?.close();
            wsRef.current = null;
            bgWsRef.current = null;
            clearSession();
            clearAuth();
          }
        },
        onOpen: () => {
          bgWs.send({
            type: 'start_session',
            payload: { content, uploaded_file_id: uploadedFileId, provider, target_depth: depth, question_mode: questionMode, model },
          });
        },
        onClose: () => {},
      });
      bgWs.connect();
      bgWsRef.current = bgWs;

      // 立刻樂觀新增書本到最前面，讓使用者知道材料正在生成，不等 DB
      setBookshelf((prev) => [
        {
          sessionId: newSid,
          title: '新材料生成中…',
          status: 'generating' as const,
          totalStages: 0,
          completedStages: 0,
          updatedAt: null,
        },
        ...prev,
      ]);
      setShowUpload(false);
      return;
    }

    // 前景模式：沒有進行中的 session，正常啟動
    activeProviderRef.current = provider;
    activeModelRef.current = model || undefined;

    wsRef.current?.close();
    clearSession();
    const newSid = generateSessionId();
    sessionIdRef.current = newSid;
    localStorage.setItem('wl_session_id', newSid); // 讓重整後能定位到正確 session
    // 立刻進入「分析教材」loading，不等 session_generating／重整後 REST
    setIsWaitingForCurrentGeneration(true);

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
    listSessions(token).then(fresh => setBookshelf(prev => mergeBookshelf(prev, fresh)));
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
    setIsWaitingForCurrentGeneration(false);

    const isBgSession = entry.sessionId === bgSessionIdRef.current;

    // bgSession 且知識地圖已在記憶體中 → 直接切換，不需重新 fetch
    if (isBgSession && bgPendingMap) {
      bgWsRef.current?.close();
      bgWsRef.current = null;
      const sid = entry.sessionId;
      const pendingMapData = bgPendingMap;
      bgSessionIdRef.current = null;
      setBgPendingMap(null);

      wsRef.current?.close();
      clearSession();
      activeProviderRef.current = bgProviderRef.current;
      activeModelRef.current = bgModelRef.current;
      sessionIdRef.current = sid;
      localStorage.setItem('wl_session_id', sid);
      setPendingMap(pendingMapData);
      const ws = new LearningWebSocket(sid, token!, {
        onMessage: handleMessage,
        onOpen: () => setConnected(true),
        onClose: () => setConnected(false),
      });
      ws.connect();
      wsRef.current = ws;
      return;
    }

    // bgSession 仍在生成中 → 切換到等待狀態，讓重整後能追蹤；主 WS 斷開，學習繼續由輪詢等待
    if (isBgSession && entry.status === 'generating') {
      wsRef.current?.close();
      clearSession();
      sessionIdRef.current = entry.sessionId;
      localStorage.setItem('wl_session_id', entry.sessionId);
      setIsWaitingForCurrentGeneration(true);
      return;
    }

    // 一般切換（非 bgSession，或 bgSession 的地圖記憶體遺失但 DB 已完成）
    if (isBgSession) {
      bgWsRef.current?.close();
      bgWsRef.current = null;
      bgSessionIdRef.current = null;
      setBgPendingMap(null);
    }

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
      useSessionStore.getState().beginExplanationLoading(useSessionStore.getState().currentStageId);
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
    if (sessionId === bgSessionIdRef.current) {
      bgWsRef.current?.close();
      bgWsRef.current = null;
      bgSessionIdRef.current = null;
      setBgPendingMap(null);
    }
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

  const kickedModal = kickedMessage ? (
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
  ) : null;

  if (!token) {
    return (
      <>
        <AuthForm />
        {kickedModal}
      </>
    );
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
              setBookshelf([]);
              wsRef.current?.close();
              bgWsRef.current?.close();
              clearSession();
              clearAuth();
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
                disableNewMaterial={hasGenerating}
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
            isWaitingForCurrentGeneration ? (
              <div className="generating-wait-state">
                <div className="generating-wait-spinner" />
                <p className="generating-wait-title">AI 正在分析教材</p>
                <p className="generating-wait-hint">完成後將自動顯示知識地圖，請稍候…</p>
              </div>
            ) : (
              <>
                {isExplanationLoading ? (
                  <div className="explanation-panel explanation-panel-loading" role="status" aria-live="polite">
                    <div className="explanation-panel-loading-inner">
                      <div className="generating-wait-spinner" />
                      <p className="generating-wait-title">AI 正在生成本章講解</p>
                      <p className="generating-wait-hint">完成後將自動顯示全文與題目，請稍候…</p>
                    </div>
                  </div>
                ) : (
                  <ExplanationPanel />
                )}
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
            )
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

      {kickedModal}
    </div>
  );
}
