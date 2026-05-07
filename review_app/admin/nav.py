"""Sidebar navigation tree + role visibility table.

Single source of truth for the admin sidebar — both the Jinja partial
(``templates/admin/_sidebar.html``) and the unit tests import from here so
that role gating is asserted in one place. Keeping the tree as Python data
(not Jinja literals) means tests can verify "viewers don't see Settings"
without parsing HTML.

The structure mirrors ``docs/admin-ia.md`` §1 exactly (8 categories, max
depth 2). Each :class:`NavItem` has the role list that's allowed to *see*
the link; the route handler still enforces the gate via ``@requires_role``.
The sidebar hides items for roles that lack access (per IA decision: hidden,
not greyed out).
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Role identifiers — must mirror :data:`review_app.auth.models.VALID_ROLES`.
ROLE_ADMIN: str = "admin"
ROLE_STAFF: str = "staff"
ROLE_VIEWER: str = "viewer"

ALL_ROLES: tuple[str, ...] = (ROLE_ADMIN, ROLE_STAFF, ROLE_VIEWER)
ADMIN_STAFF: tuple[str, ...] = (ROLE_ADMIN, ROLE_STAFF)
ADMIN_ONLY: tuple[str, ...] = (ROLE_ADMIN,)


@dataclass(frozen=True)
class NavItem:
    """Leaf nav entry — a sub-page link inside a category."""

    label: str
    endpoint: str  # Flask endpoint string (e.g., "admin.catalog_species")
    roles: tuple[str, ...] = ALL_ROLES


@dataclass(frozen=True)
class NavCategory:
    """Top-level sidebar category — has a label, an icon, and 1+ children."""

    label: str
    icon: str  # short label rendered as text glyph (no SVG dep yet)
    children: tuple[NavItem, ...] = field(default_factory=tuple)
    # The category is visible iff at least one child is visible to the role.

    def visible_for(self, role: str | None) -> bool:
        """True if any child is accessible to ``role``."""
        if role is None:
            return False
        return any(role in child.roles for child in self.children)

    def visible_children(self, role: str | None) -> tuple[NavItem, ...]:
        """Subset of :attr:`children` accessible to ``role``."""
        if role is None:
            return ()
        return tuple(c for c in self.children if role in c.roles)

    def first_endpoint(self, role: str | None) -> str | None:
        """First child endpoint visible to ``role`` — used for category links."""
        kids = self.visible_children(role)
        return kids[0].endpoint if kids else None


# ---------------------------------------------------------------------------
# The tree.
# Order matches docs/admin-ia.md §1 exactly. Adding a page = adding a NavItem
# here + the route handler + the template. See docs/admin-shell.md.
# ---------------------------------------------------------------------------
NAV_TREE: tuple[NavCategory, ...] = (
    NavCategory(
        label="Dashboard",
        icon="DB",
        children=(
            NavItem("Overview", "admin.dashboard", ALL_ROLES),
        ),
    ),
    NavCategory(
        label="Orders",
        icon="OR",
        children=(
            # Owned by the parallel agent. Endpoints stubbed to "admin.orders_*"
            # so the sidebar links resolve once parallel work merges.
            NavItem("All orders", "admin.orders_list", ALL_ROLES),
            NavItem("Refunds queue", "admin.orders_refunds", ADMIN_ONLY),
            NavItem("Test orders", "admin.orders_test", ADMIN_STAFF),
        ),
    ),
    NavCategory(
        label="Customers",
        icon="CU",
        children=(
            NavItem("All customers", "admin.customers_list", ALL_ROLES),
        ),
    ),
    NavCategory(
        label="Catalog",
        icon="CA",
        children=(
            NavItem("Species", "admin.catalog_species", ALL_ROLES),
            NavItem("Backgrounds", "admin.catalog_backgrounds", ADMIN_STAFF),
            NavItem("Sizing", "admin.catalog_sizing", ADMIN_ONLY),
            NavItem("Frame SKUs", "admin.catalog_frame_skus", ADMIN_ONLY),
            NavItem("Lakes", "admin.catalog_lakes", ADMIN_STAFF),
            NavItem("Render presets", "admin.catalog_render_presets", ADMIN_ONLY),
        ),
    ),
    NavCategory(
        label="Fulfillment",
        icon="FU",
        children=(
            NavItem("Connection", "admin.fulfillment_connection", ADMIN_ONLY),
            NavItem("Webhook log", "admin.fulfillment_webhooks", ALL_ROLES),
            NavItem("Error queue", "admin.fulfillment_errors", ADMIN_STAFF),
            NavItem("Reprints", "admin.fulfillment_reprints", ADMIN_ONLY),
        ),
    ),
    NavCategory(
        label="Content",
        icon="CO",
        children=(
            NavItem("Email templates", "admin.content_email_templates", ADMIN_ONLY),
            NavItem("Email send log", "admin.content_email_log", ALL_ROLES),
            NavItem("Marketing pages", "admin.content_marketing", ADMIN_ONLY),
        ),
    ),
    NavCategory(
        label="Analytics",
        icon="AN",
        children=(
            NavItem("Sales", "admin.analytics_sales", ALL_ROLES),
            NavItem("AI usage", "admin.analytics_ai_usage", ALL_ROLES),
            NavItem("Operations", "admin.analytics_operations", ALL_ROLES),
        ),
    ),
    NavCategory(
        label="Settings",
        icon="SE",
        children=(
            NavItem("Users & roles", "admin.settings_users", ADMIN_ONLY),
            NavItem("API keys", "admin.settings_api_keys", ADMIN_ONLY),
            NavItem("Integrations", "admin.settings_integrations", ADMIN_STAFF),
            NavItem("Audit log", "admin.settings_audit", ADMIN_ONLY),
            NavItem("My account", "admin.settings_account", ALL_ROLES),
        ),
    ),
)


def visible_categories(role: str | None) -> list[NavCategory]:
    """Return the subset of :data:`NAV_TREE` that ``role`` can see."""
    return [cat for cat in NAV_TREE if cat.visible_for(role)]


__all__ = [
    "ADMIN_ONLY",
    "ADMIN_STAFF",
    "ALL_ROLES",
    "NAV_TREE",
    "ROLE_ADMIN",
    "ROLE_STAFF",
    "ROLE_VIEWER",
    "NavCategory",
    "NavItem",
    "visible_categories",
]
