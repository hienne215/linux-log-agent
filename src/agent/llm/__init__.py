"""Public adapter API; importing does not connect to any service."""
from .interface import AdapterError, Completion, LLMAdapter, Message, ToolCall, ToolDefinition, Usage
from .ollama import OllamaAdapter, OllamaConfig

__all__ = ['AdapterError', 'Completion', 'LLMAdapter', 'Message', 'ToolCall',
           'ToolDefinition', 'Usage', 'OllamaAdapter', 'OllamaConfig']
