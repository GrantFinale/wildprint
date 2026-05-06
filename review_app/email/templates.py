"""Email template registry + Jinja2 renderer.

Each transactional email "kind" maps to a tuple of:
    (subject_template, html_template_path, text_template_path)

Subjects are short Jinja strings rendered inline; HTML and text bodies
live in `review_app/email/templates/*.j2` files.

Adding a new kind:
1. Drop new `<kind>.html.j2` and `<kind>.txt.j2` files in `templates/`.
2. Add an entry to ``KIND_TO_TEMPLATE`` below.
3. Add a test in ``tests/email/test_email.py`` that renders it with a
   representative payload and asserts on key strings.

Phase 0 ships real copy for ``email.order_confirmed`` and
``email.shipped``. The remaining kinds (in_production, delivered,
refunded, problem) ship as one-line stubs and will be filled in during
Phase 3 customer-comms work.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class KindNotFoundError(KeyError):
    """Raised when ``render_template(kind, ...)`` is called with an unknown kind."""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
# Tuple shape: (subject_jinja, html_template_filename, text_template_filename)
KIND_TO_TEMPLATE: Final[dict[str, tuple[str, str, str]]] = {
    "email.order_confirmed": (
        "Order confirmed — fishingposter.com #{{ order_number }}",
        "order_confirmed.html.j2",
        "order_confirmed.txt.j2",
    ),
    "email.in_production": (
        "Your fishingposter.com order is in production",
        "in_production.html.j2",
        "in_production.txt.j2",
    ),
    "email.shipped": (
        "Your fishingposter.com order has shipped",
        "shipped.html.j2",
        "shipped.txt.j2",
    ),
    "email.delivered": (
        "Your fishingposter.com order was delivered",
        "delivered.html.j2",
        "delivered.txt.j2",
    ),
    "email.refunded": (
        "Refund processed — fishingposter.com",
        "refunded.html.j2",
        "refunded.txt.j2",
    ),
    "email.problem": (
        "We hit a snag with your fishingposter.com order",
        "problem.html.j2",
        "problem.txt.j2",
    ),
}


# ---------------------------------------------------------------------------
# Jinja environment
# ---------------------------------------------------------------------------
_TEMPLATE_DIR: Final[Path] = Path(__file__).parent / "templates"

_env_singleton: Environment | None = None


def _get_env() -> Environment:
    """Lazy Jinja environment builder.

    `StrictUndefined` so a missing payload key fails loudly during dev/test
    rather than silently rendering an empty string. Autoescape on for
    `.html.j2` files only — text emails should not HTML-escape ampersands.
    """
    global _env_singleton
    if _env_singleton is None:
        _env_singleton = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(
                enabled_extensions=("html.j2", "html"),
                default_for_string=False,
            ),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
    return _env_singleton


def render_subject(kind: str, payload: dict[str, Any]) -> str:
    """Render only the subject line for `kind` (used by the worker)."""
    if kind not in KIND_TO_TEMPLATE:
        raise KindNotFoundError(f"Unknown email kind: {kind!r}")
    subject_tpl, _, _ = KIND_TO_TEMPLATE[kind]
    return _get_env().from_string(subject_tpl).render(**payload)


def render_template(kind: str, payload: dict[str, Any]) -> tuple[str, str]:
    """Render the (html, text) body pair for `kind`.

    Subject is rendered separately via :func:`render_subject` so callers
    that only need the subject (e.g. preview UIs) don't pay the cost of
    loading the full body templates.

    Raises
    ------
    KindNotFoundError
        If `kind` isn't in :data:`KIND_TO_TEMPLATE`.
    """
    if kind not in KIND_TO_TEMPLATE:
        raise KindNotFoundError(f"Unknown email kind: {kind!r}")
    _, html_path, text_path = KIND_TO_TEMPLATE[kind]
    env = _get_env()
    html = env.get_template(html_path).render(**payload)
    text = env.get_template(text_path).render(**payload)
    return html, text


def reset_for_tests() -> None:
    """Clear the cached environment; tests use this to force a fresh load."""
    global _env_singleton
    _env_singleton = None


__all__ = [
    "KIND_TO_TEMPLATE",
    "KindNotFoundError",
    "render_subject",
    "render_template",
    "reset_for_tests",
]
