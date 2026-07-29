import json
import logging
from typing import List

from mcp.types import TextContent

from mcp_server.utils.schema_model import build_functions_document

logger = logging.getLogger(__name__)


async def kdbx_functions_impl() -> List[TextContent]:
    try:
        document = build_functions_document()
    except Exception as error:
        logger.error(f"Function discovery failed: {error}")
        document = {"error": f"Failed to read function metadata: {error}", "functions": []}
    return [TextContent(type="text", text=json.dumps(document, indent=2, default=str))]


def register_resources(mcp_server):
    # Registered unconditionally, unlike the AI-libs tools which skip registration when the
    # capability is missing. Below tier 3 this returns an empty list plus a hint naming what
    # annotating the q source would add - a visibly empty surface teaches the user something,
    # an absent one teaches nothing.
    @mcp_server.resource("kdbx://functions")
    async def kdbx_functions() -> List[TextContent]:
        """
        Callable functions the KDB-X process documents, with parameters, return types,
        worked examples and the tables each one reads.

        Requires the aimeta module loaded on the KDB-X process AND annotated q source; below
        that this returns an empty list and `mcp.hint` explains why. Examples are verbatim
        from the annotations and are illustrative - never execute one blind.

        Returns:
            List[TextContent]: The documented function surface.
        """
        return await kdbx_functions_impl()

    return ['kdbx://functions']
