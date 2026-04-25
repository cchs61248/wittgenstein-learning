export type ProviderType = 'claude' | 'openai' | 'gemini' | 'monica';
export type DepthType = 'beginner' | 'intermediate' | 'advanced';
export type DecisionType = 'advance' | 'retry' | 'remediate' | 'reteach';

export interface KnowledgeMapNode {
  node_id: string;
  stage_id: number;
  title: string;
}

export interface KnowledgeMapPayload {
  nodes: KnowledgeMapNode[];
  summary: string;
}

export interface StageInfo {
  stage_id: number;
  title: string;
}

// 伺服器 → 客戶端
export interface SessionStartedPayload {
  session_id: string;
  total_stages: number;
  stages: StageInfo[];
  stage_statuses?: Record<string, string>;
}

export interface ExplanationChunkPayload {
  chunk: string;
  is_final: boolean;
}

export interface ExplanationCompletePayload {
  stage_id: number;
  stage_title: string;
  full_explanation: string;
}

export interface QuestionPayload {
  question_id: string;
  text: string;
  type: 'apply' | 'understand' | 'create';
  stage_id: number;
  attempt_number: number;
}

export interface FeedbackPayload {
  question_id: string;
  score: number;
  feedback_text: string;
  needs_clarification: boolean;
  clarification_question?: string | null;
}

export interface StageDecisionPayload {
  decision: DecisionType;
  message: string;
  next_stage_id: number | null;
  best_score: number;
}

export interface ErrorPayload {
  message: string;
}

export type ServerMessage =
  | { type: 'knowledge_map'; payload: KnowledgeMapPayload }
  | { type: 'session_started'; payload: SessionStartedPayload }
  | { type: 'explanation_chunk'; payload: ExplanationChunkPayload }
  | { type: 'explanation_complete'; payload: ExplanationCompletePayload }
  | { type: 'explanation_reset'; payload: Record<string, never> }
  | { type: 'question'; payload: QuestionPayload }
  | { type: 'feedback'; payload: FeedbackPayload }
  | { type: 'stage_decision'; payload: StageDecisionPayload }
  | { type: 'course_completed'; payload: { message: string } }
  | { type: 'error'; payload: ErrorPayload };

// 客戶端 → 伺服器
export interface StartSessionMessage {
  type: 'start_session';
  payload: {
    content?: string;
    uploaded_file_id?: string;
    provider: ProviderType;
    target_depth: DepthType;
    model?: string;
  };
}

export interface SubmitAnswerMessage {
  type: 'submit_answer';
  payload: {
    session_id: string;
    question_id: string;
    answer: string;
  };
}

export interface ResumeSessionMessage {
  type: 'resume_session';
  payload: {
    session_id: string;
    provider: ProviderType;
    model?: string;
  };
}
