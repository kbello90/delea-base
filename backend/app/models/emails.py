"""Email settings schemas (Slice D, TRD §6.16)."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.models.members import Email

SenderName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=60, pattern=r'^[^<>"\r\n]+$'),
]


class OwnerAlerts(BaseModel):
    new_order: bool  # a website order was placed
    low_stock: bool  # a product fell below its low-stock threshold (once a day per product)
    balance_due: bool  # a daily digest of deposits whose balance is past due


CustomerTemplate = Literal[
    "order_received", "payment_received", "deposit_received", "in_production", "shipped"
]
# Shown above the email's own text. Blank counts as none.
Intro = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]


class CustomerEmail(BaseModel):
    enabled: bool
    intro: Intro | None = None


class CustomerEmails(BaseModel):
    """Emails to shoppers about their website orders (D2)."""

    order_received: CustomerEmail  # the order is placed: reference, items, how to pay, tracking
    payment_received: CustomerEmail  # paid in full
    deposit_received: CustomerEmail  # a part payment: amount and what is left
    in_production: CustomerEmail  # work started (once per order)
    shipped: CustomerEmail  # with the carrier and tracking number, if entered


class EmailLogEntry(BaseModel):
    created_at: datetime
    kind: str
    template: str
    recipient: str
    subject: str
    status: Literal["queued", "sending", "sent", "failed", "suppressed"]
    last_error: str | None


class EmailSettingsOut(BaseModel):
    provider: Literal["resend", "local", "off"]  # off: emails wait in the outbox
    from_address: str  # what recipients see as the sender
    sender_name: str | None  # None: the business name
    reply_to: str | None  # confirmed; replies go here
    reply_to_pending: str | None  # waiting for its confirmation link
    alerts: OwnerAlerts
    customer: CustomerEmails
    delayed: bool  # the oldest queued email has waited more than 15 minutes
    recent: list[EmailLogEntry]  # the latest 20


class EmailSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alerts: OwnerAlerts | None = None
    customer: CustomerEmails | None = None
    sender_name: SenderName | None = None


class EmailPreviewRequest(BaseModel):
    """A customer email rendered with a made-up order; `intro` previews unsaved text."""

    model_config = ConfigDict(extra="forbid")

    template: CustomerTemplate
    intro: Intro | None = None


class EmailPreviewOut(BaseModel):
    subject: str
    html: str  # shown in a sandboxed frame
    text: str


class TestEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template: CustomerTemplate | None = None  # None: the plain test email


class ReplyToRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Email


class ReplyToConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{20,100}$")]


class ReplyToConfirmed(BaseModel):
    shop: str


class TestEmailOut(BaseModel):
    sent_to: str  # always the signed-in owner's own sign-in email
    status: Literal["queued", "sending", "sent", "failed", "suppressed"]
