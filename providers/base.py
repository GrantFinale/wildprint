"""Abstract base class for image generation providers."""
from __future__ import annotations


from abc import ABC, abstractmethod


class BaseProvider(ABC):
    """Abstract base class for all image generation providers."""

    name: str = "base"
    supports_seed: bool = False

    def __init__(self, model: str, image_size: str, quality: str = "auto", **_: object):
        """Initialize the provider with a model, image size, and quality hint.

        Args:
            model: Provider-specific model identifier.
            image_size: Image dimensions as a "WIDTHxHEIGHT" string.
            quality: Provider-specific quality hint ("low", "medium", "high",
                "auto"). Only honored by providers that support it (currently
                OpenAI's gpt-image-1). Mock and Recraft accept and ignore it.
            **_: Accepted for forward compatibility; unknown kwargs are
                silently ignored so callers can pass provider-specific flags
                without a type error.
        """
        self.model = model
        self.image_size = image_size
        self.quality = quality

    @abstractmethod
    def generate(
        self,
        prompt: str,
        seed: int | None = None,
        style_meta: dict | None = None,
    ) -> bytes:
        """Generate an image from a prompt.

        Args:
            prompt: The text prompt describing the image to generate.
            seed: Optional random seed for reproducibility (if supported).
            style_meta: Optional dict of style metadata (the full style
                record from ``styles.json``) that provider-specific
                implementations may consult for substyle hints (e.g.,
                ``recraft_style``). Providers that don't need it must
                accept and ignore the kwarg.

        Returns:
            PNG image bytes.
        """
        raise NotImplementedError

    def _log(self, msg: str) -> None:
        """Print a message prefixed with the provider name."""
        print(f"[{self.name}] {msg}")
