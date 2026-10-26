"""Auth configuration in supabase/config.toml (no database needed).

Public sign-up must stay off: accounts come only from provisioning and owner invites. The
email provider must stay on ([auth.email] enable_signup is that switch, not the sign-up one),
or password sign-in, invites and resets stop working.
"""

import tomllib
from pathlib import Path

from scripts.check_auth_settings import settings_problems

ROOT = Path(__file__).resolve().parents[2]
AUTH = tomllib.loads((ROOT / "supabase" / "config.toml").read_text(encoding="utf-8"))["auth"]


def test_public_sign_up_is_off() -> None:
    assert AUTH["enable_signup"] is False


def test_the_email_provider_stays_on() -> None:
    assert AUTH["email"]["enable_signup"] is True


def test_passwords_need_at_least_ten_characters() -> None:
    assert AUTH["minimum_password_length"] >= 10


def test_invite_and_reset_links_last_24_hours() -> None:
    assert AUTH["email"]["otp_expiry"] == 86400


def test_invite_and_reset_emails_link_to_the_admin_pages() -> None:
    for kind, page in (("invite", "/accept-invite"), ("recovery", "/reset-password")):
        template = AUTH["email"]["template"][kind]
        body = (ROOT / template["content_path"]).read_text(encoding="utf-8")
        assert f"{{{{ .SiteURL }}}}{page}?token_hash={{{{ .TokenHash }}}}&type={kind}" in body


def test_settings_check() -> None:
    good = {"disable_signup": True, "external": {"email": True}}
    assert settings_problems(good) == []
    assert len(settings_problems({**good, "disable_signup": False})) == 1
    assert len(settings_problems({**good, "external": {"email": False}})) == 1
    assert len(settings_problems({})) == 2
