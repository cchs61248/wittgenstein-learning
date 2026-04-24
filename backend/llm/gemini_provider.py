from typing import AsyncGenerator, Optional
import google.genai as genai
import google.genai.types as genai_types
from .base_provider import BaseLLMProvider, LLMMessage, LLMResponse, MessageRole


class GeminiProvider(BaseLLMProvider):
    def __init__(self, model: str = "gemini-2.0-flash", temperature: float = 0.7, max_tokens: int = 2048):
        super().__init__(model, temperature, max_tokens)
        self._client = genai.Client()

    @property
    def context_window(self) -> int:
        return 1_000_000

    def _build_config(self) -> genai_types.GenerateContentConfig:
        return genai_types.GenerateContentConfig(
            temperature=self.temperature,
            max_output_tokens=self.max_tokens,
        )

    def _to_contents(
        self, messages: list[LLMMessage]
    ) -> tuple[list[genai_types.Content], str, dict | None]:
        contents: list[genai_types.Content] = []
        last_user = ""
        last_user_attachment: dict | None = None
        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                continue
            role = "user" if msg.role == MessageRole.USER else "model"
            if msg.role == MessageRole.USER:
                last_user = msg.content
                last_user_attachment = msg.attachment
            parts = [genai_types.Part(text=msg.content)]
            if (
                msg.role == MessageRole.USER
                and msg.attachment
                and msg.attachment.get("gemini_file_uri")
            ):
                parts.append(
                    genai_types.Part(
                        file_data=genai_types.FileData(
                            file_uri=msg.attachment["gemini_file_uri"],
                            mime_type=msg.attachment.get("mime_type", "application/octet-stream"),
                        )
                    )
                )
            contents.append(genai_types.Content(role=role, parts=parts))
        # 最後一條 user message 作為當前輸入，不放進 history
        if contents and contents[-1].role == "user":
            contents.pop()
        return contents, last_user, last_user_attachment

    async def chat(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str] = None,
    ) -> LLMResponse:
        history, current_input, current_attachment = self._to_contents(messages)
        config = self._build_config()
        if system_prompt:
            config.system_instruction = system_prompt

        current_parts = [genai_types.Part(text=current_input)]
        if current_attachment and current_attachment.get("gemini_file_uri"):
            current_parts.append(
                genai_types.Part(
                    file_data=genai_types.FileData(
                        file_uri=current_attachment["gemini_file_uri"],
                        mime_type=current_attachment.get("mime_type", "application/octet-stream"),
                    )
                )
            )

        response = await self._client.aio.models.generate_content(
            model=self.model,
            contents=history + [genai_types.Content(role="user", parts=current_parts)],
            config=config,
        )
        return LLMResponse(
            content=response.text or "",
            input_tokens=response.usage_metadata.prompt_token_count if response.usage_metadata else 0,
            output_tokens=response.usage_metadata.candidates_token_count if response.usage_metadata else 0,
            model=self.model,
            finish_reason="stop",
        )

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        history, current_input, current_attachment = self._to_contents(messages)
        config = self._build_config()
        if system_prompt:
            config.system_instruction = system_prompt

        current_parts = [genai_types.Part(text=current_input)]
        if current_attachment and current_attachment.get("gemini_file_uri"):
            current_parts.append(
                genai_types.Part(
                    file_data=genai_types.FileData(
                        file_uri=current_attachment["gemini_file_uri"],
                        mime_type=current_attachment.get("mime_type", "application/octet-stream"),
                    )
                )
            )

        async for chunk in await self._client.aio.models.generate_content_stream(
            model=self.model,
            contents=history + [genai_types.Content(role="user", parts=current_parts)],
            config=config,
        ):
            if chunk.text:
                yield chunk.text
