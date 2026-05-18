import { useCallback, useEffect, useRef, useState } from 'react';
import { useSessionStore } from './store/sessionStore';
import { AuthForm } from './components/AuthForm';
import { UploadModal } from './components/UploadModal';
import { KnowledgeMapModal } from './components/KnowledgeMapModal';
import { ExplanationPanel } from './components/ExplanationPanel';
import { QuestionPanel } from './components/QuestionPanel';
import { AskTutorPanel } from './components/AskTutorPanel';
import { LearningWebSocket } from './api/websocket';
import {
  getActiveSession,
  listSessions,
  getSessionDetail,
  renameSession,
  deleteSession,
  syntheticGeneratingSession,
} from './api/session';
import { verifyAuth } from './api/auth';
import { fetchUserUiState } from './api/userUiState';
import { applyServerUiStateToLocal, dispatchUiStateSyncedFromServer } from './utils/userUiStateSync';
import type { BookEntry } from './api/session';
import type { ServerMessage, ProviderType, DepthType } from './types/messages';
import { LearningStatsPage } from './components/LearningStatsPage';
import { BookshelfPanel } from './components/BookshelfPanel';
import { ThemeToggle } from './components/ThemeToggle';
import './App.css';
import {
  getSessionLayoutPrefs,
  patchSessionLayoutPrefs,
  removeSessionLayoutPrefs,
  readInitialChromeFromStorage,
} from './utils/sessionLayoutPrefs';
import { reconcileBookshelf, prependBookToBookshelf, saveBookOrder } from './utils/bookshelfOrder';

function generateSessionId() {
  return 'sess_' + Math.random().toString(36).slice(2, 11);
}

// React 18 strict mode 在 dev 下會 mount→unmount→mount，init useEffect 第一次的
// clearSession() 會清掉 wl_session_id，導致第二次 mount 讀到 null 後 fallback 到
// getActiveSession（拿到「最近 active」未完成 session，無視使用者實際選的 completed session）。
// 在 module load 時讀一次當下值，跨 strict-mode 兩次 mount 都用這個 snapshot。
const initialSessionIdSnapshot: string | null =
  typeof window !== 'undefined' ? localStorage.getItem('wl_session_id') : null;

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
    setReconnectAttempt,
    setReconnectGaveUp,
    setPendingMap,
    pendingMap,
    resetExplanation,
    clearSession,
    stages,
    finalizeStageExplanation,
    endExplanationLoading,
    setQaHistory,
    addTutorMessage,
    appendTutorChunk,
    clearStreamingTutor,
    commitStreamingTutorAsCancelled,
    setTutorHistories,
    isTutorLoading,
    setPendingTutor,
    clearPendingTutor,
    setPendingSubmit,
    clearPendingSubmit,
    hydrateSnapshot,
    hydrateDecisionHistory,
  } = useSessionStore();

  const isExplanationLoading = useSessionStore((s) => s.isExplanationLoading);
  const isRetryLoading = useSessionStore((s) => s.isRetryLoading);
  const reconnectAttempt = useSessionStore((s) => s.reconnectAttempt);
  const reconnectGaveUp = useSessionStore((s) => s.reconnectGaveUp);
  const selectedStageId = useSessionStore((s) => s.selectedStageId);
  const currentStageId = useSessionStore((s) => s.currentStageId);
  /** 僅在「視角為正在生成的那一章」時全螢幕 loading；回顧其他章（含本地尚無快取全文）一律走主欄 */
  const showFullExplanationLoading =
    isExplanationLoading &&
    (selectedStageId === null || selectedStageId === currentStageId);

  const [bookshelf, setBookshelf] = useState<BookEntry[]>([]);
  const [showUpload, setShowUpload] = useState(false);
  const [kickedMessage, setKickedMessage] = useState<string | null>(null);
  const [activePage, setActivePage] = useState<'learn' | 'stats'>(() => readInitialChromeFromStorage().activePage);
  const [isStageSidebarCollapsed, setIsStageSidebarCollapsed] = useState(
    () => readInitialChromeFromStorage().stageSidebarCollapsed
  );
  const [isAskTutorCollapsed, setIsAskTutorCollapsed] = useState(() => readInitialChromeFromStorage().askTutorCollapsed);
  const [isQuestionPanelCollapsed, setIsQuestionPanelCollapsed] = useState(
    () => readInitialChromeFromStorage().questionCollapsed
  );
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
  useEffect(() => {
    stagesRef.current = stages;
  }, [stages]);
  const handleMessageRef = useRef<(msg: ServerMessage) => void>(() => {});
  const explanationScrollRef = useRef<HTMLDivElement | null>(null);
  const statsScrollRef = useRef<HTMLDivElement | null>(null);
  const storeSessionId = useSessionStore((s) => s.sessionId);

  /** 不含捲動位置，避免與捲動還原 effect 競態覆寫已存捲動 */
  const persistChromeLayoutForSession = useCallback(
    (sid: string | null) => {
      if (!sid) return;
      patchSessionLayoutPrefs(sid, {
        askTutorCollapsed: isAskTutorCollapsed,
        questionCollapsed: isQuestionPanelCollapsed,
        stageSidebarCollapsed: isStageSidebarCollapsed,
        activePage,
        selectedStageId: useSessionStore.getState().selectedStageId,
      });
    },
    [isAskTutorCollapsed, isQuestionPanelCollapsed, isStageSidebarCollapsed, activePage]
  );

  const persistLayoutForSession = useCallback((sid: string | null) => {
    if (!sid) return;
    patchSessionLayoutPrefs(sid, {
      askTutorCollapsed: isAskTutorCollapsed,
      questionCollapsed: isQuestionPanelCollapsed,
      learnScrollTop: explanationScrollRef.current?.scrollTop,
      statsScrollTop: statsScrollRef.current?.scrollTop,
      stageSidebarCollapsed: isStageSidebarCollapsed,
      activePage,
      selectedStageId: useSessionStore.getState().selectedStageId,
    });
  }, [isAskTutorCollapsed, isQuestionPanelCollapsed, isStageSidebarCollapsed, activePage]);

  const applyLayoutForSession = useCallback((sid: string | null) => {
    if (!sid) return;
    const p = getSessionLayoutPrefs(sid);
    setIsAskTutorCollapsed(p?.askTutorCollapsed ?? false);
    setIsQuestionPanelCollapsed(p?.questionCollapsed ?? false);
    setIsStageSidebarCollapsed(
      p?.stageSidebarCollapsed !== undefined
        ? p.stageSidebarCollapsed
        : window.matchMedia('(max-width: 768px)').matches
    );
    setActivePage(p?.activePage === 'stats' ? 'stats' : 'learn');
    useSessionStore.getState().setSelectedStage(p?.selectedStageId ?? null);
  }, []);

  useEffect(() => {
    if (!storeSessionId || isSessionLoading) return;
    persistChromeLayoutForSession(storeSessionId);
  }, [
    storeSessionId,
    selectedStageId,
    isAskTutorCollapsed,
    isQuestionPanelCollapsed,
    isStageSidebarCollapsed,
    activePage,
    isSessionLoading,
    persistChromeLayoutForSession,
  ]);

  useEffect(() => {
    if (!storeSessionId || activePage !== 'learn') return;
    if (isWaitingForCurrentGeneration) return;
    const top = getSessionLayoutPrefs(storeSessionId)?.learnScrollTop ?? 0;
    const id = requestAnimationFrame(() => {
      if (explanationScrollRef.current) explanationScrollRef.current.scrollTop = top;
    });
    return () => cancelAnimationFrame(id);
  }, [
    storeSessionId,
    activePage,
    isExplanationLoading,
    isWaitingForCurrentGeneration,
    stages.length,
    isSessionLoading,
  ]);

  useEffect(() => {
    if (!storeSessionId || activePage !== 'learn' || isWaitingForCurrentGeneration) return;
    const el = explanationScrollRef.current;
    if (!el) return;
    let tid: ReturnType<typeof setTimeout> | undefined;
    const onScroll = () => {
      clearTimeout(tid);
      tid = setTimeout(() => {
        patchSessionLayoutPrefs(storeSessionId, { learnScrollTop: el.scrollTop });
      }, 200);
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      el.removeEventListener('scroll', onScroll);
      clearTimeout(tid);
    };
  }, [storeSessionId, activePage, isExplanationLoading, isWaitingForCurrentGeneration]);

  useEffect(() => {
    const media = window.matchMedia('(max-width: 768px)');
    const onChange = (e: MediaQueryListEvent) => {
      const sid = useSessionStore.getState().sessionId;
      if (!sid) {
        setIsStageSidebarCollapsed(e.matches);
        return;
      }
      const p = getSessionLayoutPrefs(sid);
      if (p?.stageSidebarCollapsed === undefined) {
        setIsStageSidebarCollapsed(e.matches);
      }
    };
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
    // 用 module-level snapshot 跨 React 18 strict-mode 兩次 mount。第一次 mount 的
    // clearSession() 會清掉 localStorage.wl_session_id，第二次 mount 若直接 getItem
    // 會拿到 null，導致 fallback 到 getActiveSession() 拿錯誤的 session。
    const lastSessionId = initialSessionIdSnapshot;
    setBookshelf([]);
    clearSession(); // 切換帳號前清空舊帳號的 session state
    let cancelled = false;
    setIsSessionLoading(true);

    Promise.all([
      lastSessionId ? getSessionDetail(token, lastSessionId) : Promise.resolve(null),
      listSessions(token),
      fetchUserUiState(token),
    ]).then(async ([detail, books, serverUi]) => {
      if (cancelled) return;
      if (serverUi) {
        applyServerUiStateToLocal(serverUi);
        dispatchUiStateSyncedFromServer();
      }
      let session = detail;
      if (!session && lastSessionId) {
        const entry = books.find((b) => b.sessionId === lastSessionId && b.status === 'generating');
        if (entry) {
          session = syntheticGeneratingSession(lastSessionId);
        }
      }
      if (!session) {
        session = await getActiveSession(token);
      }

      if (cancelled) return;
      setIsSessionLoading(false);
      setBookshelf(reconcileBookshelf([], books));
      if (!session) {
        const generatingEntry = books.find((b) => b.status === 'generating');
        if (generatingEntry) {
          sessionIdRef.current = generatingEntry.sessionId;
          localStorage.setItem('wl_session_id', generatingEntry.sessionId);
          useSessionStore.setState({ sessionId: generatingEntry.sessionId, stages: [], currentStageId: null });
          setIsWaitingForCurrentGeneration(true);
          setShowUpload(false);
          applyLayoutForSession(generatingEntry.sessionId);
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
        useSessionStore.setState({ sessionId: savedSessionId, stages: [], currentStageId: null });
        setIsWaitingForCurrentGeneration(true);
        applyLayoutForSession(savedSessionId);
      } else if (session.status === 'pending_confirmation' && session.pending_map) {
        // 知識地圖已生成但用戶尚未確認，直接顯示地圖讓用戶確認
        useSessionStore.setState({ sessionId: savedSessionId, stages: [], currentStageId: null });
        setPendingMap(session.pending_map);
        // 建立 WebSocket 連線，等待用戶確認後發送 confirm_map
        const ws = new LearningWebSocket(savedSessionId, token, {
          onMessage: (msg) => handleMessageRef.current(msg),
          onOpen: () => setConnected(true),
          onClose: () => setConnected(false),
          onReconnecting: (n) => setReconnectAttempt(n),
          onReconnected: () => {
            setReconnectAttempt(null);
            setReconnectGaveUp(false);
            setConnected(true);
            ws.send({
              type: 'resume_session',
              payload: { session_id: savedSessionId, provider: activeProviderRef.current, model: activeModelRef.current },
            });
          },
          onGiveUp: () => {
            setReconnectAttempt(null);
            setReconnectGaveUp(true);
          },
        });
        ws.connect();
        wsRef.current = ws;
        applyLayoutForSession(savedSessionId);
      } else {
        // 先從 REST 回應預填 stages，讓新裝置立即看到進度，不卡在空白畫面
        setSession(savedSessionId, session.stages, session.stage_statuses);
        useSessionStore.getState().beginExplanationLoading(useSessionStore.getState().currentStageId);
        applyLayoutForSession(savedSessionId);

        // 正常恢復進行中的學習
        const ws = new LearningWebSocket(savedSessionId, token, {
          onMessage: (msg) => handleMessageRef.current(msg),
          onOpen: () => {
            setConnected(true);
            ws.send({
              type: 'resume_session',
              payload: { session_id: savedSessionId, provider: activeProviderRef.current, model: activeModelRef.current },
            });
          },
          onClose: () => setConnected(false),
          onReconnecting: (n) => setReconnectAttempt(n),
          onReconnected: () => {
            setReconnectAttempt(null);
            setReconnectGaveUp(false);
            setConnected(true);
            ws.send({
              type: 'resume_session',
              payload: { session_id: savedSessionId, provider: activeProviderRef.current, model: activeModelRef.current },
            });
          },
          onGiveUp: () => {
            setReconnectAttempt(null);
            setReconnectGaveUp(true);
          },
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
      const status = await verifyAuth(token);
      if (cancelled) return;
      // 網路問題（fetch reject / 5xx）：不處理，留給 WS 重連邏輯與下次 tick；
      // 只有 server 明確回 401/403 才視為 token 失效並登出。
      if (status !== 'invalid') return;
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
        setBookshelf(prev => reconcileBookshelf(prev, fresh))
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
          onMessage: (msg) => handleMessageRef.current(msg),
          onOpen: () => setConnected(true),
          onClose: () => setConnected(false),
          onReconnecting: (n) => setReconnectAttempt(n),
          onReconnected: () => {
            setReconnectAttempt(null);
            setReconnectGaveUp(false);
            setConnected(true);
            ws.send({
              type: 'resume_session',
              payload: { session_id: currentSid, provider: activeProviderRef.current, model: activeModelRef.current },
            });
          },
          onGiveUp: () => {
            setReconnectAttempt(null);
            setReconnectGaveUp(true);
          },
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
        setBookshelf((prev) => {
          const sid = msg.payload.session_id;
          if (prev.some((b) => b.sessionId === sid)) return prev;
          return prependBookToBookshelf(prev, {
            sessionId: sid,
            title: '生成中…',
            status: 'generating' as const,
            totalStages: 0,
            completedStages: 0,
            updatedAt: null,
          });
        });
        break;
      case 'knowledge_map':
        setPendingMap({ nodes: msg.payload.nodes, summary: msg.payload.summary });
        listSessions(token!).then(fresh => setBookshelf(prev => reconcileBookshelf(prev, fresh)));
        break;
      case 'session_started': {
        setSession(msg.payload.session_id, msg.payload.stages, msg.payload.stage_statuses);
        useSessionStore.getState().setCurrentGenerationId(null);
        const stateAfter = useSessionStore.getState();
        const curSid = stateAfter.currentStageId;
        const statusMap = msg.payload.stage_statuses ?? {};
        const curStageStatus = curSid !== null ? statusMap[String(curSid)] : undefined;
        // 已完成的 stage 不開 loading — 否則 beginExplanationLoading 會把 stageExplanations[curSid]
        // 從 cache 清掉，導致「返回當前學習」後顯示空白「等待學習開始」畫面
        if (stateAfter.pendingAdvanceStageId === null && curStageStatus !== 'completed') {
          stateAfter.beginExplanationLoading(curSid);
        }
        listSessions(token!).then(fresh => setBookshelf(prev => reconcileBookshelf(prev, fresh)));
        break;
      }
      case 'explanation_chunk': {
        const st = useSessionStore.getState();
        const gid = msg.payload.generation_id;
        // 過時 generation 的 chunk 丟棄（避免 stale loading state）
        if (gid && st.currentGenerationId && gid !== st.currentGenerationId) {
          break;
        }
        // 第一次見到 generation_id 就鎖定它
        if (gid && !st.currentGenerationId) {
          st.setCurrentGenerationId(gid);
        }
        if (!msg.payload.is_final && st.pendingAdvanceStageId === null) {
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
          if (dec === 'retry') {
            useSessionStore.getState().beginRetryLoading(useSessionStore.getState().currentStageId);
          }
          if (
            (dec === 'advance' || dec === 'remediate' || dec === 'reteach') &&
            msg.payload.next_stage_id !== null
          ) {
            setPendingAdvance(msg.payload.next_stage_id);
          }
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
        useSessionStore.getState().setCurrentGenerationId(null);
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
              reasonLines: h.reason_lines,
              strategySnapshot: h.strategy_snapshot,
              candidates: h.strategy_snapshot?.next_stage_candidates ?? [],
            }))
          );
        }
        const tutorHistoriesRaw = msg.payload.tutor_histories ?? {};
        const tutorHistoriesMap: Record<number, { question: string; answer: string; in_scope?: boolean }[]> = {};
        for (const [k, v] of Object.entries(tutorHistoriesRaw)) {
          tutorHistoriesMap[Number(k)] = v;
        }
        setTutorHistories(tutorHistoriesMap);
        // 頁面重整後恢復 pending answer 狀態
        const { pendingAnswerQuestionId, pendingAnswer: pendingAns } = useSessionStore.getState();
        if (pendingAnswerQuestionId !== null && pendingAns !== null) {
          const allQaRaw = Object.values(msg.payload.stage_qa_histories as Record<string, { question_id: string }[]>).flat();
          if (allQaRaw.some((r) => r.question_id === pendingAnswerQuestionId)) {
            clearPendingSubmit(); // 後端已在重整前處理完
          } else {
            // 同 ask_tutor：in-flight feedback 仍會送達此連線，等 30 秒再重送
            setTimeout(() => {
              const { pendingAnswerQuestionId: stillQId, pendingAnswer: stillAns } = useSessionStore.getState();
              if (stillQId !== null && stillAns !== null) {
                wsRef.current?.send({
                  type: 'submit_answer',
                  payload: { session_id: sessionIdRef.current, question_id: stillQId, answer: stillAns },
                });
              }
            }, 30000);
          }
        }
        // 頁面重整後恢復 pending tutor 狀態
        const { pendingTutorQuestion, pendingTutorStageId } = useSessionStore.getState();
        if (pendingTutorQuestion !== null) {
          const stageHistory = pendingTutorStageId !== null ? (tutorHistoriesMap[pendingTutorStageId] ?? []) : [];
          if (stageHistory.some((item) => item.question === pendingTutorQuestion)) {
            clearPendingTutor(); // 後端已在重整前處理完，直接清除
          } else {
            // 後端 emit 走 ws_manager（指向最新連線），in-flight 回覆仍會送達此連線。
            // 等 30 秒再重送，讓多數 LLM 回覆有足夠時間先到達並清除 pending，
            // 萬一重送導致後端第二次 emit，addTutorMessage 的冪等判斷會防止重複顯示。
            setTimeout(() => {
              const { pendingTutorQuestion: stillPending, pendingTutorStageId: stillStageId } = useSessionStore.getState();
              if (stillPending !== null) {
                wsRef.current?.send({
                  type: 'ask_tutor',
                  payload: { session_id: sessionIdRef.current, question: stillPending, stage_id: stillStageId },
                });
              }
            }, 30000);
          }
        }
        break;
      }
      case 'tutor_chunk':
        appendTutorChunk(msg.payload);
        break;
      case 'tutor_reply':
        clearStreamingTutor();
        addTutorMessage(msg.payload);
        break;
      case 'generation_cancelled':
        if (msg.payload.kind === 'ask_tutor') {
          commitStreamingTutorAsCancelled();
        }
        // start_session/confirm_map/submit_answer/resume_session 取消後：partial
        // explanation 由後端 DebouncedExplanationWriter 的 finally 寫入 DB；
        // 前端只需停 loading 動畫，UI 端的 streaming buffer 會在下一個 explanation_chunk
        // 或 resume_session snapshot 中自然被覆蓋/補回。
        endExplanationLoading();
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
        listSessions(token!).then(fresh => setBookshelf(prev => reconcileBookshelf(prev, fresh)));
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

  useEffect(() => {
    handleMessageRef.current = handleMessage;
  });

  const handleStart = (
    provider: ProviderType,
    depth: DepthType,
    model: string,
    questionMode: 'short_answer' | 'multiple_choice',
    sources: Array<{ type: string; file_id?: string; content?: string; label: string }>
  ) => {
    if (!token) return;

    localStorage.setItem('wl_provider', provider);
    localStorage.setItem('wl_model', model);

    const startPayload = {
      sources,
      provider,
      target_depth: depth,
      question_mode: questionMode,
      model,
    };

    // 背景模式：當前已有學習中的 session，新材料在背景生成，不中斷現有學習
    if (stagesRef.current.length > 0) {
      const newSid = generateSessionId();
      bgWsRef.current?.close();
      bgSessionIdRef.current = newSid;
      setBgPendingMap(null);
      bgProviderRef.current = provider;
      bgModelRef.current = model || undefined;

      // bgWs 故意不重連 — 短暫存在；listSessions 輪詢處理連線中斷後的恢復
      const bgWs = new LearningWebSocket(newSid, token, {
        onMessage: (msg) => {
          if (msg.type === 'session_generating') {
            // stub 已建立，樂觀佔位已存在，輪詢會自動追蹤後續狀態
          } else if (msg.type === 'knowledge_map') {
            const kmap = { nodes: msg.payload.nodes, summary: msg.payload.summary };
            setBgPendingMap(kmap);
            // 此時 session 已在 DB，以真實資料取代樂觀佔位
            listSessions(token!).then(fresh => setBookshelf(prev => reconcileBookshelf(prev, fresh)));
            // 若用戶已主動切換到這個 bg session（點了書本後等待），直接顯示知識地圖
            if (sessionIdRef.current === newSid) {
              setPendingMap(kmap);
              setBgPendingMap(null);
              const ws = new LearningWebSocket(newSid, token!, {
                onMessage: (msg) => handleMessageRef.current(msg),
                onOpen: () => setConnected(true),
                onClose: () => setConnected(false),
                onReconnecting: (n) => setReconnectAttempt(n),
                onReconnected: () => {
                  setReconnectAttempt(null);
                  setReconnectGaveUp(false);
                  setConnected(true);
                  ws.send({
                    type: 'resume_session',
                    payload: { session_id: newSid, provider: activeProviderRef.current, model: activeModelRef.current },
                  });
                },
                onGiveUp: () => {
                  setReconnectAttempt(null);
                  setReconnectGaveUp(true);
                },
              });
              ws.connect();
              wsRef.current = ws;
              bgWsRef.current?.close();
              bgWsRef.current = null;
              bgSessionIdRef.current = null;
              setIsWaitingForCurrentGeneration(false);
            }
          } else if (msg.type === 'session_started') {
            listSessions(token!).then(fresh => setBookshelf(prev => reconcileBookshelf(prev, fresh)));
          } else if (msg.type === 'error') {
            bgWsRef.current?.close();
            bgWsRef.current = null;
            bgSessionIdRef.current = null;
            setBgPendingMap(null);
            // 移除樂觀佔位
            setBookshelf((prev) => {
              const n = prev.filter((b) => b.sessionId !== newSid);
              saveBookOrder(n);
              return n;
            });
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
          bgWs.send({ type: 'start_session', payload: startPayload });
        },
        onClose: () => {},
      });
      bgWs.connect();
      bgWsRef.current = bgWs;

      // 立刻樂觀新增書本到最前面，讓使用者知道材料正在生成，不等 DB
      setBookshelf((prev) =>
        prependBookToBookshelf(prev, {
          sessionId: newSid,
          title: '新材料生成中…',
          status: 'generating' as const,
          totalStages: 0,
          completedStages: 0,
          updatedAt: null,
        })
      );
      setShowUpload(false);
      return;
    }

    // 前景模式：沒有進行中的 session，正常啟動
    activeProviderRef.current = provider;
    activeModelRef.current = model || undefined;

    persistLayoutForSession(sessionIdRef.current);
    wsRef.current?.close();
    clearSession();
    clearPendingTutor();
    clearPendingSubmit();
    const newSid = generateSessionId();
    sessionIdRef.current = newSid;
    localStorage.setItem('wl_session_id', newSid); // 讓重整後能定位到正確 session
    // 立刻進入「分析教材」loading，不等 session_generating／重整後 REST
    setIsWaitingForCurrentGeneration(true);

    const ws = new LearningWebSocket(newSid, token, {
      onMessage: (msg) => handleMessageRef.current(msg),
      onOpen: () => {
        setConnected(true);
        ws.send({ type: 'start_session', payload: startPayload });
      },
      onClose: () => setConnected(false),
      onReconnecting: (n) => setReconnectAttempt(n),
      onReconnected: () => {
        setReconnectAttempt(null);
        setReconnectGaveUp(false);
        setConnected(true);
        // 重連時 session 已在 DB，改送 resume_session 重建記憶體狀態
        ws.send({
          type: 'resume_session',
          payload: { session_id: newSid, provider: activeProviderRef.current, model: activeModelRef.current },
        });
      },
      onGiveUp: () => {
        setReconnectAttempt(null);
        setReconnectGaveUp(true);
      },
    });
    ws.connect();
    wsRef.current = ws;
    setShowUpload(false);
    listSessions(token).then(fresh => setBookshelf(prev => reconcileBookshelf(prev, fresh)));
  };

  const handleSubmitAnswer = (questionId: string, answer: string) => {
    setPendingSubmit(questionId, answer);
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
    const stageId = selectedStageId ?? currentStageId;
    setPendingTutor(question, stageId);
    wsRef.current?.send({
      type: 'ask_tutor',
      payload: { session_id: sessionIdRef.current, question, stage_id: stageId },
    });
  };

  const handleCancelTutor = () => {
    if (!sessionIdRef.current) return;
    wsRef.current?.send({
      type: 'cancel_generation',
      payload: { key: `${sessionIdRef.current}:tutor` },
    });
  };

  const handleSwitchSession = async (entry: BookEntry) => {
    if (entry.sessionId === sessionIdRef.current) return;
    clearPendingTutor();
    clearPendingSubmit();
    persistLayoutForSession(sessionIdRef.current);
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
      useSessionStore.setState({ sessionId: sid, stages: [], currentStageId: null });
      setPendingMap(pendingMapData);
      const ws = new LearningWebSocket(sid, token!, {
        onMessage: (msg) => handleMessageRef.current(msg),
        onOpen: () => setConnected(true),
        onClose: () => setConnected(false),
      });
      ws.connect();
      wsRef.current = ws;
      applyLayoutForSession(sid);
      return;
    }

    // bgSession 仍在生成中 → 切換到等待狀態，讓重整後能追蹤；主 WS 斷開，學習繼續由輪詢等待
    if (isBgSession && entry.status === 'generating') {
      wsRef.current?.close();
      clearSession();
      sessionIdRef.current = entry.sessionId;
      localStorage.setItem('wl_session_id', entry.sessionId);
      useSessionStore.setState({ sessionId: entry.sessionId, stages: [], currentStageId: null });
      setIsWaitingForCurrentGeneration(true);
      applyLayoutForSession(entry.sessionId);
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
    let session = await getSessionDetail(token!, entry.sessionId);
    if (!session && entry.status === 'generating') {
      session = syntheticGeneratingSession(entry.sessionId);
    }
    if (!session) return;

    const sid = session.session_id;
    activeProviderRef.current = session.provider || 'claude';
    activeModelRef.current = session.model || undefined;
    sessionIdRef.current = sid;
    localStorage.setItem('wl_session_id', sid);

    if (session.status === 'generating') {
      useSessionStore.setState({ sessionId: sid, stages: [], currentStageId: null });
      setIsWaitingForCurrentGeneration(true);
      applyLayoutForSession(sid);
      return;
    }

    if (session.status === 'pending_confirmation' && session.pending_map) {
      useSessionStore.setState({ sessionId: sid, stages: [], currentStageId: null });
      setPendingMap(session.pending_map);
      const ws = new LearningWebSocket(sid, token!, {
        onMessage: (msg) => handleMessageRef.current(msg),
        onOpen: () => setConnected(true),
        onClose: () => setConnected(false),
      });
      ws.connect();
      wsRef.current = ws;
      applyLayoutForSession(sid);
    } else {
      setSession(sid, session.stages, session.stage_statuses);
      useSessionStore.getState().beginExplanationLoading(useSessionStore.getState().currentStageId);
      const ws = new LearningWebSocket(sid, token!, {
        onMessage: (msg) => handleMessageRef.current(msg),
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
      applyLayoutForSession(sid);
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
    removeSessionLayoutPrefs(sessionId);
    setBookshelf((prev) => {
      const n = prev.filter((b) => b.sessionId !== sessionId);
      saveBookOrder(n);
      return n;
    });
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
      {(reconnectAttempt !== null || reconnectGaveUp) && (
        <div
          className="reconnect-banner"
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            zIndex: 1000,
            padding: '8px 16px',
            background: reconnectGaveUp ? '#c0392b' : '#f39c12',
            color: 'white',
            textAlign: 'center',
            fontSize: 14,
          }}
          role="status"
          aria-live="polite"
        >
          {reconnectGaveUp
            ? '連線中斷且自動重連失敗，請手動重新整理頁面。'
            : `連線中斷，正在重新連線…（第 ${reconnectAttempt} 次）`}
        </div>
      )}
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
          <ThemeToggle />
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

      <div
        className={`app-body${isStageSidebarCollapsed ? ' is-stage-sidebar-collapsed' : ''}`}
      >
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
                activeSessionId={storeSessionId}
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
                {showFullExplanationLoading ? (
                  <div
                    ref={explanationScrollRef}
                    className="explanation-panel explanation-panel-loading"
                    role="status"
                    aria-live="polite"
                  >
                    <div className="explanation-panel-loading-inner">
                      <div className="generating-wait-spinner" />
                      {isRetryLoading ? (
                        <>
                          <p className="generating-wait-title">AI 正在生成新一輪練習題</p>
                          <p className="generating-wait-hint">根據本次作答，正針對需要補強的概念重新出題，請稍候…</p>
                        </>
                      ) : (
                        <>
                          <p className="generating-wait-title">AI 正在生成本章講解</p>
                          <p className="generating-wait-hint">完成後將自動顯示全文與題目，請稍候…</p>
                        </>
                      )}
                    </div>
                  </div>
                ) : (
                  <ExplanationPanel ref={explanationScrollRef} />
                )}
                <AskTutorPanel
                  currentStageId={selectedStageId ?? currentStageId}
                  onAskTutor={handleAskTutor}
                  onCancel={handleCancelTutor}
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
            <LearningStatsPage ref={statsScrollRef} token={token!} sessionId={storeSessionId} />
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
