import { useState, useRef, useEffect } from 'react';
import type { BookEntry } from '../api/session';
import { StageMap } from './StageMap';
import { getSessionLayoutPrefs, patchSessionLayoutPrefs } from '../utils/sessionLayoutPrefs';
import { UI_STATE_SYNCED_EVENT } from '../utils/userUiStateSync';

interface BookshelfPanelProps {
  books: BookEntry[];
  activeSessionId: string | null;
  onSwitch: (entry: BookEntry) => void;
  onNewMaterial: () => void;
  disableNewMaterial?: boolean;
  canAddMaterial?: boolean;
  onRename: (sessionId: string, title: string) => Promise<void>;
  onDelete: (sessionId: string) => Promise<void>;
  onRetry: (sessionId: string) => Promise<void>;
  onDismiss: (sessionId: string) => Promise<void>;
}

function statusLabel(status: BookEntry['status']): string {
  if (status === 'active') return '學習中';
  if (status === 'completed') return '已完成';
  if (status === 'generating') return '生成中…';
  if (status === 'failed') return '生成失敗';
  return '待確認';
}

function statusClass(status: BookEntry['status']): string {
  if (status === 'active') return 'status-active';
  if (status === 'completed') return 'status-completed';
  if (status === 'generating') return 'status-generating';
  if (status === 'failed') return 'status-failed';
  return 'status-pending';
}

interface BookItemProps {
  entry: BookEntry;
  isActive: boolean;
  onSwitch: () => void;
  onRename: (title: string) => Promise<void>;
  onDelete: () => Promise<void>;
  onRetry: (sessionId: string) => Promise<void>;
  onDismiss: (sessionId: string) => Promise<void>;
}

function BookItem({ entry, isActive, onSwitch, onRename, onDelete, onRetry, onDismiss }: BookItemProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState(entry.title);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isEditing) inputRef.current?.focus();
  }, [isEditing]);

  const handleRenameStart = (e: React.MouseEvent) => {
    e.stopPropagation();
    setEditValue(entry.title);
    setIsEditing(true);
  };

  const handleRenameSave = async () => {
    const trimmed = editValue.trim();
    if (!trimmed) { setIsEditing(false); setEditValue(entry.title); return; }
    if (trimmed === entry.title) { setIsEditing(false); return; }
    setIsSaving(true);
    await onRename(trimmed);
    setIsSaving(false);
    setIsEditing(false);
  };

  const handleRenameKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleRenameSave();
    if (e.key === 'Escape') { setIsEditing(false); setEditValue(entry.title); }
  };

  const handleDeleteStart = (e: React.MouseEvent) => {
    e.stopPropagation();
    setIsDeleting(true);
  };

  const handleDeleteConfirm = async (e: React.MouseEvent) => {
    e.stopPropagation();
    await onDelete();
    setIsDeleting(false);
  };

  const handleDeleteCancel = (e: React.MouseEvent) => {
    e.stopPropagation();
    setIsDeleting(false);
  };

  const pct = entry.totalStages > 0
    ? Math.round((entry.completedStages / entry.totalStages) * 100)
    : 0;

  const isGenerating = entry.status === 'generating';

  return (
    <div
      className={`book-item${isActive ? ' is-active' : ''}${isGenerating ? ' is-generating' : ''}`}
      onClick={!isEditing && !isDeleting && !isGenerating ? onSwitch : undefined}
      role="button"
      tabIndex={isGenerating ? -1 : 0}
      aria-pressed={isActive}
      aria-disabled={isGenerating}
      onKeyDown={(e) => { if (!isEditing && !isDeleting && !isGenerating && (e.key === 'Enter' || e.key === ' ')) onSwitch(); }}
    >
      <div className="book-item-top">
        {isEditing ? (
          <input
            ref={inputRef}
            className="book-title-input"
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            onBlur={handleRenameSave}
            onKeyDown={handleRenameKeyDown}
            disabled={isSaving}
            aria-label="書本名稱"
            onClick={(e) => e.stopPropagation()}
          />
        ) : (
          <span className="book-title-text" title={entry.title}>
            {entry.title}
          </span>
        )}

        {isDeleting ? (
          <div className="book-delete-confirm">
            <span>確定刪除？</span>
            <button
              className="book-delete-confirm-yes"
              onClick={handleDeleteConfirm}
              aria-label="確定刪除此書本"
            >
              確定
            </button>
            <button
              className="book-delete-confirm-no"
              onClick={handleDeleteCancel}
              aria-label="取消刪除"
            >
              取消
            </button>
          </div>
        ) : (
          !isEditing && !isGenerating && (
            <div className="book-actions">
              <button
                className="book-icon-btn"
                onClick={handleRenameStart}
                aria-label={`重新命名「${entry.title}」`}
                title="重新命名"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                  <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                </svg>
              </button>
              <button
                className="book-icon-btn delete"
                onClick={handleDeleteStart}
                aria-label={`刪除「${entry.title}」`}
                title="刪除書本"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <polyline points="3 6 5 6 21 6"/>
                  <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                  <path d="M10 11v6M14 11v6"/>
                  <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
                </svg>
              </button>
            </div>
          )
        )}
      </div>

      {entry.status === 'failed' && (
        <div className="book-failed-row">
          <span className="book-failed-hint">
            課程生成中斷或逾時，尚未產生完整結果。
          </span>
          <div className="book-failed-actions">
            <button
              className="book-retry-btn"
              onClick={(e) => { e.stopPropagation(); onRetry(entry.sessionId); }}
              aria-label={`重新生成「${entry.title}」`}
            >
              重新生成
            </button>
            <button
              className="book-dismiss-btn"
              onClick={(e) => { e.stopPropagation(); onDismiss(entry.sessionId); }}
              aria-label={`移除「${entry.title}」`}
            >
              移除
            </button>
          </div>
        </div>
      )}

      <div className="book-progress-row">
        <div className="book-progress-bar-track" aria-hidden="true">
          <div className="book-progress-bar-fill" style={{ width: `${pct}%` }} />
        </div>
        <span className="book-progress-label" aria-label={`${entry.completedStages} / ${entry.totalStages} 章已完成`}>
          {entry.completedStages}/{entry.totalStages}
        </span>
        <span className={`book-status-badge ${statusClass(entry.status)}`}>
          {statusLabel(entry.status)}
        </span>
      </div>
    </div>
  );
}

export function BookshelfPanel({
  books,
  activeSessionId,
  onSwitch,
  onNewMaterial,
  disableNewMaterial = false,
  canAddMaterial = true,
  onRename,
  onDelete,
  onRetry,
  onDismiss,
}: BookshelfPanelProps) {
  const [view, setView] = useState<'list' | 'map'>('list');
  const [viewingSessionId, setViewingSessionId] = useState<string | null>(null);
  const [hydrateTick, setHydrateTick] = useState(0);
  const mapScrollRef = useRef<HTMLDivElement | null>(null);
  const lastHydratedSession = useRef<string | null>(null);

  const viewingBook = viewingSessionId
    ? books.find((b) => b.sessionId === viewingSessionId) ?? null
    : null;

  useEffect(() => {
    const onSynced = () => {
      lastHydratedSession.current = null;
      setHydrateTick((t) => t + 1);
    };
    window.addEventListener(UI_STATE_SYNCED_EVENT, onSynced);
    return () => window.removeEventListener(UI_STATE_SYNCED_EVENT, onSynced);
  }, []);

  // 依目前前景 session 還原：是否在「章節列表」內（重整後不跳回書櫃總覽）
  useEffect(() => {
    if (!activeSessionId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setView('list');
      setViewingSessionId(null);
      lastHydratedSession.current = null;
      return;
    }
    if (lastHydratedSession.current === activeSessionId) return;
    lastHydratedSession.current = activeSessionId;
    const p = getSessionLayoutPrefs(activeSessionId);
    if (p?.bookshelfPanelView === 'map') {
      setView('map');
      setViewingSessionId(activeSessionId);
    } else {
      setView('list');
      setViewingSessionId(null);
    }
  }, [activeSessionId, hydrateTick]);

  // 書本被刪除時若仍停在 map，退回列表
  useEffect(() => {
    if (view !== 'map' || !viewingSessionId) return;
    if (!books.some((b) => b.sessionId === viewingSessionId)) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setView('list');
      setViewingSessionId(null);
    }
  }, [books, view, viewingSessionId]);

  useEffect(() => {
    if (view !== 'map' || !activeSessionId) return;
    const el = mapScrollRef.current;
    if (!el) return;
    const top = getSessionLayoutPrefs(activeSessionId)?.bookshelfMapScrollTop ?? 0;
    const id = requestAnimationFrame(() => {
      if (mapScrollRef.current) mapScrollRef.current.scrollTop = top;
    });
    return () => cancelAnimationFrame(id);
  }, [view, activeSessionId, viewingSessionId, books.length]);

  useEffect(() => {
    if (view !== 'map' || !activeSessionId) return;
    const el = mapScrollRef.current;
    if (!el) return;
    let tid: ReturnType<typeof setTimeout> | undefined;
    const onScroll = () => {
      clearTimeout(tid);
      tid = setTimeout(() => {
        patchSessionLayoutPrefs(activeSessionId, { bookshelfMapScrollTop: el.scrollTop });
      }, 200);
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      el.removeEventListener('scroll', onScroll);
      clearTimeout(tid);
    };
  }, [view, activeSessionId]);

  const handleBookSelect = (entry: BookEntry) => {
    if (entry.status === 'generating' || entry.status === 'failed') return;
    patchSessionLayoutPrefs(entry.sessionId, { bookshelfPanelView: 'map' });
    setViewingSessionId(entry.sessionId);
    setView('map');
    onSwitch(entry);
  };

  const handleBackToList = () => {
    const sid = viewingSessionId ?? activeSessionId;
    if (sid) patchSessionLayoutPrefs(sid, { bookshelfPanelView: 'list' });
    setView('list');
    setViewingSessionId(null);
  };

  if (view === 'map') {
    return (
      <div className="bookshelf-panel">
        <div className="bookshelf-map-header">
          <button
            className="bookshelf-back-btn"
            onClick={handleBackToList}
            aria-label="返回書櫃列表"
          >
            ← 書櫃
          </button>
          {viewingBook && (
            <span className="bookshelf-map-title" title={viewingBook.title}>
              {viewingBook.title}
            </span>
          )}
        </div>
        <div ref={mapScrollRef} className="bookshelf-map-body">
          <StageMap hideHeading />
        </div>
      </div>
    );
  }

  return (
    <div className="bookshelf-panel">
      <div className="bookshelf-header">
        <span>書櫃</span>
        {canAddMaterial && (
          <button
            className="bookshelf-add-btn"
            onClick={onNewMaterial}
            disabled={disableNewMaterial}
            aria-label="新增學習材料"
            title={disableNewMaterial ? '目前有教材正在生成，完成後才能新增' : '新增學習材料'}
          >
            ＋ 新增材料
          </button>
        )}
      </div>

      <div className="bookshelf-list">
        {books.length === 0 ? (
          <div className="bookshelf-empty">尚無學習材料</div>
        ) : (
          books.map((entry) => (
            <BookItem
              key={entry.sessionId}
              entry={entry}
              isActive={entry.sessionId === activeSessionId}
              onSwitch={() => handleBookSelect(entry)}
              onRename={(title) => onRename(entry.sessionId, title)}
              onDelete={() => onDelete(entry.sessionId)}
              onRetry={onRetry}
              onDismiss={onDismiss}
            />
          ))
        )}
      </div>
    </div>
  );
}
