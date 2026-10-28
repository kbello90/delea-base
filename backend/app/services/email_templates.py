"""Email content (TRD §6.16): plain, single column, mobile-friendly, in the shop's colour, with
a plain-text version of each. Every value from the shop or a shopper is escaped: none is ever
read as HTML. No email carries a street address or a phone number: owner alerts (D1) and
customer emails (D2) show the order reference, the items, the amounts, how to pay and the
tracking link. Customer emails are transactional and promote nothing.
"""

import re
from dataclasses import dataclass
from datetime import date
from html import escape

DEFAULT_COLOR = "#6B2737"
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_SYMBOLS = {
    "USD": "$",
    "CAD": "CA$",
    "AUD": "A$",
    "NZD": "NZ$",
    "EUR": "€",
    "GBP": "£",
    "MXN": "MX$",
    "CHF": "CHF ",
}

PAYMENT_CHOICES = {
    "bank_transfer": "paying by bank transfer",
    "pay_on_pickup": "paying on pickup",
    "card": "paying by card",
}


@dataclass(frozen=True)
class Rendered:
    subject: str
    html: str
    text: str


def money(cents: int, currency: str) -> str:
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(int(cents)), 100)
    return f"{sign}{_SYMBOLS.get(currency, currency + ' ')}{whole:,}.{fraction:02d}"


Rows = list[tuple[str, str]]


def _footer_lines(footer: str | list[str]) -> list[str]:
    return [footer] if isinstance(footer, str) else footer


def _paragraphs(lines: list[str]) -> str:
    return "".join(
        f'<p style="margin:0 0 14px;font-size:16px;line-height:1.5;color:#1f1a17">{escape(line)}</p>'
        for line in lines
    )


def _table(rows: Rows | None) -> str:
    if not rows:
        return ""
    cells = "".join(
        '<tr><td style="padding:6px 0;border-top:1px solid #ece6de;font-size:15px;color:#1f1a17">'
        f"{escape(label)}</td>"
        '<td align="right" style="padding:6px 0 6px 12px;border-top:1px solid #ece6de;'
        f'font-size:15px;color:#1f1a17;white-space:nowrap">{escape(value)}</td></tr>'
        for label, value in rows
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin:0 0 16px;border-bottom:1px solid #ece6de">{cells}</table>'
    )


def _layout(
    shop: str,
    color: str | None,
    heading: str,
    lines: list[str],
    button: tuple[str, str] | None,
    footer: str | list[str],
    rows: Rows | None = None,
    after: list[str] | None = None,
) -> str:
    color = color if color and _HEX.match(color) else DEFAULT_COLOR
    paragraphs = _paragraphs(lines) + _table(rows) + _paragraphs(after or [])
    action = ""
    if button:
        label, url = button
        action = (
            f'<p style="margin:22px 0"><a href="{escape(url, quote=True)}" '
            f'style="display:inline-block;padding:12px 20px;background:{color};color:#ffffff;'
            f'text-decoration:none;border-radius:4px;font-weight:600">{escape(label)}</a></p>'
        )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(heading)}</title></head>"
        '<body style="margin:0;padding:0;background:#f7f4ef">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f7f4ef">'
        '<tr><td align="center" style="padding:24px 12px">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="max-width:560px;background:#ffffff;border-radius:6px;overflow:hidden">'
        f'<tr><td style="height:6px;background:{color}"></td></tr>'
        '<tr><td style="padding:24px 24px 8px;font-family:Arial,Helvetica,sans-serif">'
        f'<p style="margin:0 0 6px;font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:#7a6f66">{escape(shop)}</p>'
        f'<h1 style="margin:0 0 16px;font-size:22px;line-height:1.3;color:#1f1a17">{escape(heading)}</h1>'
        f"{paragraphs}{action}"
        "</td></tr>"
        '<tr><td style="padding:16px 24px 24px;font-family:Arial,Helvetica,sans-serif;'
        'font-size:12px;line-height:1.5;color:#7a6f66">'
        f"{'<br>'.join(escape(line) for line in _footer_lines(footer))}</td></tr>"
        "</table></td></tr></table></body></html>"
    )


def _text(
    shop: str,
    heading: str,
    lines: list[str],
    button: tuple[str, str] | None,
    footer: str | list[str],
    rows: Rows | None = None,
    after: list[str] | None = None,
) -> str:
    parts = [shop, "", heading, "", *lines]
    if rows:
        parts += ["", *(f"{label}: {value}" for label, value in rows), ""]
    parts += after or []
    if button:
        parts += ["", f"{button[0]}: {button[1]}"]
    return "\n".join([*parts, "", "--", *_footer_lines(footer)]) + "\n"


def _render(
    subject: str,
    shop: str,
    color: str | None,
    heading: str,
    lines: list[str],
    button: tuple[str, str] | None,
    footer: str | list[str],
    rows: Rows | None = None,
    after: list[str] | None = None,
) -> Rendered:
    return Rendered(
        subject=subject,
        html=_layout(shop, color, heading, lines, button, footer, rows, after),
        text=_text(shop, heading, lines, button, footer, rows, after),
    )


ALERT_FOOTER = "An alert from DELEA for {shop}. Turn alerts on or off in Settings › Emails."


def new_order_alert(shop: str, color: str | None, invoice: dict, admin_url: str) -> Rendered:
    ref = invoice.get("order_ref") or invoice.get("number") or "a new order"
    items = int(invoice.get("items") or 0)
    lines = [
        f"{invoice.get('customer_name') or 'A shopper'} placed order {ref} on your website.",
        f"{items} {'item' if items == 1 else 'items'}, total "
        f"{money(int(invoice['total_cents']), invoice['currency'])}"
        + (
            f", {PAYMENT_CHOICES[invoice['payment_choice']]}"
            if invoice.get("payment_choice") in PAYMENT_CHOICES
            else ""
        )
        + ".",
    ]
    return _render(
        f"New website order {ref}",
        shop,
        color,
        f"New website order {ref}",
        lines,
        ("Open the order", admin_url),
        ALERT_FOOTER.format(shop=shop),
    )


def low_stock_alert(shop: str, color: str | None, product: dict, admin_url: str) -> Rendered:
    name = product.get("name") or "A product"
    lines = [
        f"{name} ({product.get('sku')}) is down to {product.get('stock_qty')} "
        f"(your alert is below {product.get('threshold')}).",
    ]
    return _render(
        f"Low stock: {name}",
        shop,
        color,
        f"Low stock: {name}",
        lines,
        ("See low stock", admin_url),
        ALERT_FOOTER.format(shop=shop),
    )


def balance_due_digest(
    shop: str, color: str | None, currency: str, invoices: list[dict], today: date, admin_url: str
) -> Rendered:
    count = len(invoices)
    lines = [
        f"{count} {'order holds' if count == 1 else 'orders hold'} a deposit with the rest past due:"
    ]
    for inv in invoices[:20]:
        lines.append(
            f"{inv.get('number')} · {inv.get('customer_name')} · "
            f"{money(int(inv['balance_cents']), currency)} due since {inv.get('due_date')}"
        )
    if count > 20:
        lines.append(f"…and {count - 20} more.")
    return _render(
        f"Balances due ({today.isoformat()})",
        shop,
        color,
        "Balances due",
        lines,
        ("Open invoices", admin_url),
        ALERT_FOOTER.format(shop=shop),
    )


def test_email(shop: str, color: str | None) -> Rendered:
    lines = [
        "This is a test from DELEA. Emails to you and your customers will look like this, "
        "in your shop's colour.",
        "If you can read this, email reaches you.",
    ]
    return _render(
        f"Test email from {shop}",
        shop,
        color,
        "Your test email",
        lines,
        None,
        f"Sent by DELEA because you asked for a test in {shop}'s settings.",
    )


def reply_to_confirmation(shop: str, color: str | None, link: str) -> Rendered:
    lines = [
        f"{shop} wants customers' replies to its emails to come to this address.",
        "If that's you, confirm it. The link works once, for 24 hours. If you don't know "
        "this shop, ignore this email: nothing changes.",
    ]
    return _render(
        f"Confirm replies for {shop}",
        shop,
        color,
        "Confirm this reply address",
        lines,
        ("Confirm this address", link),
        f"Sent by DELEA on behalf of {shop}.",
    )


# ---------------------------------------------------------------------------
# Customer emails (D2): website orders only
# ---------------------------------------------------------------------------
CUSTOMER_TEMPLATES = (
    "order_received",
    "payment_received",
    "deposit_received",
    "in_production",
    "shipped",
)
CARD_MINIMUM_CENTS = 50  # Stripe's minimum charge; the tracking page says the same


@dataclass(frozen=True)
class Order:
    """What a customer email may say about an order (from email_customer_context). There is
    deliberately no field for the street address, the phone number or the shopper's note."""

    shop: str
    color: str | None
    ref: str
    first_name: str
    currency: str
    lines: list[dict]
    subtotal_cents: int
    shipping_cents: int
    tax_cents: int
    total_cents: int
    paid_cents: int
    balance_cents: int
    fulfillment: str | None
    payment_choice: str | None
    payment_instructions: str | None
    pickup_note: str | None
    carrier: str | None
    tracking_number: str | None
    tracking_url: str
    website_url: str | None
    contact_email: str | None
    intro: str | None = None


def order_from_context(
    shop: str,
    color: str | None,
    ctx: dict,
    tracking_url: str,
    website_url: str | None,
    intro: str | None,
) -> Order:
    return Order(
        shop=shop,
        color=color,
        ref=ctx.get("order_ref") or "your order",
        first_name=ctx.get("first_name") or "there",
        currency=ctx.get("currency") or "USD",
        lines=list(ctx.get("lines") or []),
        subtotal_cents=int(ctx.get("subtotal_cents") or 0),
        shipping_cents=int(ctx.get("shipping_cents") or 0),
        tax_cents=int(ctx.get("tax_cents") or 0),
        total_cents=int(ctx.get("total_cents") or 0),
        paid_cents=int(ctx.get("amount_paid_cents") or 0),
        balance_cents=int(ctx.get("balance_cents") or 0),
        fulfillment=ctx.get("fulfillment"),
        payment_choice=ctx.get("payment_choice"),
        payment_instructions=ctx.get("payment_instructions"),
        pickup_note=ctx.get("pickup_note"),
        carrier=ctx.get("carrier"),
        tracking_number=ctx.get("tracking_number"),
        tracking_url=tracking_url,
        website_url=website_url,
        contact_email=ctx.get("contact_email"),
        intro=intro,
    )


def _customer_footer(order: Order) -> list[str]:
    lines = [f"You're receiving this because you placed an order with {order.shop}."]
    if order.contact_email:
        lines.append(f"Questions? Write to {order.contact_email}.")
    if order.website_url:
        lines.append(order.website_url)
    return lines


def _opening(order: Order, line: str) -> list[str]:
    return ([order.intro] if order.intro else []) + [line]


def _money(order: Order, cents: int) -> str:
    return money(cents, order.currency)


def _item_rows(order: Order) -> Rows:
    rows: Rows = []
    for line in order.lines:
        answers = ", ".join(
            f"{a.get('label')}: {a.get('value')}"
            for a in line.get("personalization") or []
            if isinstance(a, dict) and a.get("value")
        )
        label = f"{line.get('description')} × {line.get('qty')}"
        if answers:
            label += f" ({answers})"
        rows.append((label, _money(order, int(line.get("line_total_cents") or 0))))
    return rows


def _total_rows(order: Order) -> Rows:
    rows: Rows = [("Subtotal", _money(order, order.subtotal_cents))]
    if order.fulfillment == "pickup":
        rows.append(("Pickup", _money(order, 0)))
    elif order.fulfillment == "ship" or order.shipping_cents:
        rows.append(("Shipping", _money(order, order.shipping_cents)))
    if order.tax_cents:
        rows.append(("Tax", _money(order, order.tax_cents)))
    rows.append(("Total", _money(order, order.total_cents)))
    if 0 < order.paid_cents < order.total_cents:
        rows += [
            ("Paid", _money(order, order.paid_cents)),
            ("Left to pay", _money(order, order.balance_cents)),
        ]
    return rows


def _how_to_pay(order: Order) -> tuple[list[str], tuple[str, str]]:
    """What to do about the balance, and the email's button. Pay now opens the tracking page,
    whose own Pay now button starts the payment: a link in an email never charges anything."""
    track = ("Track your order", order.tracking_url)
    if order.balance_cents <= 0:
        return ["It's paid. Thank you."], track
    due = _money(order, order.balance_cents)
    if order.payment_choice == "card":
        if order.balance_cents < CARD_MINIMUM_CENTS:
            return [f"{due} is left to pay. Pay the rest with the shop."], track
        return [f"{due} is left to pay. You can pay it by card on your order page."], (
            "Pay now",
            order.tracking_url,
        )
    if order.payment_choice == "pay_on_pickup":
        lines = [f"You pay {due} when you pick up your order."]
    else:
        lines = [f"How to pay {due}: by bank transfer."]
    if order.payment_instructions:
        lines += [part for part in order.payment_instructions.splitlines() if part.strip()]
    return lines, track


def order_received(order: Order) -> Rendered:
    pay, button = _how_to_pay(order)
    if order.fulfillment == "pickup" and order.pickup_note:
        pay.append(f"Pickup: {order.pickup_note}")
    return _render(
        f"Order {order.ref} received",
        order.shop,
        order.color,
        f"Thank you, {order.first_name}",
        _opening(order, f"We received your order {order.ref}. Here is what you ordered:"),
        button,
        _customer_footer(order),
        rows=_item_rows(order) + _total_rows(order),
        after=pay,
    )


def payment_received(order: Order) -> Rendered:
    return _render(
        f"Payment received for {order.ref}",
        order.shop,
        order.color,
        "Payment received",
        _opening(
            order,
            f"Your order {order.ref} is paid in full: {_money(order, order.total_cents)}. "
            "Thank you.",
        ),
        ("Track your order", order.tracking_url),
        _customer_footer(order),
    )


def deposit_received(order: Order, amount_cents: int) -> Rendered:
    pay, button = _how_to_pay(order)
    return _render(
        f"Deposit received for {order.ref}",
        order.shop,
        order.color,
        "Deposit received",
        _opening(
            order,
            f"We received your deposit of {_money(order, amount_cents)} for order {order.ref}.",
        ),
        button,
        _customer_footer(order),
        rows=[
            ("Order total", _money(order, order.total_cents)),
            ("Paid so far", _money(order, order.paid_cents)),
            ("Left to pay", _money(order, order.balance_cents)),
        ],
        after=pay,
    )


def in_production(order: Order) -> Rendered:
    return _render(
        f"Order {order.ref} is being made",
        order.shop,
        order.color,
        "Your order is being made",
        _opening(order, f"Good news: work on your order {order.ref} has started."),
        ("Track your order", order.tracking_url),
        _customer_footer(order),
    )


def shipped(order: Order) -> Rendered:
    track = ("Track your order", order.tracking_url)
    if order.fulfillment == "pickup":
        return _render(
            f"Order {order.ref} is ready for pickup",
            order.shop,
            order.color,
            "Ready for pickup",
            _opening(order, f"Your order {order.ref} is ready for pickup."),
            track,
            _customer_footer(order),
            after=[f"Pickup: {order.pickup_note}"] if order.pickup_note else None,
        )
    rows: Rows = []
    if order.carrier:
        rows.append(("Carrier", order.carrier))
    if order.tracking_number:
        rows.append(("Tracking number", order.tracking_number))
    return _render(
        f"Order {order.ref} has shipped",
        order.shop,
        order.color,
        "Your order is on its way",
        _opening(order, f"Your order {order.ref} has shipped."),
        track,
        _customer_footer(order),
        rows=rows or None,
    )


def customer_email(template: str, order: Order, amount_cents: int | None = None) -> Rendered:
    if template == "order_received":
        return order_received(order)
    if template == "payment_received":
        return payment_received(order)
    if template == "deposit_received":
        return deposit_received(order, order.paid_cents if amount_cents is None else amount_cents)
    if template == "in_production":
        return in_production(order)
    if template == "shipped":
        return shipped(order)
    raise ValueError(f"unknown customer email {template!r}")


def sample_order(
    shop: str,
    color: str | None,
    website_url: str | None,
    contact_email: str | None,
    intro: str | None,
) -> Order:
    """The preview's made-up order: a ring with a deposit paid by card, shipped by USPS. No
    real shopper's data."""
    base = (website_url or "https://shop.example").rstrip("/")
    return Order(
        shop=shop,
        color=color,
        ref="W-SAMPLE01",
        first_name="Sarah",
        currency="USD",
        lines=[
            {
                "description": "Halo Ring",
                "qty": 1,
                "line_total_cents": 14500,
                "personalization": [{"label": "Ring size", "value": "7"}],
            }
        ],
        subtotal_cents=14500,
        shipping_cents=600,
        tax_cents=1160,
        total_cents=16260,
        paid_cents=5000,
        balance_cents=11260,
        fulfillment="ship",
        payment_choice="card",
        payment_instructions=None,
        pickup_note=None,
        carrier="USPS",
        tracking_number="9400 1000 0000 0000 0000 00",
        tracking_url=f"{base}/orders/sample",
        website_url=website_url,
        contact_email=contact_email,
        intro=intro,
    )
