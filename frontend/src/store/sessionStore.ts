import { create } from 'zustand';
import type { StageInfo, QuestionPayload, FeedbackPayload, StageDecisionPayload, KnowledgeMapNode } from '../types/messages';

export type StageStatus = 'pending' | 'current' | 'completed';

interface StageWithStatus extends StageInfo {
  status: StageStatus;
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
  setSession: (sessionId: string, stages: StageInfo[]) => void;

  // 講解
  explanationText: string;
  isStreaming: boolean;
  appendExplanationChunk: (chunk: string) => void;
  setExplanationComplete: () => void;

  // 問答
  currentQuestion: QuestionPayload | null;
  lastFeedback: FeedbackPayload | null;
  lastDecision: StageDecisionPayload | null;
  setQuestion: (q: QuestionPayload) => void;
  setFeedback: (f: FeedbackPayload) => void;
  setDecision: (d: StageDecisionPayload) => void;
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
    set({ token: null, userId: null, email: null });
  },

  sessionId: null,
  stages: [],
  currentStageId: null,
  setSession: (sessionId, stages) =>
    set({
      sessionId,
      stages: stages.map((s, i) => ({ ...s, status: i === 0 ? 'current' : 'pending' })),
      currentStageId: stages[0]?.stage_id ?? null,
      explanationText: '',
      isStreaming: false,
      currentQuestion: null,
      lastFeedback: null,
      lastDecision: null,
      courseCompleted: false,
    }),

  explanationText: '',
  isStreaming: false,
  appendExplanationChunk: (chunk) =>
    set((s) => ({ explanationText: s.explanationText + chunk, isStreaming: true })),
  setExplanationComplete: () => set({ isStreaming: false }),

  currentQuestion: null,
  lastFeedback: null,
  lastDecision: null,
  setQuestion: (q) => set({ currentQuestion: q, lastFeedback: null }),
  setFeedback: (f) => set({ lastFeedback: f }),
  setDecision: (d) => set({ lastDecision: d }),
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
    })),

  pendingMap: null,
  setPendingMap: (map) => set({ pendingMap: map }),

  isConnected: false,
  setConnected: (v) => set({ isConnected: v }),
  courseCompleted: false,
  setCourseCompleted: () => set({ courseCompleted: true }),
  resetExplanation: () => set({ explanationText: '', isStreaming: false, currentQuestion: null, lastFeedback: null }),
}));
