export type ProviderType = 'claude' | 'openai' | 'gemini' | 'monica' | 'deepseek';
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
  node_id?: string;
  title: string;
  source_chunks?: SourceChunk[];
  kind?: 'reteach' | 'remediation' | 'enrichment' | string;
  source_stage_id?: number;
}

export interface SourceChunk {
  chunk_id: string;
  quote: string;
  note?: string;
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
  generation_id?: string;
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
  answer_mode?: 'short_answer' | 'multiple_choice';
  options?: { id: string; text: string }[];
  evidence_chunk_ids?: string[];
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
  next_stage_score?: number | null;
  best_score: number;
  reason_lines?: string[];
  strategy_snapshot?: {
    current_stage_id: number;
    current_stage_title: string;
    stable_high: boolean;
    weak_concepts?: string[];
    mastery_map?: Record<string, number>;
    score_trend?: number[];
    next_stage_candidates?: {
      stage_id: number;
      title: string;
      score: number;
      is_dynamic?: boolean;
      kind?: string;
      source_stage_id?: number;
    }[];
    remediation_focus: string[];
    dynamic_stage_inserted: boolean;
  };
}

export interface ErrorPayload {
  message: string;
}

export interface QaHistoryRecord {
  question_id: string;
  question_text: string;
  question_type: 'apply' | 'understand' | 'create';
  user_answer: string;
  score: number;
  feedback_text: string;
}

export interface QaHistoryPayload {
  records: QaHistoryRecord[];
}

export type TutorScope = 'current_chapter' | 'other_chapter' | 'out_of_scope';

export interface TutorMessage {
  id?: number;
  question: string;
  answer: string;
  in_scope?: boolean;
  scope?: TutorScope;
}

export interface TutorReplyPayload extends TutorMessage {
  stage_id: number;
  scope?: TutorScope;
}

export interface TutorChunkPayload {
  chunk: string;
  stage_id: number;
  question: string;
}

export interface SessionSnapshotPayload {
  stage_explanations: Record<string, string>;
  stage_qa_histories: Record<string, QaHistoryRecord[]>;
  decision_history?: Array<{
    stage_id: number;
    decision: DecisionType;
    best_score: number;
    next_stage_id: number | null;
    next_stage_score?: number | null;
    reason_lines: string[];
    strategy_snapshot: StageDecisionPayload['strategy_snapshot'];
    created_at: string;
  }>;
  tutor_histories?: Record<string, TutorMessage[]>;
}

export interface ResumeStatePayload {
  current_question?: QuestionPayload | null;
  last_feedback?: FeedbackPayload | null;
}

export type ServerMessage =
  | { type: 'session_generating'; payload: { session_id: string } }
  | { type: 'knowledge_map'; payload: KnowledgeMapPayload }
  | { type: 'session_started'; payload: SessionStartedPayload }
  | { type: 'explanation_chunk'; payload: ExplanationChunkPayload }
  | { type: 'explanation_complete'; payload: ExplanationCompletePayload }
  | { type: 'explanation_reset'; payload: Record<string, never> }
  | { type: 'question'; payload: QuestionPayload }
  | { type: 'feedback'; payload: FeedbackPayload }
  | { type: 'stage_decision'; payload: StageDecisionPayload }
  | { type: 'qa_history'; payload: QaHistoryPayload }
  | { type: 'session_snapshot'; payload: SessionSnapshotPayload }
  | { type: 'resume_state'; payload: ResumeStatePayload }
  | { type: 'tutor_chunk'; payload: TutorChunkPayload }
  | { type: 'tutor_reply'; payload: TutorReplyPayload }
  | { type: 'generation_cancelled'; payload: { key: string; kind: 'ask_tutor' | 'other' } }
  | { type: 'course_completed'; payload: { message: string } }
  | { type: 'kicked'; payload: { message: string } }
  | { type: 'error'; payload: ErrorPayload };

// 客戶端 → 伺服器
export interface StartSessionMessage {
  type: 'start_session';
  payload: {
    content?: string;
    uploaded_file_id?: string;
    provider: ProviderType;
    target_depth: DepthType;
    question_mode?: 'short_answer' | 'multiple_choice';
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

export interface CancelGenerationMessage {
  type: 'cancel_generation';
  payload: { key?: string };
}
