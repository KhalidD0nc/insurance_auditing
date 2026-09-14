class AgentError(RuntimeError):
    """Base exception for agent failures."""


class AgentConfigurationError(AgentError):
    """Raised when required agent configuration is missing or invalid."""


class OpenRouterError(AgentError):
    """Raised when OpenRouter cannot return a usable completion."""
