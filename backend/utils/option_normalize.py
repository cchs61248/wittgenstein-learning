"""選擇題 option 的 key 正規化（確定性、無 LLM）。

背景：LLM 產生 multiple_choice options 時 key 名稱不穩定，常吐 `option_id`
（受鄰近 `correct_option_id` 影響）。但全系統契約——前端型別 `{ id, text }`、
EvaluatorAgent（`opt.get("id")`）、測試 fixtures——一律用 `id`。

若不正規化，option 缺 `id` key：前端 `opt.id` 全為 undefined → `selectedOption === opt.id`
對每個選項都成立 → **所有選項同時被選中（全選）**、A/B/C/D 標籤空白、提交送出空答案、
EvaluatorAgent 也找不到選項文字。

在 QG 產生後與 DB 載回時各跑一次，可同時修好新題與已存的舊題（resume 即生效），
不需資料庫 migration。
"""
from typing import Any

# 依優先序嘗試的 option id 別名；LLM 實測最常用 option_id。
_OPTION_ID_ALIASES = ("id", "option_id", "optionId", "option", "label", "key")


def normalize_question_options(question: dict[str, Any]) -> dict[str, Any]:
    """就地把 question["options"] 內每個 option 的識別碼收斂成 `id` key。

    - 依 _OPTION_ID_ALIASES 取第一個非空值當 id；都缺則依序補 A/B/C/D…。
    - 移除殘留的 `option_id` 別名，避免雙 key 混淆。
    - 順手把 correct_option_id 轉成 strip 過的字串（與前端送出的選項 id 比對一致）。
    非選擇題或格式異常一律原樣返回，不拋例外。
    """
    if not isinstance(question, dict):
        return question
    options = question.get("options")
    if not isinstance(options, list) or not options:
        return question

    for idx, opt in enumerate(options):
        if not isinstance(opt, dict):
            continue
        opt_id = ""
        for alias in _OPTION_ID_ALIASES:
            val = opt.get(alias)
            if val is not None and str(val).strip():
                opt_id = str(val).strip()
                break
        if not opt_id:
            opt_id = chr(ord("A") + idx) if idx < 26 else str(idx + 1)
        opt["id"] = opt_id
        opt.pop("option_id", None)

    correct = question.get("correct_option_id")
    if correct is not None:
        question["correct_option_id"] = str(correct).strip()
    return question
