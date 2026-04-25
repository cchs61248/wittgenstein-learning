const BASE = 'http://localhost:8000';

export interface ActiveSessionStage {
  stage_id: number;
  node_id: string;
  title: string;
}

export interface ActiveSession {
  session_id: string;
  current_stage_id: number;
  total_stages: number;
  stages: ActiveSessionStage[];
  stage_statuses: Record<string, string>;
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
