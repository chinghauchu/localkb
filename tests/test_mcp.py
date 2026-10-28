import asyncio
import json
import sys
from pathlib import Path

from starlette.testclient import TestClient

from localkb.mcp_server import INSTRUCTIONS, build_server, create_http_app, http_banner


def _payload(result) -> dict:
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    return json.loads(result.content[0].text)


def test_server_instructions_require_citations(corpus: Path) -> None:
    server = build_server(corpus)
    assert "cite" in (server.instructions or "").lower()
    assert server.instructions == INSTRUCTIONS
    tools = asyncio.run(server.list_tools())
    names = {tool.name for tool in tools}
    assert names == {"search", "fetch", "list_documents", "status"}
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True


def test_mcp_tools_return_citations(corpus: Path) -> None:
    server = build_server(corpus)

    async def call() -> None:
        search = await server.call_tool("search", {"query": "本季營收", "k": 3})
        payload = _payload(search)
        hit = payload["results"][0]
        assert hit["file"] == "reports/quarter.pdf"
        assert hit["page"] == 2
        assert hit["id"]
        assert hit["score"] > 0
        assert hit["title"]
        assert hit["url"].startswith("file:")

        fetched = await server.call_tool("fetch", {"id": hit["id"]})
        full = _payload(fetched)
        assert full["id"] == hit["id"]
        assert "四千兩百萬" in full["text"]
        assert full["metadata"]["file"] == "reports/quarter.pdf"
        assert full["metadata"]["location"] == "page 2"
        assert full["url"]

        listed = await server.call_tool("list_documents", {})
        documents = _payload(listed)["documents"]
        files = {item["file"] for item in documents}
        assert "notes/budget.xlsx" in files
        assert "notes/overview.pptx" in files

        status = await server.call_tool("status", {})
        info = _payload(status)
        assert info["files"] == 6
        assert info["model"] == "hash-v1"

    asyncio.run(call())


def test_http_requires_bearer_token(corpus: Path) -> None:
    app = create_http_app(corpus, token="secret", host="127.0.0.1")
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True
        denied = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert denied.status_code == 401
        allowed = client.post(
            "/mcp",
            headers={"Authorization": "Bearer secret"},
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
        assert allowed.status_code != 401


def test_http_banner_warns_about_tunnels() -> None:
    text = http_banner("127.0.0.1", 8765, token_set=False)
    assert "tunnel" in text.lower()
    assert "bearer token" in text.lower()
    assert "No bearer token" in text


def test_stdio_client_can_search(corpus: Path) -> None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    root = Path(__file__).resolve().parents[1]
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "localkb", "serve", "--folder", str(corpus)],
        env={"PYTHONPATH": str(root / "src")},
    )

    async def call() -> None:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} >= {
                    "search",
                    "fetch",
                    "list_documents",
                    "status",
                }
                result = await session.call_tool("search", {"query": "March", "k": 2})
                payload = _payload(result)
                assert payload["results"][0]["file"] == "notes/launch.docx"

    asyncio.run(call())
