import { useState } from 'react';
import type { QualityWarnings } from '../types/messages';

interface Props {
  warnings: QualityWarnings;
  onReupload?: () => void;
}

export function QualityWarningBanner({ warnings, onReupload }: Props) {
  const [expanded, setExpanded] = useState(false);
  const count = warnings.missing_options?.length ?? 0;
  const label =
    count > 0
      ? `本教材複雜度較高，自動切分有 ${count} 項建議優化`
      : '本教材複雜度較高，自動切分品質未達最佳';

  return (
    <div className="quality-warning-banner" role="alert">
      <div className="quality-warning-banner__head">
        <span className="quality-warning-banner__icon" aria-hidden="true">
          ⚠️
        </span>
        <span>{label}</span>
        <button
          type="button"
          className="btn-ghost quality-warning-banner__toggle"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? '收合詳情' : '查看詳情'}
        </button>
        {onReupload && (
          <button type="button" className="btn-ghost" onClick={onReupload}>
            拆檔重新上傳
          </button>
        )}
      </div>
      {expanded && (
        <div className="quality-warning-banner__detail">
          {warnings.reason && <p>{warnings.reason}</p>}
          {count > 0 && (
            <ul>
              {warnings.missing_options!.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
