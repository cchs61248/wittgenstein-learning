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
  isAwaitingFeedback: boolean;
  pendingNextQuestion: QuestionPayload | null;
  pendingAnswer: string | null;
  qaHistory: QaHistoryItem[];
  setQuestion: (q: QuestionPayload) => void;
  setFeedback: (f: FeedbackPayload) => void;
  setDecision: (d: StageDecisionPayload) => void;
  setAwaitingFeedback: (v: boolean) => void;
  setPendingAnswer: (answer: string) => void;
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
    set({ token: null, userId: null, email: null, sessionId: null, stages: [] });
  },

  sessionId: localStorage.getItem('wl_session_id'),
  stages: [],
  currentStageId: null,
  setSession: (sessionId, stages, stageStatuses?) => {
    localStorage.setItem('wl_session_id', sessionId);
    set({
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
      pendingNextQuestion: null,
      isAwaitingFeedback: false,
      courseCompleted: false,
    });
  },

  explanationText: '',
  isStreaming: false,
  appendExplanationChunk: (chunk) =>
    set((s) => ({ explanationText: s.explanationText + chunk, isStreaming: true })),
  setExplanationComplete: () => set({ isStreaming: false }),
  stageExplanations: {},
  storeStageExplanation: (stageId, text) =>
    set((s) => ({ stageExplanations: { ...s.stageExplanations, [stageId]: text } })),
  selectedStageId: null,
  setSelectedStage: (id) => set({ selectedStageId: id }),

  currentQuestion: null,
  lastFeedback: null,
  lastDecision: null,
  isAwaitingFeedback: false,
  pendingNextQuestion: null,
  pendingAnswer: null,
  qaHistory: [],
  setQuestion: (q) =>
    set((s) => {
      if (s.lastFeedback) {
        return { pendingNextQuestion: q, isAwaitingFeedback: false };
      }
      return { currentQuestion: q, lastFeedback: null, pendingNextQuestion: null, isAwaitingFeedback: false };
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
  setDecision: (d) => set({ lastDecision: d }),
  setAwaitingFeedback: (v) => set({ isAwaitingFeedback: v }),
  setPendingAnswer: (answer) => set({ pendingAnswer: answer }),
  proceedToNextQuestion: () =>
    set((s) => ({
      currentQuestion: s.pendingNextQuestion ?? s.currentQuestion,
      pendingNextQuestion: null,
      lastFeedback: null,
    })),
  advanceStage: (nextStageId) =>
    set((s) => ({
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
    })),

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
  }),
  clearSession: () => {
    localStorage.removeItem('wl_session_id');
    localStorage.removeItem('wl_provider');
    localStorage.removeItem('wl_model');
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
      selectedStageId: null,
      qaHistory: [],
      pendingAnswer: null,
    });
  },
}));
