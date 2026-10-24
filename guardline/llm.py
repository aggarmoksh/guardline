"""LLM adapter. Default provider: Anthropic API, model string from .env (LLM_MODEL)."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv


class LLMError(Exception):
    pass


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class LLM:
    def __init__(self, model: str | None = None):
        load_dotenv()
        import anthropic

        self.model = model or os.environ["LLM_MODEL"]
        self._client = anthropic.Anthropic(timeout=30.0, max_retries=1)
        self.usage = Usage()

    def _track(self, resp) -> None:
        self.usage.calls += 1
        self.usage.input_tokens += resp.usage.input_tokens
        self.usage.output_tokens += resp.usage.output_tokens

    def forced_tool_call(self, system: str, user: str, tool: dict) -> dict:
        """One forced tool call; returns the tool input dict."""
        try:
            resp = self._client.messages.create(
                model=self.model, max_tokens=400, system=system,
                messages=[{"role": "user", "content": user}],
                tools=[tool], tool_choice={"type": "tool", "name": tool["name"]},
            )
        except Exception as e:  # network, auth, rate limit
            raise LLMError(type(e).__name__) from None
        self._track(resp)
        for block in resp.content:
            if block.type == "tool_use" and block.name == tool["name"]:
                return dict(block.input)
        raise LLMError("no tool call returned")

    def complete(self, system: str, user: str, max_tokens: int = 120) -> str:
        try:
            resp = self._client.messages.create(
                model=self.model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as e:
            raise LLMError(type(e).__name__) from None
        self._track(resp)
        return "".join(b.text for b in resp.content if b.type == "text").strip()
