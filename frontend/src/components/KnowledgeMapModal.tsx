import type { KnowledgeMapNode, QualityWarnings } from '../types/messages';
import { QualityWarningBanner } from './QualityWarningBanner';

interface Props {
  nodes: KnowledgeMapNode[];
  summary: string;
  qualityWarnings?: QualityWarnings;
  onConfirm: () => void;
  onCancel?: () => void;
  onReupload?: () => void;
}

export function KnowledgeMapModal({
  nodes,
  summary,
  qualityWarnings,
  onConfirm,
  onCancel,
  onReupload,
}: Props) {
  return (
    <div className="modal-overlay">
      <div className="modal-card km-modal">
        <h2>知識地圖</h2>
        {qualityWarnings?.splitter_verifier_failed && (
          <QualityWarningBanner warnings={qualityWarnings} onReupload={onReupload} />
        )}
        {summary && <p className="km-summary">{summary}</p>}

        <p className="km-desc">
          AI 已將材料拆解為以下學習節點，請確認後開始學習：
        </p>
        <p className="km-contract">
          ✅ 確認後即進入「覆蓋合約」——以上所有節點都將被完整覆蓋，系統不會跳過任何一個。
        </p>

        <div className="km-table-wrap">
          <table className="km-table">
            <thead>
              <tr>
                <th>節點</th>
                <th>知識點名稱</th>
              </tr>
            </thead>
            <tbody>
              {nodes.map((n) => (
                <tr key={n.stage_id}>
                  <td className="km-node-id">{n.node_id}</td>
                  <td>{n.title}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className="km-count">共 {nodes.length} 個節點</p>

        <div className="km-actions">
          {onCancel && (
            <button className="btn-ghost" onClick={onCancel}>
              稍後再說
            </button>
          )}
          <button className="btn-primary btn-large" onClick={onConfirm}>
            確認，開始學習 →
          </button>
        </div>
      </div>
    </div>
  );
}
