import tiktoken

_enc = tiktoken.get_encoding("cl100k_base")


class TokenCounter:
    def count(self, text: str) -> int:
        return len(_enc.encode(text))

    def count_messages(self, messages: list[dict]) -> int:
        total = 0
        for m in messages:
            total += self.count(m.get("content", ""))
        return total
