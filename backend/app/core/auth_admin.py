"""Supabase Auth admin API (service-role key): find, create, invite and delete users, and send
password-reset emails.

Provisioning and local tooling (the showcase loader, create_dev_users.py) create users already
confirmed, so Supabase sends them no email. Owner invites (POST /api/v1/members) and password
resets (POST /api/v1/auth/password-reset) are the only paths that make Auth send email; the
links in those emails go to admin pages (supabase/templates). The key never reaches a browser.
"""

from dataclasses import dataclass
from typing import Any

import httpx


class AuthAdminError(Exception):
    """Auth answered with an unexpected status, or couldn't be reached."""


class EmailExists(AuthAdminError):
    """Auth refused an invite because the email already has a confirmed account."""


@dataclass(frozen=True)
class AuthAdmin:
    supabase_url: str
    service_role_key: str

    def _client(self) -> httpx.AsyncClient:
        key = self.service_role_key
        return httpx.AsyncClient(
            base_url=f"{self.supabase_url.rstrip('/')}/auth/v1",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=15,
        )

    async def find_user_id(self, email: str) -> str | None:
        """The user's id, or None. Pages through all users (fine for tooling, not hot paths)."""
        wanted = email.strip().lower()
        async with self._client() as auth:
            page = 1
            while True:
                r = await auth.get("/admin/users", params={"page": page, "per_page": 1000})
                if r.status_code != 200:
                    raise AuthAdminError(f"list users failed: {r.status_code}")
                users = r.json().get("users", [])
                for user in users:
                    if (user.get("email") or "").lower() == wanted:
                        return user["id"]
                if len(users) < 1000:
                    return None
                page += 1

    async def ensure_user(self, email: str, password: str) -> str:
        """Create a confirmed user, or reset an existing one's password. Returns the id."""
        body = {"password": password, "email_confirm": True}
        user_id = await self.find_user_id(email)
        async with self._client() as auth:
            if user_id is None:
                r = await auth.post("/admin/users", json={"email": email, **body})
            else:
                r = await auth.put(f"/admin/users/{user_id}", json=body)
        if r.status_code not in (200, 201):
            raise AuthAdminError(f"create/update user {email} failed: {r.status_code} {r.text}")
        return r.json()["id"]

    async def invite(self, email: str, data: dict[str, Any]) -> str:
        """Send an invite email and return the user's id.

        Creates the user (unconfirmed) if there is none. For an existing user who hasn't
        accepted yet, Auth sends a fresh invite (the earlier link stops working). A confirmed
        account raises EmailExists. `data` is the user's metadata, readable by the email
        template as {{ .Data.<key> }}.
        """
        try:
            async with self._client() as auth:
                r = await auth.post("/invite", json={"email": email, "data": data})
        except httpx.HTTPError as exc:
            raise AuthAdminError(f"invite failed: {exc.__class__.__name__}") from exc
        if r.status_code == 200:
            return r.json()["id"]
        if r.status_code == 422 and _error_code(r) == "email_exists":
            raise EmailExists("the email already has an account")
        raise AuthAdminError(f"invite failed: {r.status_code} {_error_code(r)}")

    async def send_recovery(self, email: str) -> None:
        """Ask Auth to email a password-reset link. Auth answers the same whether or not the
        account exists."""
        try:
            async with self._client() as auth:
                r = await auth.post("/recover", json={"email": email})
        except httpx.HTTPError as exc:
            raise AuthAdminError(f"recover failed: {exc.__class__.__name__}") from exc
        if r.status_code != 200:
            raise AuthAdminError(f"recover failed: {r.status_code} {_error_code(r)}")

    async def delete_user(self, user_id: str) -> None:
        async with self._client() as auth:
            r = await auth.delete(f"/admin/users/{user_id}")
        if r.status_code not in (200, 404):
            raise AuthAdminError(f"delete user failed: {r.status_code}")


def _error_code(response: httpx.Response) -> str | None:
    try:
        return response.json().get("error_code")
    except ValueError:
        return None
