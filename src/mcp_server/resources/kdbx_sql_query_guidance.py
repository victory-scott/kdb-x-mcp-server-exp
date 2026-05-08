
import logging

logger = logging.getLogger(__name__)

def kdbx_sql_query_guidance_impl() -> str:
    path = "src/mcp_server/resources/kdbx_sql_query_guidance.txt"
    with open(path, 'r', encoding='utf-8') as file:
        return file.read()

def register_resources(mcp_server):
    @mcp_server.resource("file://guidance/kdbx-sql-queries")
    async def kdbx_sql_query_guidance() -> str:
        """
        Provides guidance when using SQL select statements with the kdbx_run_sql_query tool.

        Returns:
            str: Details and examples on supported select statement when using the sql tool.
        """
        # Spike Layer 1: principal logging on a static-content resource. No
        # data-sensitivity story here (it's documentation), but completes
        # the picture that every primitive — tool, resource, prompt —
        # reaches application code with the same get_access_token() call.
        # No Layer 2 entitlement work expected for static guidance.
        try:
            from mcp.server.auth.middleware.auth_context import get_access_token
            tok = get_access_token()
            if tok is not None:
                logger.info(
                    f"SPIKE: resource 'file://guidance/kdbx-sql-queries' read by "
                    f"client_id={tok.client_id!r} scopes={tok.scopes!r}"
                )
        except Exception as e:
            logger.debug(f"SPIKE: get_access_token unavailable: {e}")

        return kdbx_sql_query_guidance_impl()
    return ['file://guidance/kdbx-sql-queries']