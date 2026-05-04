import { getApiBase } from './apiBase';

const BASE = getApiBase();

export type UserUiStatePayload = {
  v?: number;
  layoutBySession: Record<string, Record<string, unknown>>;
  bookshelfOrder: string[];
};

export async function fetchUserUiState(token: string): Promise<UserUiStatePayload | null> {
  try {
    const res = await fetch(`${BASE}/user/ui-state?token=${encodeURIComponent(token)}`);
    if (!res.ok) return null;
    return (await res.json()) as UserUiStatePayload;
  } catch {
    return null;
  }
}

export async function saveUserUiState(
  token: string,
  body: { layoutBySession: Record<string, unknown>; bookshelfOrder: string[] }
): Promise<boolean> {
  try {
    const res = await fetch(`${BASE}/user/ui-state?token=${encodeURIComponent(token)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return res.ok;
  } catch {
    return false;
  }
}
