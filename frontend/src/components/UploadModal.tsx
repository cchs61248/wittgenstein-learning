import { useState, useRef } from 'react';
import type { DragEvent, ChangeEvent } from 'react';
import { useSessionStore } from '../store/sessionStore';
import { uploadFile } from '../api/upload';
import type { ProviderType, DepthType } from '../types/messages';

interface Props {
  onStart: (
    provider: ProviderType,
    depth: DepthType,
    model: string,
    uploadedFileId?: string,
    content?: string
  ) => void;
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
};

export function UploadModal({ onStart }: Props) {
  const { token } = useSessionStore();
  const [content, setContent] = useState('');
  const [provider, setProvider] = useState<ProviderType>('claude');
  const [model, setModel] = useState(PROVIDER_MODELS.claude[0].id);
  const [depth, setDepth] = useState<DepthType>('intermediate');
  const [uploading, setUploading] = useState(false);
  const [uploadedFilename, setUploadedFilename] = useState<string | null>(null);
  const [uploadedFileId, setUploadedFileId] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

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
    onStart(provider, depth, model, uploadedFileId ?? undefined, text || undefined);
  };

  const models = PROVIDER_MODELS[provider];

  return (
    <div className="modal-overlay">
      <div className="modal-card">
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
              <span className="drop-icon">✓</span>
              <span className="drop-hint">{uploadedFilename}</span>
              <span className="drop-formats">點擊可重新上傳</span>
            </>
          ) : (
            <>
              <span className="drop-icon">📄</span>
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
