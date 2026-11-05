"""Standard Webhooks signatures (standardwebhooks.com), as Supabase Auth signs its HTTP hooks.

The sender signs "{webhook-id}.{webhook-timestamp}.{raw body}" with HMAC-SHA256 under the
base64-decoded secret and sends "v1,<base64 signature>" in webhook-signature (several,
space-separated, while a secret is being rotated). Supabase shows the secret as
"v1,whsec_<base64>"; "whsec_<base64>" is accepted too.

verify() raises WebhookRefused on anything wrong, with a reason that never contains the secret,
a signature or the body.
"""

import base64
import binascii
import hashlib
import hmac
import time
from collections.abc import Callable, Mapping

TOLERANCE_SECONDS = 5 * 60


class WebhookRefused(Exception):
    """The request isn't a valid, recent, signed webhook."""


def secret_key(secret: str) -> bytes:
    value = secret.strip()
    for prefix in ("v1,whsec_", "whsec_"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    try:
        key = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("the webhook secret isn't valid base64") from exc
    if len(key) < 16:
        raise ValueError("the webhook secret is too short")
    return key


def sign(key: bytes, webhook_id: str, timestamp: str, body: bytes) -> str:
    message = f"{webhook_id}.{timestamp}.".encode() + body
    digest = hmac.new(key, message, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


def verify(
    secret: str,
    headers: Mapping[str, str],
    body: bytes,
    now: Callable[[], float] = time.time,
) -> str:
    """Check the signature and the timestamp; return the webhook id."""
    webhook_id = headers.get("webhook-id", "")
    timestamp = headers.get("webhook-timestamp", "")
    signatures = headers.get("webhook-signature", "")
    if not webhook_id or not timestamp or not signatures:
        raise WebhookRefused("missing webhook headers")
    if len(webhook_id) > 200 or not timestamp.isdigit():
        raise WebhookRefused("malformed webhook headers")
    if abs(now() - int(timestamp)) > TOLERANCE_SECONDS:
        raise WebhookRefused("timestamp outside the tolerance")
    expected = sign(secret_key(secret), webhook_id, timestamp, body)
    for candidate in signatures.split():
        if hmac.compare_digest(candidate.encode(), expected.encode()):
            return webhook_id
    raise WebhookRefused("no valid signature")
