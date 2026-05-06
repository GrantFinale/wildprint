"""Recraft v3 image generation provider.

API verified April 2026.

Endpoint:
    POST https://external.api.recraft.ai/v1/images/generations

Auth:
    Bearer token via ``Authorization: Bearer <RECRAFT_API_KEY>``. The key
    is read lazily inside ``generate()`` so that importing this module or
    instantiating the provider (e.g. for a dry-run) does not require
    ``RECRAFT_API_KEY`` to be set.

Seeds:
    Recraft v3 does NOT support deterministic seeds. If a seed is passed,
    we log a one-line warning and discard it; the seed should still be
    recorded in the caller's sidecar/manifest for bookkeeping.

Substyle mapping:
    Recraft supports three top-level styles:

        - ``realistic_image``
        - ``digital_illustration``
        - ``vector_illustration``

    Each top-level style also accepts slash-form substyles, e.g.
    ``vector_illustration/engraving``, ``digital_illustration/hand_drawn``,
    ``digital_illustration/watercolor``. The wildprint ``styles.json`` may
    attach a ``recraft_style`` key to a style record; if present it is
    passed through verbatim. Otherwise we fall back to the top-level
    ``digital_illustration``.

Valid sizes:
    Recraft only accepts a fixed allow-list of sizes. See
    ``RecraftProvider.VALID_SIZES`` for the full list. Arbitrary sizes
    (e.g. ``1920x1080``) will raise a clear error before hitting the API.

No external dependencies: the HTTP call uses ``urllib.request`` from the
standard library on purpose, to avoid pulling ``requests`` into
``requirements.txt``.
"""
from __future__ import annotations


import base64
import json as _json
import urllib.error
import urllib.request

from providers.base import BaseProvider


class RecraftProvider(BaseProvider):
    """Provider that calls the Recraft v3 Images API.

    The API key is read lazily inside ``generate()`` so that construction
    is side-effect free.
    """

    name = "recraft"
    # Recraft v3 does not honor deterministic seeds as of 2026-04. Seeds
    # passed to ``generate()`` are logged and discarded.
    supports_seed = False

    API_URL = "https://external.api.recraft.ai/v1/images/generations"

    # Fixed allow-list of sizes Recraft accepts. Arbitrary sizes will be
    # rejected server-side, so we validate up-front with a clearer error.
    VALID_SIZES = [
        "1024x1024",
        "1365x1024",
        "1024x1365",
        "1536x1024",
        "1024x1536",
        "1820x1024",
        "1024x1820",
        "2048x1024",
        "1024x2048",
        "1434x1024",
        "1024x1434",
        "1280x1024",
        "1024x1280",
        "1707x1024",
        "1024x1707",
    ]

    DEFAULT_STYLE = "digital_illustration"

    def __init__(
        self,
        model: str = "recraftv3",
        image_size: str = "1536x1024",
        quality: str = "auto",
    ):
        # Recraft v3 has no explicit quality control, but we accept the kwarg
        # for interface parity with OpenAI so callers don't have to branch.
        super().__init__(model=model, image_size=image_size, quality=quality)

    def generate(
        self,
        prompt: str,
        seed: int | None = None,
        style_meta: dict | None = None,
    ) -> bytes:
        """Call the Recraft v3 Images API and return raw PNG bytes.

        Args:
            prompt: Text prompt to send to the model.
            seed: Ignored by Recraft v3. If supplied, a one-line warning
                is emitted via ``self._log``; callers should still record
                the requested seed in their own metadata.
            style_meta: Optional full style record from ``styles.json``.
                If it is a dict containing a ``recraft_style`` key, that
                value is used verbatim as the Recraft ``style`` field
                (supports slash-form substyles like
                ``vector_illustration/engraving``). Otherwise falls back
                to ``RecraftProvider.DEFAULT_STYLE``.

        Returns:
            Decoded image bytes (PNG) from the first item in the response.

        Raises:
            RuntimeError: On missing API key, invalid size, HTTP error,
                network error, timeout, or malformed response. The
                original exception is chained via ``raise ... from exc``
                where applicable.
        """
        if seed is not None:
            self._log(
                f"seed={seed} ignored: Recraft v3 does not support "
                "deterministic seeds; record it upstream."
            )

        from config.settings import RECRAFT_API_KEY

        if not RECRAFT_API_KEY:
            raise RuntimeError(
                "RECRAFT_API_KEY is not set. Export it or put it in .env "
                "before using the recraft provider."
            )

        if self.image_size not in self.VALID_SIZES:
            raise RuntimeError(
                f"Recraft does not accept image_size={self.image_size!r}. "
                f"Recraft only allows a fixed set of sizes. Valid values: "
                f"{', '.join(self.VALID_SIZES)}."
            )

        # Resolve substyle. style_meta is the full style record; the
        # ``recraft_style`` key (if present) may be slash-form.
        if isinstance(style_meta, dict) and style_meta.get("recraft_style"):
            resolved_style = str(style_meta["recraft_style"])
        else:
            resolved_style = self.DEFAULT_STYLE
        self._log(f"resolved recraft style: {resolved_style}")

        body = {
            "prompt": prompt,
            "model": self.model,
            "style": resolved_style,
            "size": self.image_size,
            "n": 1,
            "response_format": "b64_json",
        }

        data = _json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.API_URL,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {RECRAFT_API_KEY}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

        # Telemetry-wrapped Recraft call (Phase 0.10). The `track` context
        # manager times the call and writes one ai_usage_log row when
        # AI_LOGGING_ENABLED is set; otherwise it's a no-op pass-through.
        # Exceptions raised inside the block propagate unchanged with
        # status='error' recorded.
        from review_app.ai import recraft_client as _recraft_log
        try:
            with _recraft_log.track(model=self.model, n=1.0):
                with urllib.request.urlopen(req, timeout=120) as resp:
                    payload = _json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Try to surface the server-provided error body for debugging.
            try:
                err_body = exc.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                err_body = "<unreadable error body>"
            raise RuntimeError(
                f"Recraft API HTTP {exc.code} {exc.reason}: {err_body}. "
                "Check your prompt, style, size, and API key."
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Network error reaching Recraft API: {exc.reason}. "
                "Check connectivity and DNS."
            ) from exc
        except TimeoutError as exc:
            raise RuntimeError(
                "Recraft API request timed out after 120s. "
                "Retry or reduce load."
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Unexpected error calling Recraft API: {exc}"
            ) from exc

        try:
            first = payload["data"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                "Recraft returned an unexpected response shape; "
                "no data[0] found."
            ) from exc

        b64 = first.get("b64_json") if isinstance(first, dict) else None
        if b64:
            image_bytes = base64.b64decode(b64)
            self._log(f"generated image: {len(image_bytes)} bytes")
            return image_bytes

        url = first.get("url") if isinstance(first, dict) else None
        if url:
            self._log(
                "b64_json missing from Recraft response; falling back to "
                "url fetch."
            )
            try:
                with urllib.request.urlopen(url, timeout=120) as img_resp:
                    image_bytes = img_resp.read()
            except urllib.error.HTTPError as exc:
                raise RuntimeError(
                    f"Recraft url fallback HTTP {exc.code} {exc.reason}."
                ) from exc
            except urllib.error.URLError as exc:
                raise RuntimeError(
                    f"Network error fetching Recraft url fallback: {exc.reason}."
                ) from exc
            except TimeoutError as exc:
                raise RuntimeError(
                    "Recraft url fallback timed out after 120s."
                ) from exc
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"Unexpected error fetching Recraft url fallback: {exc}"
                ) from exc
            self._log(f"generated image (via url): {len(image_bytes)} bytes")
            return image_bytes

        raise RuntimeError("Recraft returned neither b64_json nor url")

    @classmethod
    def supported_sizes(cls) -> list[str]:
        """Return the fixed list of sizes Recraft v3 accepts.

        Useful for callers that want to validate ``image_size`` before
        instantiating the provider.
        """
        return list(cls.VALID_SIZES)

    @classmethod
    def supported_styles(cls) -> list[str]:
        """Return the three top-level Recraft styles.

        Note: Recraft also supports slash-form substyles such as
        ``vector_illustration/engraving``, ``digital_illustration/hand_drawn``,
        and ``digital_illustration/watercolor``. Those are passed through
        verbatim via ``style_meta["recraft_style"]`` on the style record
        and are not enumerated here.
        """
        return ["realistic_image", "digital_illustration", "vector_illustration"]
