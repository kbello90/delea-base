"""Supabase client factory (tenant-scoped role).

Stub. Will build Supabase clients from Settings: a user-scoped client that forwards the
caller's JWT so RLS applies, and a service-role client for admin actions such as CPA
invites (TRD §3.4).
"""


def get_supabase_client() -> None:
    raise NotImplementedError("Supabase client factory is not implemented yet")
