"""Decorators for the customer-facing /account/* routes."""
from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from flask import g, redirect, request, url_for

F = TypeVar("F", bound=Callable[..., Any])


def requires_customer(view: F) -> F:
    """Redirect anonymous visitors to the magic-link login page.

    Honors the ``next`` query param so the customer lands back on the
    originally requested URL after sign-in.
    """

    @wraps(view)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if getattr(g, "current_customer", None) is None:
            return redirect(url_for("account.login", next=request.url))
        return view(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


__all__ = ["requires_customer"]
