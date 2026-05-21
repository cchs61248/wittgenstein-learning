import re


def _slice_json(text: str) -> str | None:
    """從任意文字中擷取最外層 JSON object/array。"""
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        idx = text.find(start_char)
        if idx != -1:
            last_idx = text.rfind(end_char)
            if last_idx > idx:
                return text[idx:last_idx + 1].strip()
    return None


def extract_json(text: str) -> str:
    """從 LLM 回應中提取 JSON 字串，支援 JSON 出現在回應任意位置的情況。"""
    # 支援 ```json、````artifact 等 fenced block。若 block 本身不是純 JSON，
    # 再從 block 內的 content: |- 之類包裝中擷取第一個 JSON object/array。
    m = re.search(r'`{3,}[^\n`]*\n([\s\S]+?)\n?`{3,}', text)
    if m:
        block = m.group(1).strip()
        sliced = _slice_json(block)
        return sliced or block

    sliced = _slice_json(text)
    if sliced:
        return sliced
    return text.strip()
