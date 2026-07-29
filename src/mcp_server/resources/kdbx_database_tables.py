import json
import logging
from typing import List

from mcp.types import TextContent

from mcp_server.utils.schema_model import (
    DEFAULT_PREVIEW_ROWS,
    build_database_document,
    build_table_document,
)

logger = logging.getLogger(__name__)


def _as_json(document: dict) -> List[TextContent]:
    return [TextContent(type="text", text=json.dumps(document, indent=2, default=str))]


async def kdbx_describe_tables_impl() -> List[TextContent]:
    """Whole-database schema as the unified aimeta-shaped document."""
    try:
        return _as_json(build_database_document())
    except Exception as error:
        logger.error(f"Database schema analysis failed: {error}")
        return _as_json({"error": f"Failed to analyze database schema: {error}"})


async def kdbx_describe_table_impl(
    table: str, preview_rows: int = DEFAULT_PREVIEW_ROWS
) -> List[TextContent]:
    """Single-table schema, without walking the whole database."""
    try:
        return _as_json(build_table_document(table, preview_rows=preview_rows))
    except Exception as error:
        logger.error(f"Failed to analyze table '{table}': {error}")
        return _as_json({"error": f"Failed to analyze table '{table}': {error}"})


def register_resources(mcp_server):
    @mcp_server.resource("kdbx://tables")
    async def kdbx_describe_tables() -> List[TextContent]:
        """
        Complete KDB-X database schema: every table with its columns, types and a data preview.

        When the KDB-X process has the aimeta module loaded this also carries column
        descriptions, semantic types, foreign-key edges and a `references` index for resolving
        human-readable values to keys. `mcp.tier` reports how much of that is available.

        Returns:
            List[TextContent]: The database schema document.
        """
        return await kdbx_describe_tables_impl()

    @mcp_server.resource("kdbx://tables/{table}")
    async def kdbx_describe_table(table: str) -> List[TextContent]:
        """
        Schema for a single KDB-X table, plus any aimeta references its columns point at.

        Prefer this over kdbx://tables when you already know which table you need - it avoids
        walking every table in the database.

        Args:
            table: Name of the KDB-X table to describe.

        Returns:
            List[TextContent]: The table schema document.
        """
        return await kdbx_describe_table_impl(table)

    return ['kdbx://tables', 'kdbx://tables/{table}']
