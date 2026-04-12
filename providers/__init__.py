"""Image generation provider registry."""

from providers.base import BaseProvider


def get_provider(name: str) -> BaseProvider:
    """Return a provider instance by name.

    Args:
        name: Provider identifier. Supported: "mock", "openai", "recraft".

    Returns:
        An uninitialized provider class (caller must instantiate with
        model and image_size). Actually returns the class, not an instance.

    Raises:
        ValueError: If the provider name is not recognized.
    """
    if name == "mock":
        from providers.mock_provider import MockProvider

        return MockProvider
    if name == "openai":
        from providers.openai_provider import OpenAIProvider

        return OpenAIProvider
    if name == "recraft":
        from providers.recraft_provider import RecraftProvider

        return RecraftProvider
    raise ValueError(
        f"Unknown provider: {name!r}. "
        "Supported providers: 'mock', 'openai', 'recraft'."
    )


__all__ = ["BaseProvider", "get_provider"]
