import type { KnowledgeMapNode } from '../types/messages';

interface Props {
  nodes: KnowledgeMapNode[];
  summary: string;
  onConfirm: () => void;
}

export function KnowledgeMapModal({ nodes, summary, onConfirm }: Props) {
  return (
    <div className="modal-overlay">
      <div className="modal-card km-modal">
        <h2>📋 知識地圖</h2>
        {summary && <p className="km-summary">{summary}</p>}

        <p className="km-desc">
          AI 已將材料拆解為以下學習節點，請確認後開始學習：
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

        <button className="btn-primary btn-large" onClick={onConfirm}>
          確認，開始學習 →
        </button>
      </div>
    </div>
  );
}
