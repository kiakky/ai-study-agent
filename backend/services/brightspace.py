
"""Brightspace MCP client for AI Study Agent."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

READ_TOOLS = frozenset({"get_my_courses", "get_course_content", "get_syllabus", "get_announcements",
    "get_assignments", "get_upcoming_due_dates", "get_calendar_events", "search_course", "download_file"})


class ReadOnlySession:
    """Positive read allowlist at the shared MCP boundary, including legacy helpers."""
    def __init__(self, session):
        self._session = session
        self._allowed = None

    async def list_tools(self):
        response = await self._session.list_tools()
        allowed = []
        for tool in response.tools:
            annotations = getattr(tool, "annotations", None)
            hint = annotations.get("readOnlyHint") if isinstance(annotations, dict) else getattr(annotations, "readOnlyHint", None)
            if tool.name in READ_TOOLS and hint is True:
                allowed.append(tool)
        self._allowed = {tool.name for tool in allowed}
        return SimpleNamespace(tools=allowed)

    async def call_tool(self, name, arguments=None):
        if name not in READ_TOOLS:
            raise ValueError("Brightspace operation is not on the permanent read-only allowlist.")
        if self._allowed is None:
            await self.list_tools()
        if name not in self._allowed:
            raise ValueError("Brightspace tool is unavailable or lacks verified read-only metadata.")
        if name == "download_file" and any(k in (arguments or {}) for k in ("downloadPath", "download_path", "outputPath", "output_path")):
            raise ValueError("Arbitrary MCP filesystem output paths are prohibited.")
        return await self._session.call_tool(name, arguments=arguments or {})

from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def server_parameters():
    """Configure the Brightspace MCP subprocess on Windows."""

    # The LMS subprocess does not need the bot, Calendar or future LLM secrets.
    private_prefixes=('TELEGRAM_','GOOGLE_','OPENAI_','LLM_','IBM_','WATSONX_','WXO_')
    env = {key:value for key,value in os.environ.items() if not key.upper().startswith(private_prefixes)}

    # Never inherit a default institution or a shared developer session directory.
    origin = env.get("D2L_BASE_URL", "")
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Configure your own HTTPS D2L_BASE_URL before starting Brightspace.")
    env["D2L_SESSION_DIR"] = str(Path(env.get("D2L_SESSION_DIR") or Path(__file__).resolve().parents[2] / ".private-lms" / "session").resolve())
    env["D2L_NO_UPDATE_CHECK"] = "1"
    Path(env["D2L_SESSION_DIR"]).mkdir(parents=True, exist_ok=True)

    # Make the login browser visible if authentication is needed.
    env["D2L_HEADLESS"] = "false"

    return StdioServerParameters(
        command="cmd.exe",
        args=[
            "/c",
            "npx",
            "-y",
            "brightspace-mcp-server@3.13.18",
        ],
        env=env,
        cwd=env["D2L_SESSION_DIR"],
    )


@asynccontextmanager
async def brightspace_connection():
    """Open and close an authenticated MCP session."""

    params = server_parameters()

    # Child diagnostics may contain SSO/session information. The application emits
    # only sanitized operation failures, never raw server stderr or source dumps.
    with open(os.devnull,'w') as diagnostics:
        async with stdio_client(params,errlog=diagnostics) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield ReadOnlySession(session)


async def list_available_tools():
    """Discover Brightspace tools and their input schemas."""

    async with brightspace_connection() as session:
        response = await session.list_tools()
        return response.tools


async def call_brightspace_tool(tool_name, arguments=None):
    """Call a Brightspace tool and return its MCP response."""

    if tool_name not in READ_TOOLS:
        raise ValueError("Brightspace operation is not read-only.")
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
