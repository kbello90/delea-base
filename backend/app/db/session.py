"""DB session/connection helpers.

Stub. Two access paths will live here:
- tenant-scoped connections for the API, with RLS keyed on the JWT's tenant_id;
- the `ai_readonly` Postgres role for the AI assistant, which holds SELECT only on
  tenant-scoped views (TRD §3.2).
Schema is owned by supabase/migrations/, never by the backend.
"""


def get_connection() -> None:
    raise NotImplementedError("DB connections are not implemented yet")
