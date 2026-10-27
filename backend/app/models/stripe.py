"""Stripe payments schemas (Slice B2, TRD §6.14)."""

from datetime import datetime

from pydantic import BaseModel


class PaymentAccountOut(BaseModel):
    """The shop's connected Stripe account (Standard equivalent, onboarded on Stripe)."""

    account_id: str
    charges_enabled: bool  # Stripe lets it take card payments
    details_submitted: bool  # the owner finished Stripe's onboarding form
    connected_at: datetime
    disconnected_at: datetime | None


class PaymentSettingsOut(BaseModel):
    stripe_configured: bool  # this server has Stripe set up (test mode)
    is_demo: bool  # demo shops use the showcase test account; they can't connect their own
    account: PaymentAccountOut | None
    card_payments: bool  # shoppers can pay by card at checkout


class OnboardingLinkOut(BaseModel):
    url: str  # Stripe-hosted onboarding; open it in the same tab


class PayNowOut(BaseModel):
    checkout_url: str  # Stripe-hosted Checkout for the order's balance
