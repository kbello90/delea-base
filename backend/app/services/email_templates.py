"""Email content (TRD §6.16): plain, single column, mobile-friendly, in the shop's colour, with
a plain-text version of each. Every value from the shop or a shopper is escaped: none is ever
read as HTML. Owner alerts carry no street address or phone number either (D1 sends owner
alerts only; customer emails arrive in D2 under the same rules).
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
    "bank_transfer": "bank transfer",
    "pay_on_pickup": "paying on pickup",
    "card": "card",
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


def _layout(
    shop: str,
    color: str | None,
    heading: str,
    lines: list[str],
    button: tuple[str, str] | None,
    footer: str,
) -> str:
    color = color if color and _HEX.match(color) else DEFAULT_COLOR
    paragraphs = "".join(
        f'<p style="margin:0 0 14px;font-size:16px;line-height:1.5;color:#1f1a17">{escape(line)}</p>'
        for line in lines
    )
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
        f'font-size:12px;line-height:1.5;color:#7a6f66">{escape(footer)}</td></tr>'
        "</table></td></tr></table></body></html>"
    )


def _text(
    shop: str, heading: str, lines: list[str], button: tuple[str, str] | None, footer: str
) -> str:
    parts = [shop, "", heading, "", *lines]
    if button:
        parts += ["", f"{button[0]}: {button[1]}"]
    return "\n".join([*parts, "", "--", footer]) + "\n"


def _render(
    subject: str,
    shop: str,
    color: str | None,
    heading: str,
    lines: list[str],
    button: tuple[str, str] | None,
    footer: str,
) -> Rendered:
    return Rendered(
        subject=subject,
        html=_layout(shop, color, heading, lines, button, footer),
        text=_text(shop, heading, lines, button, footer),
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
