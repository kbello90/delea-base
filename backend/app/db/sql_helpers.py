"""Small SQL-building helpers shared by the v1 routers.

Column names always come from the request model's declared fields, never from raw request
keys, and values are always bound parameters.
"""

from collections.abc import Iterable, Mapping

from fastapi import HTTPException, status
from psycopg import sql
from pydantic import BaseModel


def like_pattern(term: str) -> str:
    """ILIKE pattern that matches `term` literally (its % and _ are not wildcards)."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def patch_changes(body: BaseModel, nullable: Iterable[str]) -> dict:
    """The fields a PATCH body actually sent, or 422 if it is empty or nulls a required field."""
    changes = body.model_dump(mode="json", exclude_unset=True)
    if not changes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No fields to update")
    allowed_null = set(nullable)
    null_not_allowed = sorted(k for k, v in changes.items() if v is None and k not in allowed_null)
    if null_not_allowed:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"These fields cannot be null: {', '.join(null_not_allowed)}",
        )
    return changes


def update_statement(
    table: str,
    changes: Mapping[str, object],
    model: type[BaseModel],
    returning: str,
    casts: Mapping[str, str] | None = None,
    key_column: str = "id",
) -> sql.Composed:
    """`update public.<table> set ... where <key> = %(key)s and tenant_id = %(tenant_id)s`.

    Bind parameters: every changed field, plus `key` and `tenant_id`.
    """
    casts = casts or {}
    assignments = [
        sql.SQL("{} = {}").format(
            sql.Identifier(field),
            sql.SQL("{}::{}").format(sql.Placeholder(field), sql.SQL(casts[field]))
            if field in casts
            else sql.Placeholder(field),
        )
        for field in changes
        if field in model.model_fields
    ]
    return sql.SQL(
        "update {table} set {assignments} where {key} = %(key)s and tenant_id = %(tenant_id)s "
        "returning {returning}"
    ).format(
        table=sql.Identifier("public", table),
        assignments=sql.SQL(", ").join(assignments),
        key=sql.Identifier(key_column),
        returning=sql.SQL(returning),
    )
