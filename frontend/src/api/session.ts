const BASE = 'http://localhost:8000';

export interface ActiveSessionStage {
  stage_id: number;
  node_id: string;
  title: string;
}

export interface ActiveSession {
  session_id: string;
  status: 'active' | 'pending_confirmation';
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
