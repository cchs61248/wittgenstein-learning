const SUMMARY_MAX = 180;

/** 舊版 V2 可能拼接全部 region 摘要；前端顯示時壓成單段。 */
export function formatKnowledgeMapSummary(
  summary: string,
  nodeCount: number,
): { display: string; isTruncated: boolean; full: string } {
  const full = (summary || '').trim();
  if (!full) {
    return {
      display: nodeCount > 0 ? `共 ${nodeCount} 個學習節點` : '',
      isTruncated: false,
      full: '',
    };
  }

  let head = full;
  const regionSplit = full.split(/\s+(?=本段)/);
  if (regionSplit.length > 1) {
    head = regionSplit[0].trim();
  }

  if (head.length > SUMMARY_MAX) {
    head = truncateAtSentence(head, SUMMARY_MAX);
  }

  const suffix = nodeCount > 1 ? `（共 ${nodeCount} 個學習節點）` : '';
  const display =
    suffix && !head.includes(suffix) ? `${head}${suffix}` : head;

  const isTruncated = display.length < full.length * 0.85;
  return { display, isTruncated, full };
}

function truncateAtSentence(text: string, maxLen: number): string {
  if (text.length <= maxLen) return text;
  const cut = text.slice(0, maxLen);
  for (const sep of ['。', '！', '？', '.', '!']) {
    const idx = cut.lastIndexOf(sep);
    if (idx >= 40) return cut.slice(0, idx + 1);
  }
  return cut.replace(/[，,、\s]+$/, '') + '…';
}
