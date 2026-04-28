import { create } from 'zustand';
import type { StageInfo, QuestionPayload, FeedbackPayload, StageDecisionPayload, KnowledgeMapNode } from '../types/messages';

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
  setAuth: (token: string, userId: string, email: string) => void;
  clearAuth: () => void;

  // 會話
  sessionId: string | null;
  stages: StageWithStatus[];
  currentStageId: number | null;
  setSession: (sessionId: string, stages: StageInfo[], stageStatuses?: Record<string, string>) => void;

  // 講解
  explanationText: string;
  isStreaming: boolean;
  appendExplanationChunk: (chunk: string) => void;
  setExplanationComplete: () => void;
  stageExplanations: Record<number, string>;
  storeStageExplanation: (stageId: number, text: string) => void;
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
    candidates?: { stage_id: number; title: string; score: number; is_dynamic?: boolean }[];
  }>;
  isAwaitingFeedback: boolean;
  pendingNextQuestion: QuestionPayload | null;
  pendingAnswer: string | null;
  qaHistory: QaHistoryItem[];
  stageQaHistories: Record<number, QaHistoryItem[]>;
  tutorReply: { question: string; answer: string; in_scope?: boolean } | null;
  setQuestion: (q: QuestionPayload) => void;
  setQuestionImmediate: (q: QuestionPayload | null) => void;
  setFeedback: (f: FeedbackPayload) => void;
  setRecoveredFeedback: (f: FeedbackPayload | null) => void;
  setDecision: (d: StageDecisionPayload) => void;
  pushDecisionHistory: (d: StageDecisionPayload) => void;
  setAwaitingFeedback: (v: boolean) => void;
  setPendingAnswer: (answer: string) => void;
  setQaHistory: (records: QaHistoryItem[]) => void;
  setTutorReply: (reply: { question: string; answer: string; in_scope?: boolean } | null) => void;
  hydrateSnapshot: (snapshot: { stageExplanations: Record<number, string>; stageQaHistories: Record<number, QaHistoryItem[]> }) => void;
  hydrateDecisionHistory: (history: Array<{
    at: string;
    decision: StageDecisionPayload['decision'];
    stageId: number | null;
    stageTitle: string;
    bestScore: number;
    nextStageId: number | null;
    nextStageScore?: number | null;
    candidates?: { stage_id: number; title: string; score: number; is_dynamic?: boolean }[];
  }>) => void;
  proceedToNextQuestion: () => void;
  advanceStage: (nextStageId: number | null) => void;

  // 知識地圖確認
  pendingMap: { nodes: KnowledgeMapNode[]; summary: string } | null;
  setPendingMap: (map: { nodes: KnowledgeMapNode[]; summary: string } | null) => void;

  // UI 狀態
  isConnected: boolean;
  setConnected: (v: boolean) => void;
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

const DECISION_HISTORY_MAX = 200;

export const useSessionStore = create<SessionState>((set) => ({
  token: localStorage.getItem('wl_token'),
  userId: localStorage.getItem('wl_user_id'),
  email: localStorage.getItem('wl_email'),
  setAuth: (token, userId, email) => {
    localStorage.setItem('wl_token', token);
    localStorage.setItem('wl_user_id', userId);
    localStorage.setItem('wl_email', email);
    set({ token, userId, email });
  },
  clearAuth: () => {
    localStorage.removeItem('wl_token');
    localStorage.removeItem('wl_user_id');
    localStorage.removeItem('wl_email');
    localStorage.removeItem('wl_session_id');
    localStorage.removeItem('wl_stage_explanations');
    localStorage.removeItem('wl_stage_qa_histories');
    localStorage.removeItem('wl_decision_history');
    set({ token: null, userId: null, email: null, sessionId: null, stages: [], pendingMap: null });
  },

  sessionId: localStorage.getItem('wl_session_id'),
  stages: [],
  currentStageId: null,
  setSession: (sessionId, stages, stageStatuses?) => {
    localStorage.setItem('wl_session_id', sessionId);
    set((s) => {
      const isNewSession = s.sessionId !== sessionId;
      if (isNewSession) {
        localStorage.removeItem('wl_decision_history');
      }
      return {
      sessionId,
      stages: stages.map((s, i) => {
        const dbStatus = stageStatuses?.[String(s.stage_id)];
        let status: StageStatus;
        if (dbStatus === 'completed') {
          status = 'completed';
        } else if (dbStatus === 'in_progress') {
          status = 'current';
        } else {
          status = i === 0 ? 'current' : 'pending';
        }
        return { ...s, status };
      }),
      currentStageId: stages[0]?.stage_id ?? null,
      explanationText: '',
      isStreaming: false,
      currentQuestion: null,
      lastFeedback: null,
      lastDecision: null,
      decisionHistory: isNewSession ? [] : s.decisionHistory,
      pendingNextQuestion: null,
      isAwaitingFeedback: false,
      courseCompleted: false,
      tutorReply: null,
      };
    });
  },

  explanationText: '',
  isStreaming: false,
  appendExplanationChunk: (chunk) =>
    set((s) => ({ explanationText: s.explanationText + chunk, isStreaming: true })),
  setExplanationComplete: () => set({ isStreaming: false }),
  stageExplanations: loadStageExplanations(),
  storeStageExplanation: (stageId, text) =>
    set((s) => {
      const updated = { ...s.stageExplanations, [stageId]: text };
      localStorage.setItem('wl_stage_explanations', JSON.stringify(updated));
      return { stageExplanations: updated };
    }),
  selectedStageId: null,
  setSelectedStage: (id) => set({ selectedStageId: id }),

  currentQuestion: null,
  lastFeedback: null,
  lastDecision: null,
  decisionHistory: loadDecisionHistory(),
  isAwaitingFeedback: false,
  pendingNextQuestion: null,
  pendingAnswer: null,
  qaHistory: [],
  stageQaHistories: loadStageQaHistories(),
  tutorReply: null,
  setQuestion: (q) =>
    set((s) => {
      if (s.lastFeedback) {
        return { pendingNextQuestion: q, isAwaitingFeedback: false };
      }
      return { currentQuestion: q, lastFeedback: null, pendingNextQuestion: null, isAwaitingFeedback: false };
    }),
  setQuestionImmediate: (q) =>
    set({
      currentQuestion: q,
      pendingNextQuestion: null,
      isAwaitingFeedback: false,
    }),
  setFeedback: (f) =>
    set((s) => {
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
        qaHistory: item ? [...s.qaHistory, item] : s.qaHistory,
      };
    }),
  setRecoveredFeedback: (f) =>
    set({
      lastFeedback: f,
      isAwaitingFeedback: false,
      pendingAnswer: null,
    }),
  setDecision: (d) => set({ lastDecision: d }),
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
  setQaHistory: (records) => set({ qaHistory: records }),
  setTutorReply: (reply) => set({ tutorReply: reply }),
  hydrateSnapshot: ({ stageExplanations, stageQaHistories }) =>
    set(() => {
      localStorage.setItem('wl_stage_explanations', JSON.stringify(stageExplanations));
      localStorage.setItem('wl_stage_qa_histories', JSON.stringify(stageQaHistories));
      return { stageExplanations, stageQaHistories };
    }),
  hydrateDecisionHistory: (history) =>
    set(() => {
      const limited = history.slice(-DECISION_HISTORY_MAX);
      localStorage.setItem('wl_decision_history', JSON.stringify(limited));
      return { decisionHistory: limited };
    }),
  proceedToNextQuestion: () =>
    set((s) => ({
      currentQuestion: s.pendingNextQuestion ?? null,
      pendingNextQuestion: null,
      lastFeedback: null,
    })),
  advanceStage: (nextStageId) =>
    set((s) => {
      const updatedStageQaHistories = s.currentStageId !== null && s.qaHistory.length > 0
        ? { ...s.stageQaHistories, [s.currentStageId]: s.qaHistory }
        : s.stageQaHistories;
      if (updatedStageQaHistories !== s.stageQaHistories) {
        localStorage.setItem('wl_stage_qa_histories', JSON.stringify(updatedStageQaHistories));
      }
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
        explanationText: '',
        currentQuestion: null,
        lastFeedback: null,
        pendingNextQuestion: null,
        isAwaitingFeedback: false,
        selectedStageId: null,
        qaHistory: [],
        pendingAnswer: null,
        stageQaHistories: updatedStageQaHistories,
      };
    }),

  pendingMap: null,
  setPendingMap: (map) => set({ pendingMap: map }),

  isConnected: false,
  setConnected: (v) => set({ isConnected: v }),
  courseCompleted: false,
  setCourseCompleted: () => set({ courseCompleted: true }),
  resetExplanation: () => set({
    explanationText: '',
    isStreaming: false,
    currentQuestion: null,
    lastFeedback: null,
    pendingNextQuestion: null,
    isAwaitingFeedback: false,
    tutorReply: null,
  }),
  clearSession: () => {
    localStorage.removeItem('wl_session_id');
    localStorage.removeItem('wl_provider');
    localStorage.removeItem('wl_model');
    localStorage.removeItem('wl_stage_explanations');
    localStorage.removeItem('wl_stage_qa_histories');
    localStorage.removeItem('wl_decision_history');
    set({
      sessionId: null,
      stages: [],
      currentStageId: null,
      explanationText: '',
      isStreaming: false,
      currentQuestion: null,
      lastFeedback: null,
      lastDecision: null,
      pendingNextQuestion: null,
      isAwaitingFeedback: false,
      courseCompleted: false,
      stageExplanations: {},
      stageQaHistories: {},
      selectedStageId: null,
      qaHistory: [],
      pendingAnswer: null,
      tutorReply: null,
      decisionHistory: [],
    });
  },
}));
