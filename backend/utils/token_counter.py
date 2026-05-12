import tiktoken

_enc = tiktoken.get_encoding("cl100k_base")


class TokenCounter:
    def count(self, text: str) -> int:
        return len(_enc.encode(text))
