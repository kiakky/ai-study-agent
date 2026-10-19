
"""Brightspace MCP client for AI Study Agent."""

import asyncio
import json
import os

from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def server_parameters():
    """Use pinned upstream session management without changing retrieval."""
    from services.lms_upstream_startup import upstream_parameters
    return upstream_parameters()


@asynccontextmanager
async def brightspace_connection():
    """Open and close an authenticated MCP session."""

    params = server_parameters()

    # Child diagnostics may contain SSO/session information. The application emits
    # only sanitized operation failures, never raw server stderr or source dumps.
    with open(os.devnull,'w') as diagnostics:
        async with stdio_client(params,errlog=diagnostics) as (read, write):
            async with ClientSession(read, write) as session:
                initial = await session.initialize()
                from services.lms_policy import ReadOnlySession, value
                info = value(initial, 'server_info', value(initial, 'serverInfo'))
                yield ReadOnlySession(session, value(info, 'version'), private_downloads=True)


async def list_available_tools():
    """Discover Brightspace tools and their input schemas."""

    async with brightspace_connection() as session:
        response = await session.list_tools()
        return response.tools


async def call_brightspace_tool(tool_name, arguments=None):
    """Call a Brightspace tool and return its MCP response."""

    from services.lms_policy import validate_arguments
    validate_arguments(tool_name, {} if arguments is None else arguments)
    async with brightspace_connection() as session:
        result = await session.call_tool(
            tool_name,
            arguments=arguments or {},
        )

        if getattr(result,'isError',getattr(result,'is_error',False)):
            # MCP errors may contain private LMS/session details. Do not log them.
            raise RuntimeError('Brightspace MCP operation failed.')

        return result




async def main():
    """Explicit operator diagnostic only; never dump private course content."""
    tools=await list_available_tools()
    print('Brightspace MCP tools available:',len(tools))




if __name__ == "__main__":
    asyncio.run(main())
