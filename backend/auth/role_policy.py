"""角色權限政策：教材上傳角色集合、editor 的 provider/model 白名單。"""

MATERIAL_UPLOAD_ROLES = frozenset({"admin", "editor"})

EDITOR_PROVIDER = "monica"
# 需與 frontend/src/components/UploadModal.tsx 的 EDITOR_MODELS 保持一致。
# = claude-4.6-sonnet + aimodels 中所有免費（basic_query）模型。
EDITOR_ALLOWED_MODELS = frozenset({
    "gemini-3-flash",
    "claude-4.6-sonnet",
    "claude-haiku-5.5",
    "gpt-6-luna",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "deepseek-v4.1-flash",
    "qwen3.8-flash",
    "glm-5.3-flash",
})


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
        return "此角色僅可使用 Claude 4.6 Sonnet 或 Monica 免費模型"
    return None
