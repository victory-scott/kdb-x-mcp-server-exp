import logging
from typing import Any, Dict

from mcp_server.utils.kdbx import get_kdb_connection
from mcp_server.utils.schema_model import DEFAULT_PREVIEW_ROWS, build_table_document

logger = logging.getLogger(__name__)


async def kdbx_get_table_metadata_impl(
    table: str, preview_rows: int = DEFAULT_PREVIEW_ROWS
) -> Dict[str, Any]:
    try:
        document = build_table_document(table, preview_rows=preview_rows)
        if "error" in document:
            return {"status": "error", "message": document["error"]}
        return {"status": "success", **document}
    except Exception as error:
        logger.error(f"Failed to read metadata for table '{table}': {error}")
        return {"status": "error", "message": str(error)}


async def kdbx_refresh_metadata_impl() -> Dict[str, Any]:
    from mcp_server.utils.aimeta import reload_remote_metadata

    try:
        conn = get_kdb_connection()
        reloaded = reload_remote_metadata(conn)
    except Exception as error:
        logger.error(f"Metadata refresh failed: {error}")
        return {"status": "error", "message": str(error)}

    return {
        "status": "success",
        "remoteReload": reloaded,
        "message": (
            "Asked KDB-X to re-read its compiled metadata and cleared the local cache"
            if reloaded
            else "Cleared the local metadata cache (KDB-X has no aimeta module loaded)"
        ),
    }


def register_tools(mcp_server):
    @mcp_server.tool()
    async def kdbx_get_table_metadata(
        table: str, preview_rows: int = DEFAULT_PREVIEW_ROWS
    ) -> Dict[str, Any]:
        """
        Get the full schema for one KDB-X table before writing a query against it.

        Returns columns with kdb types, plus - when the KDB-X process has aimeta loaded and
        its q source annotated - column descriptions, semantic types, foreign-key edges and
        the `references` entries needed to resolve a human-readable value to a key (for
        example turning "US Dollar" into `USD before filtering on a currency column).

        `live.rowCount` and `live.partitioned` are always current. `live.rowSource` says what
        the example rows are: "preview" means real rows read from the table just now,
        "sampleData" means illustrative rows from the annotations - do not assume the values
        in those exist in the data.

        Args:
            table: Name of the KDB-X table.
            preview_rows: How many real rows to read. 0 skips the read entirely.

        Returns:
            Dict[str, Any]: status plus the table schema document.
        """
        return await kdbx_get_table_metadata_impl(table, preview_rows)

    @mcp_server.tool()
    async def kdbx_refresh_metadata() -> Dict[str, Any]:
        """
        Re-read KDB-X table and function metadata, picking up annotation changes.

        Use after recompiling aimeta annotations on the KDB-X process so new descriptions and
        function signatures appear without restarting this MCP server.

        Returns:
            Dict[str, Any]: status and whether the KDB-X process reloaded its own metadata.
        """
        return await kdbx_refresh_metadata_impl()

    return ['kdbx_get_table_metadata', 'kdbx_refresh_metadata']
