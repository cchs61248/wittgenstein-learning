import { useState, useRef, useEffect } from 'react';
import type { BookEntry } from '../api/session';
import { StageMap } from './StageMap';

interface BookshelfPanelProps {
  books: BookEntry[];
  activeSessionId: string | null;
  onSwitch: (entry: BookEntry) => void;
  onNewMaterial: () => void;
  onRename: (sessionId: string, title: string) => Promise<void>;
  onDelete: (sessionId: string) => Promise<void>;
}

function statusLabel(status: BookEntry['status']): string {
  if (status === 'active') return '學習中';
  if (status === 'completed') return '已完成';
  return '待確認';
}

function statusClass(status: BookEntry['status']): string {
  if (status === 'active') return 'status-active';
  if (status === 'completed') return 'status-completed';
  return 'status-pending';
}

interface BookItemProps {
  entry: BookEntry;
  isActive: boolean;
  onSwitch: () => void;
  onRename: (title: string) => Promise<void>;
  onDelete: () => Promise<void>;
}

function BookItem({ entry, isActive, onSwitch, onRename, onDelete }: BookItemProps) {
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

  return (
    <div
      className={`book-item${isActive ? ' is-active' : ''}`}
      onClick={!isEditing && !isDeleting ? onSwitch : undefined}
      role="button"
      tabIndex={0}
      aria-pressed={isActive}
      onKeyDown={(e) => { if (!isEditing && !isDeleting && (e.key === 'Enter' || e.key === ' ')) onSwitch(); }}
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
          !isEditing && (
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
  onRename,
  onDelete,
}: BookshelfPanelProps) {
  return (
    <div className="bookshelf-panel">
      <div className="bookshelf-header">
        <span>書櫃</span>
        <button
          className="bookshelf-add-btn"
          onClick={onNewMaterial}
          aria-label="新增學習材料"
        >
          ＋ 新增材料
        </button>
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
              onSwitch={() => onSwitch(entry)}
              onRename={(title) => onRename(entry.sessionId, title)}
              onDelete={() => onDelete(entry.sessionId)}
            />
          ))
        )}
      </div>

      {books.length > 0 && (
        <>
          <div className="bookshelf-stage-divider">當前進度</div>
          <StageMap hideHeading />
        </>
      )}
    </div>
  );
}
