import { useState } from 'react';
import type { ProviderType, DepthType } from '../types/messages';

interface Props {
  onStart: (content: string, provider: ProviderType, depth: DepthType) => void;
}

export function UploadModal({ onStart }: Props) {
  const [content, setContent] = useState('');
  const [provider, setProvider] = useState<ProviderType>('claude');
  const [depth, setDepth] = useState<DepthType>('intermediate');

  const handleStart = () => {
    if (content.trim().length < 50) return;
    onStart(content.trim(), provider, depth);
  };

  return (
    <div className="modal-overlay">
      <div className="modal-card">
        <h2>上傳學習材料</h2>
        <p>貼上你想深入理解的文章、教材、或任何文字內容</p>

        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="貼上學習材料（至少 50 字）..."
          rows={10}
        />

        <div className="modal-options">
          <div className="option-group">
            <label>AI 模型</label>
            <select value={provider} onChange={(e) => setProvider(e.target.value as ProviderType)}>
              <option value="claude">Claude (Anthropic)</option>
              <option value="openai">GPT-4o (OpenAI)</option>
              <option value="gemini">Gemini (Google)</option>
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
          disabled={content.trim().length < 50}
        >
          開始學習
        </button>
      </div>
    </div>
  );
}
