import type { BookEntry } from '../api/session';

export const BOOKSHELF_ORDER_KEY = 'wl_bookshelf_order_v1';

/** 與 App 原邏輯相同：合併輪詢結果與樂觀「生成中」標題 */
export function mergeBookshelf(existing: BookEntry[], fresh: BookEntry[]): BookEntry[] {
  const freshMap = new Map(fresh.map((e) => [e.sessionId, e]));
  const existingIds = new Set(existing.map((e) => e.sessionId));
  const newItems = fresh.filter((e) => !existingIds.has(e.sessionId));
  const updatedExisting = existing
    .filter((e) => freshMap.has(e.sessionId) || e.status === 'generating')
    .map((e) => {
      const freshEntry = freshMap.get(e.sessionId);
      if (!freshEntry) return e;
      if (freshEntry.status === 'generating') return { ...freshEntry, title: e.title };
      return freshEntry;
    });
  return [...newItems, ...updatedExisting];
}

export function loadBookOrder(): string[] {
  try {
    const raw = localStorage.getItem(BOOKSHELF_ORDER_KEY);
    if (!raw) return [];
    const o = JSON.parse(raw) as unknown;
    return Array.isArray(o) ? o.filter((x): x is string => typeof x === 'string') : [];
  } catch {
    return [];
  }
}

export function saveBookOrder(books: BookEntry[]) {
  try {
    localStorage.setItem(BOOKSHELF_ORDER_KEY, JSON.stringify(books.map((b) => b.sessionId)));
  } catch {
    /* ignore */
  }
}

/**
 * 新教材（不在已存順序內）依 API 順序排在最前，其餘維持使用者上次看到的相對順序。
 */
export function orderBookshelfBySavedOrder(savedOrder: string[], merged: BookEntry[]): BookEntry[] {
  const map = new Map(merged.map((b) => [b.sessionId, b]));
  const mergedIds = merged.map((b) => b.sessionId);
  const novel = mergedIds.filter((id) => !savedOrder.includes(id));
  const kept = savedOrder.filter((id) => map.has(id));
  const finalIds = [...novel, ...kept];
  return finalIds.map((id) => map.get(id)!).filter(Boolean);
}

export function reconcileBookshelf(prev: BookEntry[], fresh: BookEntry[]): BookEntry[] {
  const merged = mergeBookshelf(prev, fresh);
  const ordered = orderBookshelfBySavedOrder(loadBookOrder(), merged);
  saveBookOrder(ordered);
  return ordered;
}

/** 樂觀插入一本（前景／背景生成）並維持穩定排序 */
export function prependBookToBookshelf(prev: BookEntry[], book: BookEntry): BookEntry[] {
  const next = [book, ...prev.filter((b) => b.sessionId !== book.sessionId)];
  const ordered = orderBookshelfBySavedOrder(loadBookOrder(), next);
  saveBookOrder(ordered);
  return ordered;
}

export function clearBookOrder() {
  localStorage.removeItem(BOOKSHELF_ORDER_KEY);
}
