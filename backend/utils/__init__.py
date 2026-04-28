import re


def extract_json(text: str) -> str:
    """從 LLM 回應中提取 JSON 字串，支援 JSON 出現在回應任意位置的情況。"""
    m = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', text)
    if m:
        return m.group(1).strip()
    # 嘗試找到第一個 { 或 [ 作為 JSON 起點
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        idx = text.find(start_char)
        if idx != -1:
            last_idx = text.rfind(end_char)
            if last_idx > idx:
                return text[idx:last_idx + 1].strip()
    return text.strip()
