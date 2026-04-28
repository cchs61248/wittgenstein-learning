import { useEffect, useState, useRef } from 'react';
import type { DragEvent, ChangeEvent } from 'react';
import { useSessionStore } from '../store/sessionStore';
import { uploadFile } from '../api/upload';
import type { ProviderType, DepthType } from '../types/messages';

interface Props {
  onStart: (
    provider: ProviderType,
    depth: DepthType,
    model: string,
    questionMode: 'short_answer' | 'multiple_choice',
    uploadedFileId?: string,
    content?: string
  ) => void;
  onClose?: () => void;
}

function IconDoc({ className }: { className?: string }) {
  return (
    <svg className={className} width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
    </svg>
  );
}

function IconCheckCircle({ className }: { className?: string }) {
  return (
    <svg className={className} width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <circle cx="12" cy="12" r="10" />
      <path d="M8 12l3 3 5-6" />
    </svg>
  );
}

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
    { id: 'gemini-3-flash-preview',      label: 'Gemini 3 Flash Preview — 預設' },
    { id: 'gemini-3.1-pro-preview',      label: 'Gemini 3.1 Pro Preview — 品質優先' },
    { id: 'gemini-3.1-flash-lite-preview', label: 'Gemini 3.1 Flash Lite Preview — 輕量快速' },
  ],
  monica: [
    { id: 'claude-4.6-sonnet',       label: 'Claude 4.6 Sonnet — 預設' },
    { id: 'claude-4.5-sonnet',       label: 'Claude 4.5 Sonnet' },
    { id: 'claude-4.5-haiku',        label: 'Claude 4.5 Haiku — 快速' },
    { id: 'claude-4-sonnet',         label: 'Claude 4 Sonnet' },
    { id: 'claude-4-sonnet-think',   label: 'Claude 4 Sonnet Think — 深度思考' },
    { id: 'gpt-5.4',                 label: 'GPT-5.4' },
    { id: 'gpt-5.3-codex',           label: 'GPT-5.3 Codex' },
    { id: 'gpt-5.3',                 label: 'GPT-5.3' },
    { id: 'gpt-5.2',                 label: 'GPT-5.2' },
    { id: 'gpt-5.1',                 label: 'GPT-5.1' },
    { id: 'gpt-5',                   label: 'GPT-5' },
    { id: 'gpt-4o',                  label: 'GPT-4o' },
    { id: 'gpt-4o-mini',             label: 'GPT-4o mini — 輕量' },
    { id: 'gemini-3-1-pro',          label: 'Gemini 3.1 Pro' },
    { id: 'gemini-3-pro',            label: 'Gemini 3 Pro' },
    { id: 'gemini-3-flash',          label: 'Gemini 3 Flash — 快速' },
    { id: 'gemini-2.5-flash',        label: 'Gemini 2.5 Flash' },
  ],
};

export function UploadModal({ onStart, onClose }: Props) {
  const { token } = useSessionStore();
  const [content, setContent] = useState('');
  const [provider, setProvider] = useState<ProviderType>('claude');
  const [model, setModel] = useState(PROVIDER_MODELS.claude[0].id);
  const [depth, setDepth] = useState<DepthType>('intermediate');
  const [questionMode, setQuestionMode] = useState<'short_answer' | 'multiple_choice'>('short_answer');
  const [uploading, setUploading] = useState(false);
  const [uploadedFilename, setUploadedFilename] = useState<string | null>(null);
  const [uploadedFileId, setUploadedFileId] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose?.();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const handleProviderChange = (p: ProviderType) => {
    setProvider(p);
    setModel(PROVIDER_MODELS[p][0].id);
  };

  const handleFile = async (file: File) => {
    if (!token) return;
    setUploading(true);
    setUploadError(null);
    setUploadedFilename(null);
    setUploadedFileId(null);
    try {
      const result = await uploadFile(file, token);
      setUploadedFilename(result.filename);
      setUploadedFileId(result.file_id);
    } catch (e: unknown) {
      setUploadError(e instanceof Error ? e.message : '上傳失敗');
    } finally {
      setUploading(false);
    }
  };

  const handleFileInput = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
    e.target.value = '';
  };

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleFile(file);
  };

  const handleStart = () => {
    const text = content.trim();
    if (!uploadedFileId && text.length < 50) return;
    onStart(provider, depth, model, questionMode, uploadedFileId ?? undefined, text || undefined);
  };

  const models = PROVIDER_MODELS[provider];

  return (
    <div className="modal-overlay">
      <div className="modal-card">
        {onClose && (
          <button
            className="modal-close-btn"
            onClick={onClose}
            aria-label="關閉上傳視窗"
            type="button"
          >
            ×
          </button>
        )}
        <h2>上傳學習材料</h2>
        <p>上傳檔案或貼上文字，系統將自動切割成學習階段</p>

        <div
          className={`file-drop-zone${dragging ? ' dragging' : ''}${uploading ? ' loading' : ''}`}
          onClick={() => !uploading && fileInputRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept=".txt,.md,.pdf,.docx,.doc"
            style={{ display: 'none' }}
            onChange={handleFileInput}
          />
          {uploading ? (
            <span className="drop-hint">解析中...</span>
          ) : uploadedFilename ? (
            <>
              <IconCheckCircle className="drop-icon-svg" />
              <span className="drop-hint">{uploadedFilename}</span>
              <span className="drop-formats">點擊可重新上傳</span>
            </>
          ) : (
            <>
              <IconDoc className="drop-icon-svg" />
              <span className="drop-hint">點擊或拖曳檔案至此</span>
              <span className="drop-formats">支援 .txt .md .pdf .docx（不做本地文字解析）</span>
            </>
          )}
        </div>

        {uploadError && <p className="upload-error">{uploadError}</p>}

        <textarea
          value={content}
          onChange={(e) => { setContent(e.target.value); setUploadedFilename(null); setUploadedFileId(null); }}
          placeholder="可直接貼上學習材料（至少 50 字）；若已上傳檔案可留空"
          rows={8}
        />

        {content.trim().length > 0 && (
          <p className="char-count">{content.trim().length.toLocaleString()} 字元</p>
        )}

        <div className="modal-options">
          <div className="option-group">
            <label>AI 提供商</label>
            <select value={provider} onChange={(e) => handleProviderChange(e.target.value as ProviderType)}>
              <option value="claude">Anthropic Claude</option>
              <option value="openai">OpenAI</option>
              <option value="gemini">Google Gemini</option>
              <option value="monica">Monica（本地代理）</option>
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
          disabled={(!uploadedFileId && content.trim().length < 50) || uploading}
        >
          開始學習
        </button>
      </div>
    </div>
  );
}
