"""The caller's tenant currency, read through public.current_tenant_currency().

The function (migration 20261020001400) takes tenant and role from the verified JWT claims of
the user session and checks the membership, so it must run in the caller's user session.
"""

from psycopg import AsyncConnection


async def fetch_currency(conn: AsyncConnection) -> str:
    async with conn.cursor() as cur:
        await cur.execute("select public.current_tenant_currency()")
        row = await cur.fetchone()
    assert row is not None  # the function returns a value or raises
    return row[0]
