"""角色權限政策：教材上傳角色集合、editor 的 provider/model 白名單。"""

MATERIAL_UPLOAD_ROLES = frozenset({"admin", "editor"})

EDITOR_PROVIDER = "monica"
EDITOR_ALLOWED_MODELS = frozenset({"claude-4.6-sonnet", "gemini-3-flash"})


def validate_provider_for_role(
    role: str,
    provider_name: str,
    model: str | None,
) -> str | None:
    """回傳錯誤訊息（未通過）或 None（通過）。"""
    if role != "editor":
        return None
    if (provider_name or "").strip().lower() != EDITOR_PROVIDER:
        return "此角色僅可使用 Monica 作為 AI 代理商"
    if model and model not in EDITOR_ALLOWED_MODELS:
        return "此角色僅可使用 Claude 4.6 Sonnet 或 Gemini 3 Flash 模型"
    return None
