"""Supabase Auth admin API (service-role key): find, create and delete users.

For provisioning and local tooling only (the showcase loader, create_dev_users.py). Users are
created already confirmed, so Supabase sends no confirmation or invite email. The key never
reaches a browser.
"""

from dataclasses import dataclass

import httpx


class AuthAdminError(Exception):
    """Auth answered with an unexpected status."""


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

    async def delete_user(self, user_id: str) -> None:
        async with self._client() as auth:
            r = await auth.delete(f"/admin/users/{user_id}")
        if r.status_code not in (200, 404):
            raise AuthAdminError(f"delete user failed: {r.status_code}")
