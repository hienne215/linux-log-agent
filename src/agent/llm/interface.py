"""Provider-neutral application types; intentionally independent of MCP."""
from dataclasses import dataclass
from typing import Any, Literal, Protocol, Sequence


class AdapterError(Exception):
    """Safe error code/message; never includes provider response or credentials."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    provider_id: str | None = None  # NOT an MCP logical/attempt/request ID.


@dataclass(frozen=True)
class Message:
    role: Literal['system', 'user', 'assistant', 'tool']
    content: str = ''
    tool_calls: tuple[ToolCall, ...] = ()
    tool_name: str | None = None


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None
    output_tokens: int | None
    generation_seconds: float | None
    provider_total_seconds: float | None
    tokens_per_second: float | None


@dataclass(frozen=True)
class Completion:
    kind: Literal['text', 'tool_calls']
    text: str
    tool_calls: tuple[ToolCall, ...]
    usage: Usage
    latency_seconds: float
    model: str
    done_reason: str
    redacted: bool = False


class LLMAdapter(Protocol):
    def complete(self, messages: Sequence[Message], tools: Sequence[ToolDefinition] = (),
                 *, require_tool: bool = False) -> Completion: ...
