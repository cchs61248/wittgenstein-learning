import { useEffect, useRef, useState } from 'react';
import type { DragEvent, ChangeEvent, KeyboardEvent } from 'react';
import { useSessionStore } from '../store/sessionStore';
import { uploadFile, uploadUrl, streamYoutubeAsr, type UploadUrlResult, type YoutubeAsrRequired, type YoutubeAsrEvent } from '../api/upload';
import { fetchDefaultProvider } from '../api/config';
import type { ProviderType, DepthType } from '../types/messages';

// ── 型別 ─────────────────────────────────────────────────────────

type SourceType = 'file' | 'url' | 'text';

interface Source {
  id: string;
  type: SourceType;
  label: string;
  fileId?: string;
  content?: string;
  charCount?: number;
  uploading?: boolean;
  error?: string;
}

interface Props {
  onStart: (
    provider: ProviderType,
    depth: DepthType,
    model: string,
    questionMode: 'short_answer' | 'multiple_choice',
    sources: Array<{ type: SourceType; file_id?: string; content?: string; label: string }>
  ) => void;
  onClose?: () => void;
}

// ── 圖示元件 ───────────────────────────────────────────────────

function IconFile() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
    </svg>
  );
}

function IconLink() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
      <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
    </svg>
  );
}

function IconText() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <line x1="4" y1="6" x2="20" y2="6" />
      <line x1="4" y1="10" x2="20" y2="10" />
      <line x1="4" y1="14" x2="14" y2="14" />
    </svg>
  );
}

function IconSpinner() {
  return (
    <svg className="source-spinner" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <circle cx="12" cy="12" r="10" strokeOpacity="0.25" />
      <path d="M12 2a10 10 0 0 1 10 10" />
    </svg>
  );
}

function IconX() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden>
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

// ── 常數 ────────────────────────────────────────────────────────

const PROVIDER_MODELS: Record<ProviderType, { id: string; label: string }[]> = {
  claude: [
    { id: 'claude-sonnet-4-6',         label: 'Claude Sonnet 4.6 — 平衡' },
    { id: 'claude-opus-4-7',           label: 'Claude Opus 4.7 — 最強' },
    { id: 'claude-haiku-4-5-20251001', label: 'Claude Haiku 4.5 — 快速' },
  ],
  openai: [
    { id: 'gpt-5.4-mini', label: 'GPT-5.4 mini — 穩定/成本平衡' },
    { id: 'gpt-5.4',      label: 'GPT-5.4 — 品質優先' },
  ],
  gemini: [
    { id: 'gemini-3-flash-preview',        label: 'Gemini 3 Flash Preview — 預設' },
    { id: 'gemini-3.1-pro-preview',        label: 'Gemini 3.1 Pro Preview — 品質優先' },
    { id: 'gemini-3.1-flash-lite-preview', label: 'Gemini 3.1 Flash Lite Preview — 輕量快速' },
  ],
  monica: [
    { id: 'claude-4.6-sonnet',     label: 'Claude 4.6 Sonnet — 預設' },
    { id: 'claude-4.5-sonnet',     label: 'Claude 4.5 Sonnet' },
    { id: 'claude-4.5-haiku',      label: 'Claude 4.5 Haiku — 快速' },
    { id: 'claude-4-sonnet',       label: 'Claude 4 Sonnet' },
    { id: 'claude-4-sonnet-think', label: 'Claude 4 Sonnet Think — 深度思考' },
    { id: 'gpt-5.4',               label: 'GPT-5.4' },
    { id: 'gpt-5.3-codex',         label: 'GPT-5.3 Codex' },
    { id: 'gpt-5.3',               label: 'GPT-5.3' },
    { id: 'gpt-5.2',               label: 'GPT-5.2' },
    { id: 'gpt-5.1',               label: 'GPT-5.1' },
    { id: 'gpt-5',                 label: 'GPT-5' },
    { id: 'gpt-4o',                label: 'GPT-4o' },
    { id: 'gpt-4o-mini',           label: 'GPT-4o mini — 輕量' },
    { id: 'gemini-3-1-pro',        label: 'Gemini 3.1 Pro' },
    { id: 'gemini-3-pro',          label: 'Gemini 3 Pro' },
    { id: 'gemini-3-flash',        label: 'Gemini 3 Flash — 快速' },
    { id: 'gemini-2.5-flash',      label: 'Gemini 2.5 Flash' },
  ],
  deepseek: [
    { id: 'deepseek-v4-flash', label: 'DeepSeek V4 Flash — 快速/低成本' },
    { id: 'deepseek-v4-pro',   label: 'DeepSeek V4 Pro — 品質優先' },
  ],
};

const MAX_SOURCES = 50;

function genId() {
  return Math.random().toString(36).slice(2, 10);
}

// ── 主元件 ────────────────────────────────────────────────────

export function UploadModal({ onStart, onClose }: Props) {
  const { token } = useSessionStore();
  const [sources, setSources] = useState<Source[]>([]);
  const [provider, setProvider] = useState<ProviderType>('claude');
  const [model, setModel] = useState(PROVIDER_MODELS.claude[0].id);
  const [depth, setDepth] = useState<DepthType>('intermediate');
  const [questionMode, setQuestionMode] = useState<'short_answer' | 'multiple_choice'>('short_answer');

  // 從後端取得預設 provider
  useEffect(() => {
    fetchDefaultProvider().then((p) => {
      setProvider(p);
      setModel(PROVIDER_MODELS[p][0].id);
    });
  }, []);

  // URL 輸入
  const [urlInput, setUrlInput] = useState('');
  const [urlLoading, setUrlLoading] = useState(false);
  const [urlError, setUrlError] = useState<string | null>(null);

  // YouTube：當字幕不可用時，改成使用者同意後再做 ASR（含進度條）
  const [asrRequired, setAsrRequired] = useState<YoutubeAsrRequired | null>(null);
  const [asrRunning, setAsrRunning] = useState(false);
  const [asrDownloadProgress, setAsrDownloadProgress] = useState(0);
  const [asrTranscribeProgress, setAsrTranscribeProgress] = useState(0);
  const [asrProgressError, setAsrProgressError] = useState<string | null>(null);
  const [asrSourceId, setAsrSourceId] = useState<string | null>(null);

  const asrAbortRef = useRef<AbortController | null>(null);

  // 文字輸入面板
  const [showTextInput, setShowTextInput] = useState(false);
  const [textInput, setTextInput] = useState('');

  // 拖曳
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Esc 關閉
  useEffect(() => {
    const onKeyDown = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') onClose?.();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const handleProviderChange = (p: ProviderType) => {
    setProvider(p);
    setModel(PROVIDER_MODELS[p][0].id);
  };

  // ── 新增來源 ───────────────────────────────────────────────

  const addFileSources = async (files: File[]) => {
    if (!token) return;
    if (sources.length + files.length > MAX_SOURCES) {
      alert(`最多只能加入 ${MAX_SOURCES} 個資料源`);
      return;
    }

    const placeholders: Source[] = files.map((f) => ({
      id: genId(),
      type: 'file',
      label: f.name,
      uploading: true,
    }));
    setSources((prev) => [...prev, ...placeholders]);

    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      const placeholder = placeholders[i];
      try {
        const result = await uploadFile(file, token);
        setSources((prev) =>
          prev.map((s) =>
            s.id === placeholder.id
              ? {
                  ...s,
                  fileId: result.file_id,
                  charCount: result.size,
                  uploading: false,
                  error: undefined,
                }
              : s
          )
        );
      } catch (e) {
        setSources((prev) =>
          prev.map((s) =>
            s.id === placeholder.id
              ? { ...s, uploading: false, error: e instanceof Error ? e.message : '上傳失敗' }
              : s
          )
        );
      }
    }
  };

  const handleFileInput = (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length) addFileSources(files);
    e.target.value = '';
  };

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    const files = Array.from(e.dataTransfer.files);
    if (files.length) addFileSources(files);
  };

  const handleAddUrl = async () => {
    const url = urlInput.trim();
    if (!url || !token) return;
    if (sources.length >= MAX_SOURCES) {
      alert(`最多只能加入 ${MAX_SOURCES} 個資料源`);
      return;
    }
    setUrlLoading(true);
    setUrlError(null);
    try {
      const result = await uploadUrl(url, token);
      if ('asr_required' in result && result.asr_required) {
        setAsrRequired(result);
        return;
      }

      const okResult = result as UploadUrlResult;
      setSources((prev) => [
        ...prev,
        {
          id: genId(),
          type: 'url',
          label: okResult.title || url,
          fileId: okResult.file_id,
          charCount: okResult.char_count,
        },
      ]);
      setUrlInput('');
    } catch (e) {
      setUrlError(e instanceof Error ? e.message : 'URL 擷取失敗');
    } finally {
      setUrlLoading(false);
    }
  };

  const handleConfirmAsr = async () => {
    if (!asrRequired || !token) return;

    // 若使用者多次點擊，避免重入
    if (asrRunning) return;

    setAsrRunning(true);
    setAsrProgressError(null);
    setAsrDownloadProgress(0);
    setAsrTranscribeProgress(0);

    const placeholderId = genId();
    setAsrSourceId(placeholderId);
    setSources((prev) => [
      ...prev,
      {
        id: placeholderId,
        type: 'url',
        label: 'YouTube 影片（轉寫中…）',
        uploading: true,
      },
    ]);

    const abort = new AbortController();
    asrAbortRef.current = abort;

    try {
      const final = await streamYoutubeAsr(
        asrRequired.url,
        token,
        (evt: YoutubeAsrEvent) => {
          if (evt.type !== 'progress') return;
          if (evt.stage === 'download') setAsrDownloadProgress(evt.progress);
          if (evt.stage === 'transcribe') setAsrTranscribeProgress(evt.progress);
        },
        abort.signal,
      );

      setSources((prev) =>
        prev.map((s) =>
          s.id === placeholderId
            ? {
                ...s,
                uploading: false,
                error: undefined,
                fileId: final.file_id,
                charCount: final.char_count,
                label: final.title || asrRequired.title || s.label,
              }
            : s
        ),
      );
      setAsrRequired(null);
      setAsrSourceId(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'YouTube 音訊轉寫失敗';
      setAsrProgressError(msg);
      setSources((prev) => prev.map((s) => (s.id === placeholderId ? { ...s, uploading: false, error: msg } : s)));
      asrAbortRef.current = null;
    } finally {
      setAsrRunning(false);
    }
  };

  const handleCancelAsr = () => {
    setAsrProgressError(null);
    setAsrRequired(null);
    if (asrAbortRef.current) {
      asrAbortRef.current.abort();
      asrAbortRef.current = null;
    }
    if (asrSourceId) {
      setSources((prev) => prev.filter((s) => s.id !== asrSourceId));
    }
    setAsrSourceId(null);
  };

  const handleUrlKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') handleAddUrl();
  };

  const handleAddText = () => {
    const text = textInput.trim();
    if (text.length < 50) return;
    if (sources.length >= MAX_SOURCES) {
      alert(`最多只能加入 ${MAX_SOURCES} 個資料源`);
      return;
    }
    setSources((prev) => [
      ...prev,
      {
        id: genId(),
        type: 'text',
        label: `貼上的文字（${text.length.toLocaleString()} 字元）`,
        content: text,
        charCount: text.length,
      },
    ]);
    setTextInput('');
    setShowTextInput(false);
  };

  const removeSource = (id: string) => {
    setSources((prev) => prev.filter((s) => s.id !== id));
  };

  // ── 啟動學習 ───────────────────────────────────────────────

  const readySources = sources.filter((s) => !s.uploading && !s.error);
  const canStart = readySources.length > 0;

  const handleStart = () => {
    if (!canStart) return;
    onStart(
      provider,
      depth,
      model,
      questionMode,
      readySources.map((s) => ({
        type: s.type,
        file_id: s.fileId,
        content: s.content,
        label: s.label,
      }))
    );
  };

  const models = PROVIDER_MODELS[provider];

  // ── 渲染 ───────────────────────────────────────────────────

  const sourceTypeIcon = (type: SourceType) => {
    if (type === 'file') return <IconFile />;
    if (type === 'url') return <IconLink />;
    return <IconText />;
  };

  return (
    <div className="modal-overlay">
      <div className="modal-card modal-card-wide">
        {onClose && (
          <button className="modal-close-btn" onClick={onClose} aria-label="關閉" type="button">
            ×
          </button>
        )}

        <h2>加入學習材料</h2>
        <p className="modal-subtitle">
          支援多個來源混合使用，AI 會自動合併相似主題，不重複教學
        </p>

        {/* ── 加入來源操作列 ── */}
        <div className="source-actions">
          {/* 檔案上傳區 */}
          <div
            className={`source-drop-zone${dragging ? ' dragging' : ''}`}
            onClick={() => fileInputRef.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            role="button"
            tabIndex={0}
            aria-label="點擊或拖曳上傳檔案"
            onKeyDown={(e) => e.key === 'Enter' && fileInputRef.current?.click()}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".txt,.md,.pdf,.docx,.doc,.pptx,.html,.epub"
              multiple
              style={{ display: 'none' }}
              onChange={handleFileInput}
            />
            <IconFile />
            <span>上傳檔案</span>
            <span className="source-drop-hint">PDF · DOCX · EPUB · TXT · MD</span>
          </div>

          {/* URL 輸入 */}
          <div className="source-url-group">
            <div className="source-url-input-row">
              <IconLink />
              <input
                type="url"
                className="source-url-input"
                placeholder="貼上網址或 YouTube 連結…"
                value={urlInput}
                onChange={(e) => { setUrlInput(e.target.value); setUrlError(null); }}
                onKeyDown={handleUrlKeyDown}
                disabled={urlLoading}
                aria-label="網址輸入"
              />
              <button
                className="btn-secondary btn-sm"
                onClick={handleAddUrl}
                disabled={!urlInput.trim() || urlLoading}
                type="button"
              >
                {urlLoading ? <IconSpinner /> : '加入'}
              </button>
            </div>
            {urlError && <p className="source-error-inline">{urlError}</p>}
          </div>

          {/* 純文字 */}
          <button
            className={`source-text-toggle${showTextInput ? ' active' : ''}`}
            onClick={() => setShowTextInput((v) => !v)}
            type="button"
          >
            <IconText />
            <span>貼上文字</span>
          </button>
        </div>

        {/* ── YouTube ASR 同意視窗（字幕不可用時） ── */}
        {asrRequired && (
          <div className="asr-consent-panel" role="dialog" aria-modal="true" aria-label="字幕不可用，需要使用音訊轉寫">
            <div className="asr-consent-title">字幕不可用，是否改用音訊轉寫？</div>
            <p className="asr-consent-sub">
              {asrRequired.reason || '目前無法擷取字幕。若你同意，系統會下載音訊並轉成逐字稿。'}
            </p>

            {!asrRunning ? (
              <div className="asr-consent-actions">
                <button className="btn-primary btn-large" onClick={handleConfirmAsr} type="button">
                  同意並開始轉寫
                </button>
                <button className="btn-ghost" style={{ width: '100%', marginTop: 10 }} onClick={handleCancelAsr} type="button">
                  稍後再說
                </button>
              </div>
            ) : (
              <div className="asr-progress-area" aria-live="polite">
                <div className="asr-progress-row">
                  <div className="asr-progress-label">音訊下載</div>
                  <div className="asr-progress-pct">{Math.round(asrDownloadProgress * 100)}%</div>
                </div>
                <div
                  className="progress-bar"
                  role="progressbar"
                  aria-label="音訊下載進度"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.round(asrDownloadProgress * 100)}
                >
                  <div className="progress-fill" style={{ width: `${Math.round(asrDownloadProgress * 100)}%` }} />
                </div>

                <div className="asr-progress-row">
                  <div className="asr-progress-label">逐字稿轉寫</div>
                  <div className="asr-progress-pct">{Math.round(asrTranscribeProgress * 100)}%</div>
                </div>
                <div
                  className="progress-bar"
                  role="progressbar"
                  aria-label="逐字稿轉寫進度"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.round(asrTranscribeProgress * 100)}
                >
                  <div className="progress-fill" style={{ width: `${Math.round(asrTranscribeProgress * 100)}%` }} />
                </div>

                {asrProgressError && <p className="source-error-inline">{asrProgressError}</p>}

                <button className="btn-ghost" style={{ width: '100%', marginTop: 10 }} onClick={handleCancelAsr} type="button" disabled={!asrRunning}>
                  取消轉寫
                </button>
              </div>
            )}
          </div>
        )}

        {/* 文字輸入展開面板 */}
        {showTextInput && (
          <div className="source-text-panel">
            <textarea
              className="source-textarea"
              value={textInput}
              onChange={(e) => setTextInput(e.target.value)}
              placeholder="貼上或輸入學習材料（至少 50 字）"
              rows={6}
              autoFocus
            />
            <div className="source-text-panel-footer">
              <span className="char-count">{textInput.trim().length.toLocaleString()} 字元</span>
              <div className="source-text-panel-btns">
                <button
                  className="btn-ghost btn-sm"
                  onClick={() => { setShowTextInput(false); setTextInput(''); }}
                  type="button"
                >
                  取消
                </button>
                <button
                  className="btn-primary btn-sm"
                  onClick={handleAddText}
                  disabled={textInput.trim().length < 50}
                  type="button"
                >
                  加入
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ── 資料源清單 ── */}
        <div className="source-list-header">
          <span>已加入的資料源</span>
          <span className="source-list-count">{sources.length} / {MAX_SOURCES}</span>
        </div>

        {sources.length === 0 ? (
          <div className="source-list-empty">尚未加入任何資料源</div>
        ) : (
          <ul className="source-list">
            {sources.map((src) => (
              <li key={src.id} className={`source-item${src.error ? ' has-error' : ''}`}>
                <span className="source-item-icon">{sourceTypeIcon(src.type)}</span>
                <span className="source-item-label" title={src.label}>{src.label}</span>
                {src.uploading && <IconSpinner />}
                {src.charCount != null && !src.uploading && (
                  <span className="source-item-meta">{src.charCount.toLocaleString()} 字元</span>
                )}
                {src.error && <span className="source-item-error">{src.error}</span>}
                <button
                  className="source-item-remove"
                  onClick={() => removeSource(src.id)}
                  aria-label={`移除 ${src.label}`}
                  type="button"
                >
                  <IconX />
                </button>
              </li>
            ))}
          </ul>
        )}

        {/* ── AI 設定 ── */}
        <div className="modal-options">
          <div className="option-group">
            <label>AI 提供商</label>
            <select value={provider} onChange={(e) => handleProviderChange(e.target.value as ProviderType)}>
              <option value="claude">Anthropic Claude</option>
              <option value="openai">OpenAI</option>
              <option value="gemini">Google Gemini</option>
              <option value="monica">Monica（本地代理）</option>
              <option value="deepseek">DeepSeek</option>
            </select>
          </div>

          <div className="option-group">
            <label>模型</label>
            <select value={model} onChange={(e) => setModel(e.target.value)}>
              {models.map((m) => (
                <option key={m.id} value={m.id}>{m.label}</option>
              ))}
            </select>
          </div>

          <div className="option-group">
            <label>難度深度</label>
            <select value={depth} onChange={(e) => setDepth(e.target.value as DepthType)}>
              <option value="beginner">入門</option>
              <option value="intermediate">進階</option>
              <option value="advanced">深度</option>
            </select>
          </div>

          <div className="option-group">
            <label>答題模式</label>
            <select value={questionMode} onChange={(e) => setQuestionMode(e.target.value as 'short_answer' | 'multiple_choice')}>
              <option value="short_answer">簡答模式</option>
              <option value="multiple_choice">選擇題模式（題數較多）</option>
            </select>
          </div>
        </div>

        <button
          className="btn-primary btn-large"
          onClick={handleStart}
          disabled={!canStart || sources.some((s) => s.uploading)}
        >
          {sources.some((s) => s.uploading) ? '上傳中…' : `開始學習（${readySources.length} 個來源）`}
        </button>
      </div>
    </div>
  );
}
