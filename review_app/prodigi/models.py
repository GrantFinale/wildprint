"""Pydantic v2 models for the Prodigi v4.0 API surface.

These models mirror the JSON shapes returned by `https://api.prodigi.com/v4.0/`
(and its sandbox counterpart). Coverage:

* `Address`, `Recipient` — order recipient + addresses
* `Asset`, `Item` — order items and their print-area assets
* `Cost`, `CostSummary`, `Charge` — money fields (Prodigi returns amounts as
  decimal strings like ``"55.00"``; we keep them as strings and parse to
  cents only when comparing/storing)
* `Shipment`, `Carrier`, `FulfillmentLocation`, `Tracking`, `ShipmentItem`
* `Status`, `StatusDetails`, `Issue`, `AuthorisationDetails`
* `Order`, `OrderRequest`, `OrderResponse` — full order object + envelopes
* `Quote`, `QuoteRequest`, `QuoteItem`, `QuoteResponse`
* `Product`, `ProductDetails`, `ProductDimensions`, `PrintArea`,
  `ProductVariant`
* `CallbackPayload` — CloudEvents v1.0 envelope used for webhook callbacks
* `Branding` — per-order branding URLs

Status / outcome enums are typed as ``Literal`` rather than ``Enum`` so the
JSON shape is forgiving — Prodigi has historically introduced new outcome
values without bumping the API version, and ``Literal`` lets us upgrade
type-narrowing without exploding at runtime. Anything not listed in the
literal narrows to ``str`` upstream (we use ``str`` typed as the wider
fallback in those spots — see ``StatusStage``).

All models are immutable-ish (``model_config = ConfigDict(extra='allow')``)
so that we tolerate forward-compatible schema additions: Prodigi adding a
new field to an order object never crashes our parser.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Type aliases / Literals
# ---------------------------------------------------------------------------
# Prodigi's documented stage values — keep narrow. New values fall through to
# the generic str field via Pydantic's `extra='allow'`, but the strongly typed
# Literal alias is what callers should switch on.
StatusStage = Literal["InProgress", "Complete", "Cancelled"]

# Per-detail-stage status values (downloadAssets, allocateProductionLocation,
# printReadyAssetsPrepared, inProduction, shipping).
StageDetailValue = Literal["NotStarted", "InProgress", "Complete", "Error"]

# Documented order issue error codes.
IssueErrorCode = Literal[
    "order.items.assets.NotDownloaded",
    "order.items.assets.FailedToDownloaded",
    "order.items.ItemUnavailable",
    "destinationCountryCode.UsSalesTaxWarning",
]

# General / endpoint-specific outcome values seen in responses.
GeneralOutcome = Literal[
    "Ok",
    "Created",
    "CreatedOk",
    "CreatedWithIssues",
    "Cancelled",
    "OnHold",
    "AlreadyExists",
    "InsufficientData",
    "Forbidden",
    "ProductDoesNotExist",
    "ProductDoesNotShip",
    "EndpointDoesNotExist",
    "ResourceNotFound",
]

# Prodigi shipping method values.
ShippingMethod = Literal["Budget", "Standard", "StandardPlus", "Express", "Overnight"]

# Item sizing options.
SizingMode = Literal["fillPrintArea", "fitPrintArea", "stretchToPrintArea"]


# ---------------------------------------------------------------------------
# Base config — every Prodigi response model permits unknown fields so a
# minor schema bump never crashes parsing.
# ---------------------------------------------------------------------------
class _ProdigiModel(BaseModel):
    """Shared base for all Prodigi API models."""

    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
        # Prodigi sends camelCase exclusively — we mirror it directly without
        # alias translation.
    )


# ---------------------------------------------------------------------------
# Money + addresses
# ---------------------------------------------------------------------------
class Cost(_ProdigiModel):
    """A money amount returned by Prodigi as ``{"amount": "55.00", "currency": "USD"}``.

    We keep ``amount`` as a string to preserve the exact decimal representation
    returned by the API. Use :meth:`amount_cents` when you need an integer.
    """

    amount: str
    currency: str

    def amount_cents(self) -> int:
        """Convert the decimal string amount to integer cents.

        Rounds half-away-from-zero. Raises ``ValueError`` if the amount can't
        be parsed.
        """
        from decimal import ROUND_HALF_UP, Decimal

        cents = (Decimal(self.amount) * 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        return int(cents)


class CostSummary(_ProdigiModel):
    """Aggregate cost breakdown returned by quotes and order responses."""

    items: Cost | None = None
    shipping: Cost | None = None
    tax: Cost | None = None
    fees: Cost | None = None
    total_cost: Cost | None = Field(default=None, alias="totalCost")
    total_tax: Cost | None = Field(default=None, alias="totalTax")


class Address(_ProdigiModel):
    """Recipient or billing address. Field names mirror Prodigi camelCase exactly."""

    line1: str
    line2: str | None = None
    postal_or_zip_code: str = Field(alias="postalOrZipCode")
    country_code: str = Field(alias="countryCode")
    town_or_city: str = Field(alias="townOrCity")
    state_or_county: str | None = Field(default=None, alias="stateOrCounty")


class Recipient(_ProdigiModel):
    """Order recipient = name + address (+ optional contact info)."""

    name: str
    email: str | None = None
    phone_number: str | None = Field(default=None, alias="phoneNumber")
    address: Address


# ---------------------------------------------------------------------------
# Items / assets / branding
# ---------------------------------------------------------------------------
class Asset(_ProdigiModel):
    """An image asset associated with an item's print area."""

    print_area: str = Field(alias="printArea")
    url: str | None = None
    thumbnail_url: str | None = Field(default=None, alias="thumbnailUrl")
    md5_hash: str | None = Field(default=None, alias="md5Hash")
    page_count: int | None = Field(default=None, alias="pageCount")
    status: str | None = None  # complete | inProgress | error  (response only)


class BrandingAsset(_ProdigiModel):
    """A single branding URL entry (postcard, packing slip, etc.)."""

    url: str | None = None


class Branding(_ProdigiModel):
    """Per-order branding component URLs.

    All fields optional — merchants typically configure a subset.
    """

    postcard: BrandingAsset | None = None
    flyer: BrandingAsset | None = None
    packing_slip_bw: BrandingAsset | None = Field(default=None, alias="packing_slip_bw")
    packing_slip_color: BrandingAsset | None = Field(
        default=None, alias="packing_slip_color"
    )
    sticker_exterior_round: BrandingAsset | None = Field(
        default=None, alias="sticker_exterior_round"
    )
    sticker_exterior_rectangle: BrandingAsset | None = Field(
        default=None, alias="sticker_exterior_rectangle"
    )
    sticker_interior_round: BrandingAsset | None = Field(
        default=None, alias="sticker_interior_round"
    )
    sticker_interior_rectangle: BrandingAsset | None = Field(
        default=None, alias="sticker_interior_rectangle"
    )


class Item(_ProdigiModel):
    """One line item on an order or quote."""

    id: str | None = None  # set by Prodigi on response only
    merchant_reference: str | None = Field(default=None, alias="merchantReference")
    sku: str
    copies: int
    sizing: SizingMode | str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    assets: list[Asset] = Field(default_factory=list)
    recipient_cost: Cost | None = Field(default=None, alias="recipientCost")
    unit_cost: Cost | None = Field(default=None, alias="unitCost")
    merge_status: dict[str, Any] | None = Field(default=None, alias="mergeStatus")


# ---------------------------------------------------------------------------
# Status, issues
# ---------------------------------------------------------------------------
class StatusDetails(_ProdigiModel):
    """Per-stage details map returned in an order's status."""

    download_assets: StageDetailValue | str | None = Field(
        default=None, alias="downloadAssets"
    )
    allocate_production_location: StageDetailValue | str | None = Field(
        default=None, alias="allocateProductionLocation"
    )
    print_ready_assets_prepared: StageDetailValue | str | None = Field(
        default=None, alias="printReadyAssetsPrepared"
    )
    in_production: StageDetailValue | str | None = Field(
        default=None, alias="inProduction"
    )
    shipping: StageDetailValue | str | None = None


class AuthorisationDetails(_ProdigiModel):
    """Authorisation details for issues that require payment auth."""

    authorisation_url: str | None = Field(default=None, alias="authorisationUrl")
    payment_details: dict[str, Any] | None = Field(default=None, alias="paymentDetails")


class Issue(_ProdigiModel):
    """A single issue blocking or affecting an order."""

    object_id: str | None = Field(default=None, alias="objectId")
    error_code: IssueErrorCode | str = Field(alias="errorCode")
    description: str | None = None
    authorisation_details: AuthorisationDetails | None = Field(
        default=None, alias="authorisationDetails"
    )


class Status(_ProdigiModel):
    """Overall order status object."""

    stage: StatusStage | str
    details: StatusDetails | None = None
    issues: list[Issue] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Shipments / carriers / charges
# ---------------------------------------------------------------------------
class Carrier(_ProdigiModel):
    """Carrier + service for a shipment."""

    name: str | None = None
    service: str | None = None


class FulfillmentLocation(_ProdigiModel):
    """Lab + country fulfilling a shipment."""

    country_code: str | None = Field(default=None, alias="countryCode")
    lab_code: str | None = Field(default=None, alias="labCode")


class Tracking(_ProdigiModel):
    """Tracking number + URL for a shipment."""

    number: str | None = None
    url: str | None = None


class ShipmentItem(_ProdigiModel):
    """Item association for a shipment."""

    item_id: str = Field(alias="itemId")


class Shipment(_ProdigiModel):
    """A single Prodigi shipment (one order may split into multiple)."""

    id: str
    status: str | None = None  # Processing | Cancelled | Shipped
    carrier: Carrier | None = None
    dispatch_date: datetime | None = Field(default=None, alias="dispatchDate")
    tracking: Tracking | None = None
    items: list[ShipmentItem] = Field(default_factory=list)
    fulfillment_location: FulfillmentLocation | None = Field(
        default=None, alias="fulfillmentLocation"
    )


class Charge(_ProdigiModel):
    """An order-level charge entry."""

    id: str | None = None
    prodigi_invoice_number: str | None = Field(
        default=None, alias="prodigiInvoiceNumber"
    )
    total_cost: Cost | None = Field(default=None, alias="totalCost")
    total_tax: Cost | None = Field(default=None, alias="totalTax")
    items: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Order objects + envelopes
# ---------------------------------------------------------------------------
class Order(_ProdigiModel):
    """The full Prodigi Order object as returned by GET / POST /Orders."""

    id: str
    created: datetime | None = None
    last_updated: datetime | None = Field(default=None, alias="lastUpdated")
    callback_url: str | None = Field(default=None, alias="callbackUrl")
    merchant_reference: str | None = Field(default=None, alias="merchantReference")
    shipping_method: ShippingMethod | str = Field(alias="shippingMethod")
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")
    status: Status
    charges: list[Charge] = Field(default_factory=list)
    shipments: list[Shipment] = Field(default_factory=list)
    recipient: Recipient
    items: list[Item] = Field(default_factory=list)
    branding: Branding | None = None
    metadata: dict[str, Any] | None = None


class OrderRequest(_ProdigiModel):
    """The body shape of POST /v4.0/Orders.

    Note: the idempotency key is sent via the ``Idempotency-Key`` HTTP header,
    NOT in the request body. Keep the body in sync with what Prodigi accepts.
    """

    shipping_method: ShippingMethod | str = Field(alias="shippingMethod")
    recipient: Recipient
    items: list[Item]
    merchant_reference: str | None = Field(default=None, alias="merchantReference")
    callback_url: str | None = Field(default=None, alias="callbackUrl")
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")
    branding: Branding | None = None
    metadata: dict[str, Any] | None = None


class OrderResponse(_ProdigiModel):
    """Envelope returned by /Orders endpoints."""

    outcome: GeneralOutcome | str
    order: Order | None = None
    trace_parent: str | None = Field(default=None, alias="traceParent")


# ---------------------------------------------------------------------------
# Order action envelopes (cancel / update shipping / update recipient)
# ---------------------------------------------------------------------------
class OrderActionResponse(_ProdigiModel):
    """Generic envelope for order action endpoints (cancel, update*)."""

    outcome: GeneralOutcome | str
    order: Order | None = None
    trace_parent: str | None = Field(default=None, alias="traceParent")


# ---------------------------------------------------------------------------
# Quote objects
# ---------------------------------------------------------------------------
class QuoteItem(_ProdigiModel):
    """An item in a quote response (mirrors Item but adds id + unitCost)."""

    id: str | None = None
    sku: str
    copies: int
    unit_cost: Cost | None = Field(default=None, alias="unitCost")
    attributes: dict[str, Any] = Field(default_factory=dict)
    assets: list[Asset] = Field(default_factory=list)


class QuoteShipment(_ProdigiModel):
    """A shipment grouping inside a quote."""

    carrier: Carrier | None = None
    fulfillment_location: FulfillmentLocation | None = Field(
        default=None, alias="fulfillmentLocation"
    )
    cost: Cost | None = None
    items: list[str] = Field(default_factory=list)


class Quote(_ProdigiModel):
    """A single quote variant inside a quote response."""

    shipment_method: ShippingMethod | str | None = Field(
        default=None, alias="shipmentMethod"
    )
    cost_summary: CostSummary | None = Field(default=None, alias="costSummary")
    shipments: list[QuoteShipment] = Field(default_factory=list)
    items: list[QuoteItem] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)


class QuoteRequest(_ProdigiModel):
    """Body shape of POST /v4.0/Quotes."""

    shipping_method: ShippingMethod | str | None = Field(
        default=None, alias="shippingMethod"
    )
    destination_country_code: str = Field(alias="destinationCountryCode")
    currency_code: str | None = Field(default=None, alias="currencyCode")
    items: list[Item]


class QuoteResponse(_ProdigiModel):
    """Envelope returned by /Quotes."""

    outcome: GeneralOutcome | str
    quotes: list[Quote] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    trace_parent: str | None = Field(default=None, alias="traceParent")


# ---------------------------------------------------------------------------
# Product details
# ---------------------------------------------------------------------------
class ProductDimensions(_ProdigiModel):
    """Outer product dimensions returned by the product details endpoint."""

    width: float
    height: float
    units: str  # 'in' | 'cm'


class PrintAreaSpec(_ProdigiModel):
    """Print area metadata (e.g. is it required, sample resolution)."""

    required: bool | None = None


class PrintAreaSize(_ProdigiModel):
    """Pixel-resolution requirement for a print area variant."""

    horizontal_resolution: int | None = Field(
        default=None, alias="horizontalResolution"
    )
    vertical_resolution: int | None = Field(default=None, alias="verticalResolution")


class ProductVariant(_ProdigiModel):
    """One attribute combination of a product (e.g. ``{"color": "black"}``)."""

    attributes: dict[str, Any] = Field(default_factory=dict)
    ships_to: list[str] = Field(default_factory=list, alias="shipsTo")
    print_area_sizes: dict[str, PrintAreaSize] = Field(
        default_factory=dict, alias="printAreaSizes"
    )


class Product(_ProdigiModel):
    """The product details payload."""

    sku: str
    description: str | None = None
    product_dimensions: ProductDimensions | None = Field(
        default=None, alias="productDimensions"
    )
    attributes: dict[str, list[str]] = Field(default_factory=dict)
    print_areas: dict[str, PrintAreaSpec] = Field(
        default_factory=dict, alias="printAreas"
    )
    variants: list[ProductVariant] = Field(default_factory=list)
    frame_sku: str | None = Field(default=None, alias="frameSku")
    ships_to: list[str] = Field(default_factory=list, alias="shipsTo")


class ProductDetails(_ProdigiModel):
    """Envelope returned by /v4.0/products/{sku}."""

    outcome: GeneralOutcome | str
    product: Product | None = None
    trace_parent: str | None = Field(default=None, alias="traceParent")


# ---------------------------------------------------------------------------
# Webhook callbacks (CloudEvents v1.0)
# ---------------------------------------------------------------------------
class CallbackPayload(_ProdigiModel):
    """CloudEvents v1.0 envelope used for Prodigi webhook callbacks.

    Notable fields:

    * ``id`` — the unique event id (we use this for dedupe). Begins with
      ``evt_`` per Prodigi's ID conventions.
    * ``type`` — the event type, e.g. ``com.prodigi.order.status.stage.changed#InProgress``.
    * ``data`` — the order snapshot at the time of the event. We DO NOT trust
      this for state mutation; instead we re-fetch via GET /Orders.
    """

    spec_version: str | None = Field(default=None, alias="specversion")
    id: str
    type: str
    source: str | None = None
    subject: str | None = None
    time: datetime | None = None
    data_content_type: str | None = Field(default=None, alias="datacontenttype")
    data_schema: str | None = Field(default=None, alias="dataschema")
    data: dict[str, Any] | None = None

    def prodigi_order_id(self) -> str | None:
        """Best-effort extraction of the prodigi order id from the payload.

        Prodigi sends the order under ``data.order.id`` for order-level events
        and ``data.shipment`` shapes for shipment events. We try the common
        paths.
        """
        if not self.data:
            return None
        order = self.data.get("order")
        if isinstance(order, dict):
            oid = order.get("id")
            if isinstance(oid, str):
                return oid
        # Shipment events: subject is the order id.
        if isinstance(self.subject, str) and self.subject.startswith("ord_"):
            return self.subject
        return None


__all__ = [
    "Address",
    "Asset",
    "AuthorisationDetails",
    "Branding",
    "BrandingAsset",
    "CallbackPayload",
    "Carrier",
    "Charge",
    "Cost",
    "CostSummary",
    "FulfillmentLocation",
    "GeneralOutcome",
    "Issue",
    "IssueErrorCode",
    "Item",
    "Order",
    "OrderActionResponse",
    "OrderRequest",
    "OrderResponse",
    "PrintAreaSize",
    "PrintAreaSpec",
    "Product",
    "ProductDetails",
    "ProductDimensions",
    "ProductVariant",
    "Quote",
    "QuoteItem",
    "QuoteRequest",
    "QuoteResponse",
    "QuoteShipment",
    "Recipient",
    "Shipment",
    "ShipmentItem",
    "ShippingMethod",
    "SizingMode",
    "StageDetailValue",
    "Status",
    "StatusDetails",
    "StatusStage",
    "Tracking",
]
