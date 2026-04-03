"""
ProviderAdapter: unified interface for LLM backends.
Defaults to echo/mock mode. Can be replaced with real OpenAI/etc. calls.
"""
from typing import Any


class ProviderAdapter:
    def __init__(self, backend: str = "mock") -> None:
        self.backend = backend

    def complete(self, prompt: str, **kwargs: Any) -> str:
        """Return a completion string. In mock mode, returns a canned response."""
        if self.backend == "mock":
            return f"[mock response for: {prompt[:50]}]"
        raise NotImplementedError(f"Backend '{self.backend}' not implemented")
