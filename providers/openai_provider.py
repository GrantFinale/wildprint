"""OpenAI image generation provider.

Verified against openai SDK v1 (>=1.0), gpt-image-1 / dall-e-3 / dall-e-2.
Re-check if SDK major version changes.

Model-specific behavior this module encodes:

- gpt-image-1 (flagship):
    * Sizes: "1024x1024", "1024x1536", "1536x1024", "auto".
    * quality: "low" | "medium" | "high" | "auto". We default to "high" because
      wildprint output is intended for print.
    * Always returns base64 in response.data[0].b64_json. It does NOT accept
      a `response_format` parameter -- passing one will error.
    * No `seed` parameter.

- dall-e-3:
    * Sizes: "1024x1024", "1024x1792", "1792x1024".
    * Accepts response_format="b64_json" | "url". Default is "url", so we
      explicitly pass "b64_json" to avoid a second HTTP fetch.
    * No `seed` parameter.

- dall-e-2 (legacy):
    * Sizes: "256x256", "512x512", "1024x1024".
    * Same response_format semantics as dall-e-3.
    * No `seed` parameter. Not a priority but handled the same as dall-e-3.

None of the current OpenAI image models accept a deterministic seed, so
`supports_seed = False` and any seed passed to `generate()` is logged and
discarded.
"""
from __future__ import annotations


import base64

from providers.base import BaseProvider


class OpenAIProvider(BaseProvider):
    """Provider that calls the OpenAI Images API.

    The OpenAI client is constructed lazily inside ``generate()`` so that
    importing this module or instantiating the provider (e.g. for a dry-run
    or tests) does not require ``OPENAI_API_KEY`` to be set.
    """

    name = "openai"
    # OpenAI image models do not support deterministic seeds as of 2026-04.
    # Seed is recorded in metadata but not honored.
    supports_seed = False

    # Size tables per model family. Kept here so ``supported_sizes`` can be
    # called without touching the SDK.
    _GPT_IMAGE_SIZES = ["1024x1024", "1024x1536", "1536x1024", "auto"]
    _DALLE3_SIZES = ["1024x1024", "1024x1792", "1792x1024"]
    _DALLE2_SIZES = ["256x256", "512x512", "1024x1024"]

    def __init__(self, model: str, image_size: str, quality: str = "auto"):
        super().__init__(model=model, image_size=image_size)
        # Only used by gpt-image-1. dall-e-* ignores this field.
        # Accepted values: "low", "medium", "high", "auto".
        self.quality = quality

    def generate(
        self,
        prompt: str,
        seed: int | None = None,
        style_meta: dict | None = None,
    ) -> bytes:
        """Call the OpenAI Images API and return raw PNG bytes.

        Args:
            prompt: Text prompt to send to the model.
            seed: Ignored by every current OpenAI image model. If supplied,
                a one-line warning is emitted via ``self._log``.
            style_meta: Ignored by this provider; accepted for interface
                compatibility with providers that consult style metadata
                (e.g. Recraft's ``recraft_style`` substyle hint).

        Returns:
            Decoded image bytes (PNG) from the first item in the response.

        Raises:
            RuntimeError: On missing API key, missing SDK, SDK-level errors,
                or a malformed response payload. The original exception is
                chained via ``raise ... from exc`` where applicable.
        """
        del style_meta  # unused by OpenAI
        from config.settings import OPENAI_API_KEY

        if not OPENAI_API_KEY:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Export it or put it in .env "
                "before using the openai provider."
            )

        try:
            # Telemetry-wrapped OpenAI client (Phase 0.10). Falls back to
            # a no-op DB write when AI_LOGGING_ENABLED is unset, so this
            # is a safe drop-in replacement for `from openai import OpenAI`.
            from review_app.ai.openai_client import OpenAI
            from openai import (
                APIError,
                RateLimitError,
                BadRequestError,
                AuthenticationError,
                APIConnectionError,
            )
        except ImportError as e:
            raise RuntimeError(
                "openai package not installed. Run: pip install openai>=1.0"
            ) from e

        if seed is not None:
            self._log(
                f"seed={seed} ignored: OpenAI image models do not support "
                "deterministic seeds."
            )

        # Build kwargs based on model family. gpt-image-1 rejects
        # response_format; dall-e-* requires it to return b64_json.
        if self.model.startswith("gpt-image"):
            kwargs: dict = {
                "model": self.model,
                "prompt": prompt,
                "size": self.image_size,
                "n": 1,
                "quality": self.quality,
            }
        else:
            # dall-e-2, dall-e-3, or any unknown legacy model. Ask for b64
            # so we can return bytes without a second HTTP call.
            kwargs = {
                "model": self.model,
                "prompt": prompt,
                "size": self.image_size,
                "n": 1,
                "response_format": "b64_json",
            }

        client = OpenAI(api_key=OPENAI_API_KEY)

        try:
            response = client.images.generate(**kwargs)
        except AuthenticationError as exc:
            raise RuntimeError(
                "OpenAI authentication failed: check OPENAI_API_KEY."
            ) from exc
        except RateLimitError as exc:
            raise RuntimeError(
                "OpenAI rate limit hit -- back off and retry."
            ) from exc
        except BadRequestError as exc:
            # Billing hard-limit rejections come back as HTTP 400 with a
            # specific error code. Surface them with an actionable message
            # instead of hiding them inside a generic "bad request" string.
            code = ""
            try:
                body = getattr(exc, "body", None) or {}
                code = (body.get("error") or {}).get("code", "") or ""
            except Exception:  # noqa: BLE001
                code = ""
            exc_str = str(exc).lower()
            if code == "billing_hard_limit_reached" or "billing" in exc_str:
                raise RuntimeError(
                    "OpenAI billing hard limit reached. Add credit or raise "
                    "your cap at https://platform.openai.com/settings/"
                    "organization/billing/overview, then retry. "
                    "(No tokens or images were billed for this call.)"
                ) from exc
            if code == "content_policy_violation" or "content policy" in exc_str:
                raise RuntimeError(
                    f"OpenAI content policy rejected the prompt: {exc}. "
                    "Tighten the style block or species traits and retry."
                ) from exc
            raise RuntimeError(
                f"OpenAI rejected the prompt or parameters: {exc}"
            ) from exc
        except APIConnectionError as exc:
            raise RuntimeError(
                f"Network error reaching OpenAI: {exc}"
            ) from exc
        except APIError as exc:
            raise RuntimeError(f"OpenAI API error: {exc}") from exc

        try:
            b64_json = response.data[0].b64_json
        except (AttributeError, IndexError, TypeError) as exc:
            raise RuntimeError(
                "OpenAI returned an unexpected response shape; "
                "no data[0].b64_json found."
            ) from exc

        if b64_json is None:
            raise RuntimeError(
                "OpenAI returned no base64 payload. Check response_format "
                "and model compatibility."
            )

        image_bytes = base64.b64decode(b64_json)
        self._log(f"generated image: {len(image_bytes)} bytes")
        return image_bytes

    @classmethod
    def supported_sizes(cls, model: str) -> list[str]:
        """Return the valid ``size`` strings for a given OpenAI image model.

        Useful for callers that want to validate ``image_size`` before
        instantiating the provider. Not currently called from anywhere in
        wildprint, but kept here as the single source of truth for the
        per-model size tables documented at the top of this module.

        Args:
            model: Model identifier, e.g. ``"gpt-image-1"`` or ``"dall-e-3"``.

        Returns:
            A list of accepted ``"WxH"`` strings (plus ``"auto"`` for
            gpt-image-1). Returns an empty list for unknown models.
        """
        if model.startswith("gpt-image"):
            return list(cls._GPT_IMAGE_SIZES)
        if model == "dall-e-3":
            return list(cls._DALLE3_SIZES)
        if model == "dall-e-2":
            return list(cls._DALLE2_SIZES)
        return []
