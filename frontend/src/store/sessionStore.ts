import { create } from 'zustand';
import type { StageInfo, QuestionPayload, FeedbackPayload, StageDecisionPayload, KnowledgeMapNode, KnowledgeMapPayload, SourceChunk, TutorMessage, TutorReplyPayload } from '../types/messages';

export type StageStatus = 'pending' | 'current' | 'completed';

interface StageWithStatus extends StageInfo {
  status: StageStatus;
}

export interface QaHistoryItem {
  questionId: string;
  questionText: string;
  questionType: 'apply' | 'understand' | 'create';
  userAnswer: string;
  score: number;
  feedbackText: string;
  clarificationQuestion?: string | null;
}

interface SessionState {
  // 認證
  token: string | null;
  userId: string | null;
  email: string | null;
  role: string | null;
  setAuth: (token: string, userId: string, email: string, role: string) => void;
  clearAuth: () => void;

  // 會話
  sessionId: string | null;
  stages: StageWithStatus[];
  currentStageId: number | null;
  currentGenerationId: string | null;
  setCurrentGenerationId: (id: string | null) => void;
  setSession: (sessionId: string, stages: StageInfo[], stageStatuses?: Record<string, string>) => void;

  // 講解
  explanationText: string;
  isStreaming: boolean;
  /** 講解由後端串流產生中：前端不顯示逐字串流，僅顯示 loading 至 explanation_complete */
  isExplanationLoading: boolean;
  /** 目前 loading 是因 retry 決策觸發（用於顯示「重新出題中」文字） */
  isRetryLoading: boolean;
  beginExplanationLoading: (stageId: number | null) => void;
  beginRetryLoading: (stageId: number | null) => void;
  endExplanationLoading: () => void;
  appendExplanationChunk: (chunk: string) => void;
  setExplanationComplete: () => void;
  stageExplanations: Record<number, string>;
  stageSourceChunks: Record<number, SourceChunk[]>;
  storeStageExplanation: (stageId: number, text: string) => void;
  /** 講解段落結束：寫入該章完整 Markdown 並清空串流緩衝，避免與已存檔內容重疊顯示 */
  finalizeStageExplanation: (stageId: number, full: string) => void;
  selectedStageId: number | null;
  setSelectedStage: (id: number | null) => void;

  // 問答
  currentQuestion: QuestionPayload | null;
  lastFeedback: FeedbackPayload | null;
  lastDecision: StageDecisionPayload | null;
  decisionHistory: Array<{
    at: string;
    decision: StageDecisionPayload['decision'];
    stageId: number | null;
    stageTitle: string;
    bestScore: number;
    nextStageId: number | null;
    nextStageScore?: number | null;
    candidates?: {
      stage_id: number;
      title: string;
      score: number;
      is_dynamic?: boolean;
      kind?: string;
      source_stage_id?: number;
    }[];
  }>;
  stageDecisions: Record<number, StageDecisionPayload>;
  isAwaitingFeedback: boolean;
  pendingNextQuestion: QuestionPayload | null;
  pendingAnswer: string | null;
  pendingAnswerQuestionId: string | null;
  setPendingSubmit: (questionId: string, answer: string) => void;
  clearPendingSubmit: () => void;
  qaHistory: QaHistoryItem[];
  stageQaHistories: Record<number, QaHistoryItem[]>;
  stageQuestions: Record<number, QuestionPayload>;
  tutorReply: TutorMessage | null;
  tutorHistory: Record<number, TutorMessage[]>;
  isTutorLoading: boolean;
  setTutorLoading: (v: boolean) => void;
  pendingTutorQuestion: string | null;
  pendingTutorStageId: number | null;
  setPendingTutor: (question: string, stageId: number | null) => void;
  clearPendingTutor: () => void;
  streamingTutorQuestion: string | null;
  streamingTutorStageId: number | null;
  streamingTutorAnswer: string;
  appendTutorChunk: (payload: { chunk: string; stage_id: number; question: string }) => void;
  clearStreamingTutor: () => void;
  commitStreamingTutorAsCancelled: () => void;
  addTutorMessage: (msg: TutorReplyPayload) => void;
  setTutorHistories: (map: Record<number, TutorMessage[]>) => void;
  deleteTutorMessage: (stageId: number, recordId: number) => void;
  setQuestion: (q: QuestionPayload) => void;
  setQuestionImmediate: (q: QuestionPayload | null) => void;
  setFeedback: (f: FeedbackPayload) => void;
  setRecoveredFeedback: (f: FeedbackPayload | null) => void;
  setDecision: (d: StageDecisionPayload) => void;
  pushDecisionHistory: (d: StageDecisionPayload) => void;
  setAwaitingFeedback: (v: boolean) => void;
  setPendingAnswer: (answer: string) => void;
  setQaHistory: (records: QaHistoryItem[]) => void;
  setTutorReply: (reply: TutorMessage | null) => void;
  clearTutorHistory: () => void;
  hydrateSnapshot: (snapshot: { stageExplanations: Record<number, string>; stageQaHistories: Record<number, QaHistoryItem[]> }) => void;
  /** 合併單章答題紀錄（例如 REST 回顧載入），寫入 localStorage */
  mergeStageQaHistory: (stageId: number, records: QaHistoryItem[]) => void;
  hydrateDecisionHistory: (history: Array<{
    at: string;
    decision: StageDecisionPayload['decision'];
    stageId: number | null;
    stageTitle: string;
    bestScore: number;
    nextStageId: number | null;
    nextStageScore?: number | null;
    reasonLines?: string[];
    strategySnapshot?: StageDecisionPayload['strategy_snapshot'];
    candidates?: {
      stage_id: number;
      title: string;
      score: number;
      is_dynamic?: boolean;
      kind?: string;
      source_stage_id?: number;
    }[];
  }>) => void;
  proceedToNextQuestion: () => void;
  advanceStage: (nextStageId: number | null) => void;
  pendingAdvanceStageId: number | null;
  setPendingAdvance: (id: number | null) => void;
  pendingCourseComplete: boolean;
  setPendingCourseComplete: (v: boolean) => void;

  // 知識地圖確認
  pendingMap: KnowledgeMapPayload | null;
  setPendingMap: (map: KnowledgeMapPayload | null) => void;

  // UI 狀態
  isConnected: boolean;
  setConnected: (v: boolean) => void;
  reconnectAttempt: number | null;
  setReconnectAttempt: (n: number | null) => void;
  reconnectGaveUp: boolean;
  setReconnectGaveUp: (v: boolean) => void;
  courseCompleted: boolean;
  setCourseCompleted: () => void;
  resetExplanation: () => void;
  clearSession: () => void;
}

function loadStageExplanations(): Record<number, string> {
  try {
    const raw = localStorage.getItem('wl_stage_explanations');
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function loadStageQaHistories(): Record<number, QaHistoryItem[]> {
  try {
    const raw = localStorage.getItem('wl_stage_qa_histories');
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function loadDecisionHistory() {
  try {
    const raw = localStorage.getItem('wl_decision_history');
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function loadStageDecisions(): Record<number, StageDecisionPayload> {
  try {
    const raw = localStorage.getItem('wl_stage_decisions');
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

const DECISION_HISTORY_MAX = 200;

export const useSessionStore = create<SessionState>((set) => ({
  token: localStorage.getItem('wl_token'),
  userId: localStorage.getItem('wl_user_id'),
  email: localStorage.getItem('wl_email'),
  role: localStorage.getItem('wl_role'),
  setAuth: (token, userId, email, role) => {
    localStorage.setItem('wl_token', token);
    localStorage.setItem('wl_user_id', userId);
    localStorage.setItem('wl_email', email);
    localStorage.setItem('wl_role', role);
    set({ token, userId, email, role });
  },
  clearAuth: () => {
    localStorage.removeItem('wl_token');
    localStorage.removeItem('wl_user_id');
    localStorage.removeItem('wl_email');
    localStorage.removeItem('wl_role');
    localStorage.removeItem('wl_session_id');
    localStorage.removeItem('wl_stage_explanations');
    localStorage.removeItem('wl_stage_qa_histories');
    localStorage.removeItem('wl_decision_history');
    localStorage.removeItem('wl_stage_decisions');
    localStorage.removeItem('wl_tutor_history');
    localStorage.removeItem('wl_tutor_pending');
    localStorage.removeItem('wl_answer_pending');
    set({
      token: null,
      userId: null,
      email: null,
      role: null,
      sessionId: null,
      stages: [],
      pendingMap: null,
      tutorHistory: {},
      tutorReply: null,
      pendingAdvanceStageId: null,
      pendingCourseComplete: false,
      stageDecisions: {},
      stageQuestions: {},
      isExplanationLoading: false,
      pendingTutorQuestion: null,
      pendingTutorStageId: null,
      streamingTutorQuestion: null,
      streamingTutorStageId: null,
      streamingTutorAnswer: '',
      pendingAnswerQuestionId: null,
      pendingAnswer: null,
      isAwaitingFeedback: false,
    });
  },

  sessionId: localStorage.getItem('wl_session_id'),
  stages: [],
  currentStageId: null,
  currentGenerationId: null,
  setCurrentGenerationId: (id) => set({ currentGenerationId: id }),
  setSession: (sessionId, stages, stageStatuses?) => {
    localStorage.setItem('wl_session_id', sessionId);
    set((s) => {
      const isNewSession = s.sessionId !== sessionId;
      if (isNewSession) {
        localStorage.removeItem('wl_decision_history');
        localStorage.removeItem('wl_stage_decisions');
      }
      // Preserve existing 'current' status when DB hasn't caught up yet
      // (e.g., advanceStage already ran but run_stage hasn't written in_progress to DB)
      const existingStatusMap = new Map(s.stages.map((st) => [st.stage_id, st.status]));
      const mappedStages = stages.map((stage, i) => {
        const dbStatus = stageStatuses?.[String(stage.stage_id)];
        let status: StageStatus;
        if (dbStatus === 'completed') {
          status = 'completed';
        } else if (dbStatus === 'in_progress') {
          status = 'current';
        } else if (existingStatusMap.get(stage.stage_id) === 'current') {
          status = 'current';
        } else {
          status = i === 0 ? 'current' : 'pending';
        }
        return { ...stage, status };
      });
      const shouldHoldCurrentStage =
        s.pendingAdvanceStageId !== null && s.currentStageId !== null;
      const viewStages: StageWithStatus[] = shouldHoldCurrentStage
        ? mappedStages.map((stage) => ({
            ...stage,
            status:
              stage.stage_id === s.currentStageId
                ? 'current'
                : stage.status === 'current'
                ? 'pending'
                : stage.status,
          }))
        : mappedStages;
      const currentStage = viewStages.find((st) => st.status === 'current');
      // 全 completed session（無 current stage）優先 fallback 到「最後一個 completed」，
      // 而非 stages[0]，符合「課程在這結束」的直覺與後端 _resume_from_stored 重 emit 的章節對齊
      const lastCompletedStage = [...viewStages].reverse().find((st) => st.status === 'completed');
      const nextCurrentStageId = shouldHoldCurrentStage
        ? s.currentStageId
        : currentStage?.stage_id
          ?? lastCompletedStage?.stage_id
          ?? stages[0]?.stage_id
          ?? null;
      const currentStageChanged =
        !isNewSession &&
        s.currentStageId !== null &&
        nextCurrentStageId !== null &&
        nextCurrentStageId !== s.currentStageId;
      const previousStageId = s.currentStageId;
      const stageQaHistories =
        currentStageChanged && previousStageId !== null && s.qaHistory.length > 0
          ? { ...s.stageQaHistories, [previousStageId]: s.qaHistory }
          : s.stageQaHistories;
      if (stageQaHistories !== s.stageQaHistories) {
        localStorage.setItem('wl_stage_qa_histories', JSON.stringify(stageQaHistories));
      }
      if (isNewSession) {
        // 切到不同 session 時清掉跨 session 殘留的 pending state，避免下次重整時
        // 從 localStorage 重新載回「AI 評估中」假象
        localStorage.removeItem('wl_answer_pending');
        localStorage.removeItem('wl_tutor_pending');
      }
      const stageReset = isNewSession ? {
        explanationText: '',
        isStreaming: false,
        isExplanationLoading: false,
        currentQuestion: null,
        lastFeedback: null,
        lastDecision: null,
        pendingNextQuestion: null,
        isAwaitingFeedback: false,
        pendingAnswer: null,
        pendingAnswerQuestionId: null,
        pendingTutorQuestion: null,
        pendingTutorStageId: null,
        isTutorLoading: false,
        courseCompleted: false,
        tutorReply: null,
        pendingAdvanceStageId: null,
        pendingCourseComplete: false,
      } : {
        // 同場次同章節更新只刷新 stages；真正換章時才清掉新畫面的上一章暫存問答。
        explanationText: '',
        isStreaming: false,
        isExplanationLoading: false,
        ...(currentStageChanged
          ? {
              currentQuestion: null,
              lastFeedback: null,
              lastDecision: null,
              pendingNextQuestion: null,
              isAwaitingFeedback: false,
              pendingAnswer: null,
              qaHistory: [],
            }
          : {}),
      };
      return {
        sessionId,
        stages: viewStages,
        currentStageId: nextCurrentStageId,
        stageQaHistories,
        stageSourceChunks: Object.fromEntries(
          stages.map((stage) => [stage.stage_id, stage.source_chunks ?? []])
        ),
        decisionHistory: isNewSession ? [] : s.decisionHistory,
        stageDecisions: isNewSession ? {} : s.stageDecisions,
        stageQuestions: isNewSession ? {} : s.stageQuestions,
        ...stageReset,
      };
    });
  },

  explanationText: '',
  isStreaming: false,
  isExplanationLoading: false,
  isRetryLoading: false,
  beginExplanationLoading: (stageId) =>
    set((s) => {
      const nextExp = { ...s.stageExplanations };
      if (stageId !== null) {
        delete nextExp[stageId];
      }
      localStorage.setItem('wl_stage_explanations', JSON.stringify(nextExp));
      return {
        isExplanationLoading: true,
        isRetryLoading: false,
        explanationText: '',
        isStreaming: false,
        stageExplanations: nextExp,
      };
    }),
  beginRetryLoading: (stageId) =>
    set((s) => {
      const nextExp = { ...s.stageExplanations };
      if (stageId !== null) {
        delete nextExp[stageId];
      }
      localStorage.setItem('wl_stage_explanations', JSON.stringify(nextExp));
      return {
        isExplanationLoading: true,
        isRetryLoading: true,
        explanationText: '',
        isStreaming: false,
        stageExplanations: nextExp,
      };
    }),
  endExplanationLoading: () => set({ isExplanationLoading: false, isRetryLoading: false }),
  appendExplanationChunk: () => {},
  setExplanationComplete: () => set({ isStreaming: false }),
  stageExplanations: loadStageExplanations(),
  stageSourceChunks: {},
  storeStageExplanation: (stageId, text) =>
    set((s) => {
      const updated = { ...s.stageExplanations, [stageId]: text };
      localStorage.setItem('wl_stage_explanations', JSON.stringify(updated));
      return { stageExplanations: updated };
    }),
  finalizeStageExplanation: (stageId, full) =>
    set((s) => {
      const updated = { ...s.stageExplanations, [stageId]: full };
      localStorage.setItem('wl_stage_explanations', JSON.stringify(updated));
      return {
        explanationText: '',
        isStreaming: false,
        isExplanationLoading: false,
        isRetryLoading: false,
        stageExplanations: updated,
        currentGenerationId: null,
      };
    }),
  selectedStageId: null,
  setSelectedStage: (id) => set({ selectedStageId: id }),

  currentQuestion: null,
  lastFeedback: null,
  lastDecision: null,
  decisionHistory: loadDecisionHistory(),
  stageDecisions: loadStageDecisions(),
  isAwaitingFeedback: !!localStorage.getItem('wl_answer_pending'),
  pendingNextQuestion: null,
  pendingAnswer: (() => {
    try { const r = localStorage.getItem('wl_answer_pending'); return r ? JSON.parse(r).answer : null; } catch { return null; }
  })(),
  pendingAnswerQuestionId: (() => {
    try { const r = localStorage.getItem('wl_answer_pending'); return r ? JSON.parse(r).questionId : null; } catch { return null; }
  })(),
  qaHistory: [],
  stageQaHistories: loadStageQaHistories(),
  stageQuestions: {},
  tutorReply: null,
  tutorHistory: {},
  isTutorLoading: !!localStorage.getItem('wl_tutor_pending'),
  setTutorLoading: (v) => set({ isTutorLoading: v }),
  pendingTutorQuestion: (() => {
    try { const r = localStorage.getItem('wl_tutor_pending'); return r ? JSON.parse(r).question : null; } catch { return null; }
  })(),
  pendingTutorStageId: (() => {
    try { const r = localStorage.getItem('wl_tutor_pending'); return r ? JSON.parse(r).stageId : null; } catch { return null; }
  })(),
  setPendingTutor: (question, stageId) => {
    try { localStorage.setItem('wl_tutor_pending', JSON.stringify({ question, stageId })); } catch { /* localStorage 不可用：忽略 */ }
    set({ pendingTutorQuestion: question, pendingTutorStageId: stageId, isTutorLoading: true });
  },
  clearPendingTutor: () => {
    localStorage.removeItem('wl_tutor_pending');
    set({ pendingTutorQuestion: null, pendingTutorStageId: null, isTutorLoading: false });
  },
  streamingTutorQuestion: null,
  streamingTutorStageId: null,
  streamingTutorAnswer: '',
  appendTutorChunk: (payload) =>
    set((s) => {
      // 新問題或不同 stage → 重啟累積；同問題 → append
      if (
        s.streamingTutorQuestion !== payload.question ||
        s.streamingTutorStageId !== payload.stage_id
      ) {
        return {
          streamingTutorQuestion: payload.question,
          streamingTutorStageId: payload.stage_id,
          streamingTutorAnswer: payload.chunk,
        };
      }
      return { streamingTutorAnswer: s.streamingTutorAnswer + payload.chunk };
    }),
  clearStreamingTutor: () =>
    set({
      streamingTutorQuestion: null,
      streamingTutorStageId: null,
      streamingTutorAnswer: '',
    }),
  commitStreamingTutorAsCancelled: () =>
    set((s) => {
      if (s.streamingTutorQuestion === null || s.streamingTutorStageId === null) {
        return s;
      }
      const stageId = s.streamingTutorStageId;
      const prev = s.tutorHistory[stageId] ?? [];
      const cancelledMsg = {
        question: s.streamingTutorQuestion,
        answer: s.streamingTutorAnswer + '\n\n*（已取消）*',
      };
      const updated = {
        ...s.tutorHistory,
        [stageId]: [...prev, cancelledMsg],
      };
      if (s.sessionId) {
        try {
          localStorage.setItem(`wl_tutor_${s.sessionId}`, JSON.stringify(updated));
        } catch { /* localStorage 不可用：忽略 */ }
      }
      localStorage.removeItem('wl_tutor_pending');
      return {
        tutorHistory: updated,
        streamingTutorQuestion: null,
        streamingTutorStageId: null,
        streamingTutorAnswer: '',
        isTutorLoading: false,
        pendingTutorQuestion: null,
        pendingTutorStageId: null,
      };
    }),
  addTutorMessage: (msg) =>
    set((s) => {
      const prev = s.tutorHistory[msg.stage_id] ?? [];
      const isDuplicate = prev.some((item) => item.question === msg.question);
      const updated: Record<number, TutorMessage[]> = isDuplicate
        ? s.tutorHistory
        : {
            ...s.tutorHistory,
            [msg.stage_id]: [
              ...prev,
              { id: msg.id, question: msg.question, answer: msg.answer, in_scope: msg.in_scope, scope: msg.scope },
            ],
          };
      if (!isDuplicate && s.sessionId) {
        try {
          localStorage.setItem(`wl_tutor_${s.sessionId}`, JSON.stringify(updated));
        } catch { /* localStorage 不可用：忽略 */ }
      }
      localStorage.removeItem('wl_tutor_pending');
      return {
        tutorReply: { question: msg.question, answer: msg.answer, in_scope: msg.in_scope, scope: msg.scope },
        tutorHistory: updated,
        isTutorLoading: false,
        pendingTutorQuestion: null,
        pendingTutorStageId: null,
      };
    }),
  setTutorHistories: (map) =>
    set((s) => {
      if (s.sessionId) {
        try {
          localStorage.setItem(`wl_tutor_${s.sessionId}`, JSON.stringify(map));
        } catch { /* localStorage 不可用：忽略 */ }
      }
      return { tutorHistory: map };
    }),
  clearTutorHistory: () =>
    set((s) => {
      if (s.sessionId) {
        try { localStorage.removeItem(`wl_tutor_${s.sessionId}`); } catch { /* localStorage 不可用：忽略 */ }
      }
      localStorage.removeItem('wl_tutor_history');
      return { tutorHistory: {}, tutorReply: null };
    }),
  deleteTutorMessage: (stageId, recordId) =>
    set((s) => {
      const prev = s.tutorHistory[stageId] ?? [];
      const updated = {
        ...s.tutorHistory,
        [stageId]: prev.filter((item) => item.id !== recordId),
      };
      if (s.sessionId) {
        try { localStorage.setItem(`wl_tutor_${s.sessionId}`, JSON.stringify(updated)); } catch { /* localStorage 不可用：忽略 */ }
      }
      return { tutorHistory: updated };
    }),
  setQuestion: (q) =>
    set((s) => {
      const isPending = s.pendingAnswerQuestionId !== null && q.question_id === s.pendingAnswerQuestionId;
      if (q.stage_id !== s.currentStageId) {
        return {
          stageQuestions: { ...s.stageQuestions, [q.stage_id]: q },
          isAwaitingFeedback: isPending ? true : false,
        };
      }
      const isNewStageQuestion =
        s.currentQuestion !== null && q.stage_id !== s.currentQuestion.stage_id;
      if (isNewStageQuestion) {
        return {
          currentQuestion: q,
          lastFeedback: null,
          pendingNextQuestion: null,
          isAwaitingFeedback: isPending ? true : false,
        };
      }
      if (s.lastFeedback) {
        return { pendingNextQuestion: q, isAwaitingFeedback: isPending ? true : false };
      }
      return { currentQuestion: q, lastFeedback: null, pendingNextQuestion: null, isAwaitingFeedback: isPending ? true : false };
    }),
  setQuestionImmediate: (q) =>
    set((s) => ({
      currentQuestion: q,
      pendingNextQuestion: null,
      // 若這題正是 pending answer 等待回覆的題目，保持鎖定狀態
      isAwaitingFeedback:
        q !== null && s.pendingAnswerQuestionId !== null && q.question_id === s.pendingAnswerQuestionId
          ? true
          : false,
    })),
  setFeedback: (f) =>
    set((s) => {
      localStorage.removeItem('wl_answer_pending');
      const item: QaHistoryItem | null =
        s.currentQuestion && s.pendingAnswer !== null
          ? {
              questionId: s.currentQuestion.question_id,
              questionText: s.currentQuestion.text,
              questionType: s.currentQuestion.type,
              userAnswer: s.pendingAnswer,
              score: f.score,
              feedbackText: f.feedback_text,
              clarificationQuestion: f.clarification_question,
            }
          : null;
      return {
        lastFeedback: f,
        isAwaitingFeedback: false,
        pendingAnswer: null,
        pendingAnswerQuestionId: null,
        qaHistory: item ? [...s.qaHistory, item] : s.qaHistory,
      };
    }),
  setRecoveredFeedback: (f) =>
    set((s) => {
      if (s.pendingAnswerQuestionId !== null) {
        if (f && f.question_id === s.pendingAnswerQuestionId) {
          // 重整前答案已處理完成：清除 pending 並顯示回覆
          localStorage.removeItem('wl_answer_pending');
          return {
            lastFeedback: f,
            isAwaitingFeedback: false,
            pendingAnswer: null,
            pendingAnswerQuestionId: null,
          };
        }
        // 正在等待本題評分，recovered feedback 是前一題的舊資料：忽略，保持鎖定
        return {};
      }
      return { lastFeedback: f, isAwaitingFeedback: false, pendingAnswer: null };
    }),
  setDecision: (d) =>
    set((s) => {
      const stageId = d.strategy_snapshot?.current_stage_id;
      if (stageId === undefined) {
        return { lastDecision: d };
      }
      const updated = { ...s.stageDecisions, [stageId]: d };
      localStorage.setItem('wl_stage_decisions', JSON.stringify(updated));
      return { lastDecision: d, stageDecisions: updated };
    }),
  pushDecisionHistory: (d) =>
    set((s) => {
      const item = {
        at: new Date().toISOString(),
        decision: d.decision,
        stageId: d.strategy_snapshot?.current_stage_id ?? null,
        stageTitle: d.strategy_snapshot?.current_stage_title ?? '',
        bestScore: d.best_score,
        nextStageId: d.next_stage_id,
        nextStageScore: d.next_stage_score,
        candidates: d.strategy_snapshot?.next_stage_candidates ?? [],
      };
      const updated = [...s.decisionHistory, item].slice(-DECISION_HISTORY_MAX);
      localStorage.setItem('wl_decision_history', JSON.stringify(updated));
      return { decisionHistory: updated };
    }),
  setAwaitingFeedback: (v) => set({ isAwaitingFeedback: v }),
  setPendingAnswer: (answer) => set({ pendingAnswer: answer }),
  setPendingSubmit: (questionId, answer) => {
    try { localStorage.setItem('wl_answer_pending', JSON.stringify({ questionId, answer })); } catch { /* localStorage 不可用：忽略 */ }
    set({ pendingAnswerQuestionId: questionId, pendingAnswer: answer, isAwaitingFeedback: true });
  },
  clearPendingSubmit: () => {
    localStorage.removeItem('wl_answer_pending');
    set({ pendingAnswerQuestionId: null, pendingAnswer: null, isAwaitingFeedback: false });
  },
  setQaHistory: (records) => set({ qaHistory: records }),
  setTutorReply: (reply) => set({ tutorReply: reply }),
  hydrateSnapshot: ({ stageExplanations, stageQaHistories }) =>
    set((s) => {
      const mergedExpl = { ...s.stageExplanations, ...stageExplanations };
      const mergedQa = { ...s.stageQaHistories, ...stageQaHistories };
      localStorage.setItem('wl_stage_explanations', JSON.stringify(mergedExpl));
      localStorage.setItem('wl_stage_qa_histories', JSON.stringify(mergedQa));
      return { stageExplanations: mergedExpl, stageQaHistories: mergedQa };
    }),
  mergeStageQaHistory: (stageId, records) =>
    set((s) => {
      const merged = { ...s.stageQaHistories, [stageId]: records };
      localStorage.setItem('wl_stage_qa_histories', JSON.stringify(merged));
      return { stageQaHistories: merged };
    }),
  hydrateDecisionHistory: (history) =>
    set(() => {
      const limited = history.slice(-DECISION_HISTORY_MAX);
      localStorage.setItem('wl_decision_history', JSON.stringify(limited));
      const stageDecisions = Object.fromEntries(
        limited
          .filter((h) => h.stageId !== null && h.strategySnapshot)
          .map((h) => [
            h.stageId as number,
            {
              decision: h.decision,
              message: '',
              next_stage_id: h.nextStageId,
              next_stage_score: h.nextStageScore,
              best_score: h.bestScore,
              reason_lines: h.reasonLines ?? [],
              strategy_snapshot: h.strategySnapshot,
            } satisfies StageDecisionPayload,
          ])
      );
      localStorage.setItem('wl_stage_decisions', JSON.stringify(stageDecisions));
      return { decisionHistory: limited, stageDecisions };
    }),
  proceedToNextQuestion: () =>
    set((s) => ({
      currentQuestion: s.pendingNextQuestion ?? null,
      pendingNextQuestion: null,
      lastFeedback: null,
    })),
  advanceStage: (nextStageId) =>
    set((s) => {
      localStorage.removeItem('wl_answer_pending');
      const updatedStageQaHistories = s.currentStageId !== null && s.qaHistory.length > 0
        ? { ...s.stageQaHistories, [s.currentStageId]: s.qaHistory }
        : s.stageQaHistories;
      if (updatedStageQaHistories !== s.stageQaHistories) {
        localStorage.setItem('wl_stage_qa_histories', JSON.stringify(updatedStageQaHistories));
      }
      const nextExpl = { ...s.stageExplanations };
      localStorage.setItem('wl_stage_explanations', JSON.stringify(nextExpl));
      const cachedQuestion = nextStageId !== null ? s.stageQuestions[nextStageId] ?? null : null;
      return {
        stages: s.stages.map((st) => ({
          ...st,
          status:
            st.stage_id === s.currentStageId
              ? 'completed'
              : st.stage_id === nextStageId
              ? 'current'
              : st.status,
        })),
        currentStageId: nextStageId,
        currentGenerationId: null,
        explanationText: '',
        isStreaming: false,
        isExplanationLoading: nextStageId !== null && !nextExpl[nextStageId],
        stageExplanations: nextExpl,
        currentQuestion: cachedQuestion,
        lastFeedback: null,
        lastDecision: null,
        pendingNextQuestion: null,
        isAwaitingFeedback: false,
        selectedStageId: null,
        qaHistory: [],
        pendingAnswer: null,
        pendingAnswerQuestionId: null,
        pendingAdvanceStageId: null,
        pendingCourseComplete: false,
        stageQaHistories: updatedStageQaHistories,
      };
    }),
  pendingAdvanceStageId: null,
  setPendingAdvance: (id) => set({ pendingAdvanceStageId: id }),
  pendingCourseComplete: false,
  setPendingCourseComplete: (v) => set({ pendingCourseComplete: v }),

  pendingMap: null,
  setPendingMap: (map) => set({ pendingMap: map }),

  isConnected: false,
  setConnected: (v) => set({ isConnected: v }),
  reconnectAttempt: null,
  reconnectGaveUp: false,
  setReconnectAttempt: (n) => set({ reconnectAttempt: n }),
  setReconnectGaveUp: (v) => set({ reconnectGaveUp: v }),
  courseCompleted: false,
  setCourseCompleted: () => set((s) => ({
    courseCompleted: true,
    stages: s.stages.map((st) => ({
      ...st,
      status: st.stage_id === s.currentStageId ? 'completed' : st.status,
    })),
  })),
  resetExplanation: () => set({
    explanationText: '',
    isStreaming: false,
    currentQuestion: null,
    lastFeedback: null,
    pendingNextQuestion: null,
    isAwaitingFeedback: false,
    tutorReply: null,
    currentGenerationId: null,
  }),
  clearSession: () => {
    localStorage.removeItem('wl_session_id');
    localStorage.removeItem('wl_provider');
    localStorage.removeItem('wl_model');
    localStorage.removeItem('wl_stage_explanations');
    localStorage.removeItem('wl_stage_qa_histories');
    localStorage.removeItem('wl_decision_history');
    localStorage.removeItem('wl_stage_decisions');
    localStorage.removeItem('wl_tutor_history');
    set({
      sessionId: null,
      stages: [],
      currentStageId: null,
      currentGenerationId: null,
      explanationText: '',
      isStreaming: false,
      currentQuestion: null,
      lastFeedback: null,
      lastDecision: null,
      pendingNextQuestion: null,
      courseCompleted: false,
      stageExplanations: {},
      stageSourceChunks: {},
      stageQaHistories: {},
      stageQuestions: {},
      selectedStageId: null,
      qaHistory: [],
      tutorReply: null,
      tutorHistory: {},
      pendingAdvanceStageId: null,
      pendingCourseComplete: false,
      decisionHistory: [],
      stageDecisions: {},
      isExplanationLoading: false,
      reconnectAttempt: null,
      reconnectGaveUp: false,
    });
  },
}));
