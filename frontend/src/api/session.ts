import { getApiBase } from './apiBase';

const BASE = getApiBase();

export interface ActiveSessionStage {
  stage_id: number;
  node_id: string;
  title: string;
  kind?: 'reteach' | 'remediation' | 'enrichment' | string;
  source_stage_id?: number;
  source_chunks?: { chunk_id: string; quote: string; note?: string }[];
}

export interface ActiveSession {
  session_id: string;
  status: 'active' | 'pending_confirmation' | 'generating';
  current_stage_id: number;
  total_stages: number;
  provider?: string | null;
  model?: string | null;
  stages: ActiveSessionStage[];
  stage_statuses?: Record<string, string>;
  pending_map?: { nodes: { node_id: string; stage_id: number; title: string }[]; summary: string } | null;
}

export async function getActiveSession(token: string): Promise<ActiveSession | null> {
  try {
    const res = await fetch(`${BASE}/sessions/active?token=${encodeURIComponent(token)}`);
    if (!res.ok) return null;
    const data = await res.json();
    return data.session as ActiveSession | null;
  } catch {
    return null;
  }
}

export interface BookEntry {
  sessionId: string;
  title: string;
  status: 'active' | 'completed' | 'pending_confirmation' | 'generating';
  totalStages: number;
  completedStages: number;
  updatedAt: string | null;
}

export async function listSessions(token: string): Promise<BookEntry[]> {
  try {
    const res = await fetch(`${BASE}/sessions/list?token=${encodeURIComponent(token)}`);
    if (!res.ok) return [];
    const data = await res.json();
    return (data.sessions as Array<{
      session_id: string; title: string; status: string;
      total_stages: number; completed_stages: number; updated_at: string | null;
    }>).map(s => ({
      sessionId: s.session_id,
      title: s.title,
      status: s.status as BookEntry['status'],
      totalStages: s.total_stages,
      completedStages: s.completed_stages,
      updatedAt: s.updated_at,
    }));
  } catch {
    return [];
  }
}

export async function getSessionDetail(token: string, sessionId: string): Promise<ActiveSession | null> {
  try {
    const res = await fetch(`${BASE}/sessions/${sessionId}?token=${encodeURIComponent(token)}`);
    if (!res.ok) return null;
    const data = await res.json();
    return data.session as ActiveSession | null;
  } catch {
    return null;
  }
}

/** 從伺服器讀取已持久化之章節講解全文（與 WS snapshot 相同邏輯），供回顧時不必重整頁面 */
export async function fetchStageExplanation(
  token: string,
  sessionId: string,
  stageId: number,
  signal?: AbortSignal
): Promise<{ stage_id: number; explanation: string } | null> {
  try {
    const res = await fetch(
      `${BASE}/sessions/${encodeURIComponent(sessionId)}/stages/${stageId}/explanation?token=${encodeURIComponent(token)}`,
      { signal }
    );
    if (!res.ok) return null;
    return (await res.json()) as { stage_id: number; explanation: string };
  } catch (e: unknown) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e;
    return null;
  }
}

/** 與 WS session_snapshot 內單筆答題紀錄欄位一致 */
export interface StageQaRecordDto {
  question_id: string;
  question_text: string;
  question_type: string;
  user_answer: string;
  score: number;
  feedback_text: string;
}

export async function fetchStageQaHistory(
  token: string,
  sessionId: string,
  stageId: number,
  signal?: AbortSignal
): Promise<{ stage_id: number; records: StageQaRecordDto[] } | null> {
  try {
    const res = await fetch(
      `${BASE}/sessions/${encodeURIComponent(sessionId)}/stages/${stageId}/qa_history?token=${encodeURIComponent(token)}`,
      { signal }
    );
    if (!res.ok) return null;
    return (await res.json()) as { stage_id: number; records: StageQaRecordDto[] };
  } catch (e: unknown) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e;
    return null;
  }
}

export async function renameSession(token: string, sessionId: string, title: string): Promise<boolean> {
  try {
    const res = await fetch(
      `${BASE}/sessions/${sessionId}/title?token=${encodeURIComponent(token)}`,
      { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }) }
    );
    return res.ok;
  } catch {
    return false;
  }
}

export async function deleteSession(token: string, sessionId: string): Promise<boolean> {
  try {
    const res = await fetch(
      `${BASE}/sessions/${sessionId}?token=${encodeURIComponent(token)}`,
      { method: 'DELETE' }
    );
    return res.ok;
  } catch {
    return false;
  }
}

/** REST 尚未寫入 stages 時，仍可用此物件代表「生成中」session（避免誤用 getActiveSession） */
export function syntheticGeneratingSession(sessionId: string): ActiveSession {
  return {
    session_id: sessionId,
    status: 'generating',
    stages: [],
    current_stage_id: 0,
    total_stages: 0,
    provider: null,
    model: null,
  };
}
