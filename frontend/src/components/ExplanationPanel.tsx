import { forwardRef, useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import { useSessionStore } from '../store/sessionStore';
import { fetchStageExplanation } from '../api/session';

type RefChunk = { id: string; chunk?: { chunk_id: string; quote: string } };

function SourceReferenceSection({ referencedChunks }: { referencedChunks: RefChunk[] }) {
  const [pinnedSourceId, setPinnedSourceId] = useState<string | null>(null);
  const [hoverSourceId, setHoverSourceId] = useState<string | null>(null);

  const displayId = pinnedSourceId ?? hoverSourceId;
  const activeChunk = displayId ? referencedChunks.find((x) => x.id === displayId) : undefined;

  return (
    <div
      className="source-reference-panel"
      onMouseLeave={() => setHoverSourceId(null)}
    >
      <div className="source-reference-title">來源追溯（移上標籤或點選，下方顯示全文摘錄）</div>
      <div className="source-reference-list">
        {referencedChunks.map(({ id }) => {
          const isShowing = displayId === id;
          return (
            <button
              key={id}
              type="button"
              className={`source-chip${isShowing ? ' source-chip--active' : ''}`}
              aria-expanded={isShowing}
              aria-label={`來源 ${id}，於下方顯示原文摘錄`}
              onMouseEnter={() => setHoverSourceId(id)}
              onClick={() =>
                setPinnedSourceId((cur) => {
                  const next = cur === id ? null : id;
                  if (next !== null) setHoverSourceId(null);
                  return next;
                })
              }
            >
              <span className="source-chip-label">[{id}]</span>
            </button>
          );
        })}
      </div>
      {activeChunk?.chunk?.quote && (
        <div
          className="source-quote-inline"
          role="region"
          aria-label={`${activeChunk.id} 原文摘錄`}
        >
          {activeChunk.chunk.quote}
        </div>
      )}
    </div>
  );
}

export const ExplanationPanel = forwardRef<HTMLDivElement>(function ExplanationPanel(_props, ref) {
  const explanationText = useSessionStore((s) => s.explanationText);
  const selectedStageId = useSessionStore((s) => s.selectedStageId);
  const currentStageId = useSessionStore((s) => s.currentStageId);
  const sessionId = useSessionStore((s) => s.sessionId);
  const token = useSessionStore((s) => s.token);
  const stages = useSessionStore((s) => s.stages);
  const stageExplanations = useSessionStore((s) => s.stageExplanations);
  const stageSourceChunks = useSessionStore((s) => s.stageSourceChunks);
  const setSelectedStage = useSessionStore((s) => s.setSelectedStage);
  const isExplanationLoading = useSessionStore((s) => s.isExplanationLoading);
  const [persistedFetch, setPersistedFetch] = useState<'idle' | 'loading' | 'done' | 'error' | 'empty'>('idle');

  const reviewStored =
    selectedStageId !== null ? (stageExplanations[selectedStageId] ?? '') : '';
  const hasReviewBody = selectedStageId !== null && reviewStored.trim().length > 0;
  // 切換 session 後 explanationText 被清空，但已完成章節的文字仍在 stageExplanations
  const currentStageStoredText = currentStageId !== null ? (stageExplanations[currentStageId] ?? '') : '';
  // 已選「回顧某章」時勿 fallback 到當前章串流／快取，避免無快取時誤顯示進行中章節內容
  const displayText = selectedStageId !== null ? reviewStored : explanationText || currentStageStoredText;
  const stageIdForDisplay = selectedStageId ?? currentStageId;
  const chunks = stageIdForDisplay !== null ? (stageSourceChunks[stageIdForDisplay] ?? []) : [];
  const refs = Array.from(new Set((displayText.match(/\[([A-Za-z0-9_.:-]+)\]/g) ?? []).map((m) => m.slice(1, -1))));
  const referencedChunks = refs
    .map((id) => ({ id, chunk: chunks.find((c) => c.chunk_id === id) }))
    .filter((x) => x.chunk);

  useEffect(() => {
    if (selectedStageId === null || !sessionId || !token) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setPersistedFetch('idle');
      return;
    }
    const stage = stages.find((s) => s.stage_id === selectedStageId);
    if (!stage || stage.status !== 'completed') {
      setPersistedFetch('idle');
      return;
    }
    const cached = (useSessionStore.getState().stageExplanations[selectedStageId] ?? '').trim();
    if (cached) {
      setPersistedFetch('idle');
      return;
    }
    const ac = new AbortController();
    setPersistedFetch('loading');
    fetchStageExplanation(token, sessionId, selectedStageId, ac.signal)
      .then((payload) => {
        if (ac.signal.aborted) return;
        if (!payload) {
          setPersistedFetch('error');
          return;
        }
        const text = (payload.explanation ?? '').trim();
        if (text) {
          useSessionStore.getState().storeStageExplanation(selectedStageId, text);
          setPersistedFetch('done');
        } else {
          setPersistedFetch('empty');
        }
      })
      .catch((e: unknown) => {
        if (e instanceof DOMException && e.name === 'AbortError') return;
        setPersistedFetch((prev) => (prev === 'loading' ? 'error' : prev));
      });
    return () => {
      ac.abort();
    };
  }, [selectedStageId, sessionId, token, stages]);

  const reviewingOtherWhileGenerating =
    isExplanationLoading &&
    selectedStageId !== null &&
    currentStageId !== null &&
    selectedStageId !== currentStageId;

  if (selectedStageId !== null && !hasReviewBody) {
    return (
      <div ref={ref} className="explanation-panel empty">
        {reviewingOtherWhileGenerating && (
          <div className="explanation-bg-gen-banner" role="status" aria-live="polite">
            新章節講解仍在背景生成。此處若無內文，代表本機尚未快取該章全文；可於目前章節完成後或重新整理頁面再試。
          </div>
        )}
        <div className="review-banner">
          <span>回顧模式</span>
          <button className="btn-ghost btn-sm" onClick={() => setSelectedStage(null)}>
            返回當前學習 →
          </button>
        </div>
        <div className="empty-ornament" aria-hidden="true" />
        {persistedFetch === 'loading' ? (
          <>
            <p className="empty-lead">正在從伺服器載入講解全文…</p>
            <p className="empty-hint">無需重新整理頁面；載入完成後會自動顯示。</p>
          </>
        ) : persistedFetch === 'error' ? (
          <>
            <p className="empty-lead">無法載入講解</p>
            <p className="empty-hint">請確認網路連線後，再點側欄該章重試。</p>
          </>
        ) : persistedFetch === 'empty' ? (
          <>
            <p className="empty-lead">伺服器尚無此章存檔</p>
            <p className="empty-hint">可能尚未寫入資料庫；請稍後再試或重新整理頁面。</p>
          </>
        ) : (
          <>
            <p className="empty-lead">此章講解尚未載入</p>
            <p className="empty-hint">
              若為已完成章節，將自動向伺服器請求全文；否則可於目前章節完成後或重新整理頁面再試。
            </p>
          </>
        )}
      </div>
    );
  }

  if (!displayText.trim()) {
    return (
      <div ref={ref} className="explanation-panel empty">
        <div className="empty-ornament" aria-hidden="true" />
        <p className="empty-lead">等待學習開始</p>
        <p className="empty-hint">上傳材料後，講解與提問會依序出現在這裡</p>
      </div>
    );
  }

  return (
    <div ref={ref} className="explanation-panel">
      {reviewingOtherWhileGenerating && (
        <div className="explanation-bg-gen-banner" role="status" aria-live="polite">
          新章節講解仍在背景生成中；完成後請點「返回當前學習」即可閱讀新章全文與題目。
        </div>
      )}
      {hasReviewBody && (
        <div className="review-banner">
          <span>回顧模式</span>
          <button className="btn-ghost btn-sm" onClick={() => setSelectedStage(null)}>
            返回當前學習 →
          </button>
        </div>
      )}
      <div className="markdown-content">
        <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>{displayText}</ReactMarkdown>
      </div>
      {referencedChunks.length > 0 && (
        <SourceReferenceSection key={String(stageIdForDisplay)} referencedChunks={referencedChunks} />
      )}
    </div>
  );
});
