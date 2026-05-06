"""Smoke test: send one transactional email through Resend end-to-end.

Run manually after Phase 0.5 is wired up, after any Resend credential
rotation, and as a quick post-deploy sanity check on staging/prod.

Usage:
    RESEND_API_KEY=re_... EMAIL_FROM=hello@fishingposter.com \
        python scripts/smoke_email.py [recipient@example.com]

If no recipient is given, defaults to benedictmt@gmail.com (Grant's
inbox), which is the address used for the original Phase 0.5 acceptance
verification.

What this checks:
    1. Templates render (order_confirmed kind).
    2. Resend authenticates (API key valid).
    3. Domain is verified for the configured EMAIL_FROM.
    4. End-to-end delivery (you confirm by checking the inbox).

This deliberately bypasses the outbox — it's a direct Resend call. The
outbox path is exercised by the unit test suite + the periodic worker.
"""
from __future__ import annotations

import os
import sys
from datetime import UTC, datetime

from review_app.email.resend_client import send_via_resend
from review_app.email.templates import render_subject, render_template


def main() -> int:
    recipient = sys.argv[1] if len(sys.argv) > 1 else "benedictmt@gmail.com"

    if not os.environ.get("RESEND_API_KEY"):
        print("ERROR: RESEND_API_KEY is not set in the environment.", file=sys.stderr)
        return 2
    if not os.environ.get("EMAIL_FROM"):
        print("ERROR: EMAIL_FROM is not set in the environment.", file=sys.stderr)
        return 2

    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    payload = {
        "order_number": f"SMOKE-{int(datetime.now(UTC).timestamp())}",
        "customer_name": "Smoke Test",
        "line_items": [
            {
                "name": f"Smoke email — {timestamp}",
                "size": "test",
                "quantity": 1,
                "price_cents": 0,
            }
        ],
        "total_cents": 0,
        "currency": "USD",
    }

    subject = render_subject("email.order_confirmed", payload)
    html, text = render_template("email.order_confirmed", payload)

    print(f"Sending smoke email to {recipient!r} ...")
    print(f"  From:    {os.environ['EMAIL_FROM']}")
    print(f"  Subject: {subject}")

    msg_id = send_via_resend(
        to=recipient,
        subject=subject,
        html=html,
        text=text,
    )
    print(f"OK — Resend message id: {msg_id}")
    print(
        "Verify delivery by checking the recipient inbox. "
        "Check Resend dashboard for delivery logs if it doesn't arrive."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
