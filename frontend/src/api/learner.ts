const BASE = 'http://localhost:8000';

export interface ConceptMastery {
  concept_name: string;
  mastery_score: number;
  total_exposures: number;
  last_tested: string | null;
}

export interface Misconception {
  concept_name: string;
  pattern: string;
  severity: 'high' | 'medium' | 'low';
  repair_strategy: string;
}

export interface LearnerStats {
  concepts: ConceptMastery[];
  misconceptions: Misconception[];
  weak_count: number;
}

export async function fetchLearnerStats(token: string): Promise<LearnerStats | null> {
  try {
    const res = await fetch(`${BASE}/learner/stats?token=${encodeURIComponent(token)}`);
    if (!res.ok) return null;
    return (await res.json()) as LearnerStats;
  } catch {
    return null;
  }
}
