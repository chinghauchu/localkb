"""Read-only MCP server: stdio for local clients, streamable HTTP for a private tunnel."""

from __future__ import annotations

import hmac
import sys
from pathlib import Path

from pydantic import BaseModel
from starlette.responses import JSONResponse

from localkb import __version__
from localkb.models import Hit
from localkb.pipeline import fetch_chunk, list_documents, require_kb, search_kb, status_kb

INSTRUCTIONS = (
    "localkb is a read-only knowledge base stored on the user's computer. "
    "Always cite the source file and location for every claim you take from these tools. "
    "A location is a page number, slide number, sheet name, or section heading. "
    "Use search to find excerpts, then fetch with an excerpt id to read the full chunk. "
    "Use list_documents to see which files are indexed and status for index health. "
    "If the excerpts do not contain the answer, say so. Do not invent citations or file names."
)

SEARCH_DESCRIPTION = (
    "Search the local knowledge base. Returns excerpts with id, file, location "
    "(page, slide, sheet, or section), score, and text, plus title and url. "
    "Cite the file and location whenever you use an excerpt. "
    "k is the maximum number of excerpts (default 5)."
)
FETCH_DESCRIPTION = (
    "Fetch one knowledge-base excerpt by the id returned from search. "
    "Returns id, title, text, url, and metadata with the file and location. "
    "Cite that file and location."
)


class SearchHitModel(BaseModel):
    id: str
    title: str
    url: str
    file: str
    locator_type: str
    locator: str
    location: str
    page: int | None = None
    slide: int | None = None
    sheet: str | None = None
    score: float
    text: str


class SearchResultModel(BaseModel):
    results: list[SearchHitModel]


class FetchResultModel(BaseModel):
    id: str
    title: str
    text: str
    url: str
    metadata: dict[str, object]


class DocumentModel(BaseModel):
    file: str
    chunks: int


class DocumentListModel(BaseModel):
    documents: list[DocumentModel]


class StatusModel(BaseModel):
    folder: str
    files: int
    chunks: int
    embedder: str
    model: str
    updated: str


def build_server(folder: Path):
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations

    folder = require_kb(folder).parent
    server = MCPServer(
        name="localkb",
        instructions=INSTRUCTIONS,
        version=__version__,
        log_level="WARNING",
    )
    read_only = ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )

    @server.tool(
        name="search",
        description=SEARCH_DESCRIPTION,
        annotations=read_only,
    )
    def search(query: str, k: int = 5) -> SearchResultModel:
        hits = search_kb(folder, query, k=k)
        return SearchResultModel(results=[_search_hit(folder, hit) for hit in hits])

    @server.tool(name="fetch", description=FETCH_DESCRIPTION, annotations=read_only)
    def fetch(id: str) -> FetchResultModel:
        hit = fetch_chunk(folder, id)
        fields = _search_hit(folder, hit)
        return FetchResultModel(
            id=hit.id,
            title=hit.title,
            text=hit.text,
            url=hit.url(folder),
            metadata={
                "file": hit.path,
                "locator_type": hit.locator_type,
                "locator": hit.locator,
                "location": hit.location,
                "page": fields.page,
                "slide": fields.slide,
                "sheet": fields.sheet,
            },
        )

    @server.tool(
        name="list_documents",
        description="List indexed documents and how many excerpts each one produced.",
        annotations=read_only,
    )
    def list_documents_tool() -> DocumentListModel:
        documents = [
            DocumentModel(file=str(item["file"]), chunks=int(item["chunks"]))
            for item in list_documents(folder)
        ]
        return DocumentListModel(documents=documents)

    @server.tool(
        name="status",
        description="Report knowledge-base size, embedding model, and last update time.",
        annotations=read_only,
    )
    def status() -> StatusModel:
        info = status_kb(folder)
        return StatusModel(
            folder=str(info["folder"]),
            files=int(info["files"]),
            chunks=int(info["chunks"]),
            embedder=str(info["embedder"]),
            model=str(info["model"]),
            updated=str(info["updated"]),
        )

    return server


def serve_stdio(folder: Path) -> None:
    build_server(folder).run(transport="stdio")


def create_http_app(folder: Path, *, token: str | None, host: str):
    from mcp.server.transport_security import TransportSecuritySettings

    server = build_server(folder)

    @server.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(_request):
        return JSONResponse({"ok": True, "name": "localkb"})

    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=False,
        stateless_http=True,
        host=host,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    if token:
        app.add_middleware(_BearerMiddleware, token=token)
    return app


def serve_http(folder: Path, *, host: str, port: int, token: str | None) -> None:
    import uvicorn

    print(http_banner(host, port, token_set=bool(token)), file=sys.stderr)
    app = create_http_app(folder, token=token, host=host)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    uvicorn.Server(config).run()


def http_banner(host: str, port: int, *, token_set: bool) -> str:
    endpoint = f"http://{host}:{port}/mcp"
    lines = [
        f"localkb HTTP mode serves indexed excerpts on {endpoint}",
        "Parsing, embeddings, and search stay on this machine. "
        "A client receives excerpt text only when it calls a tool.",
        "",
        "A tunnel (cloudflared, ngrok, Tailscale Funnel, or ssh -R) lets a hosted "
        "assistant such as ChatGPT reach this port. Anyone who can open that URL can "
        "read excerpts from the knowledge base. Prefer a private network such as "
        "Tailscale or SSH, and set a bearer token:",
        "",
        '    localkb serve --http --port 8765 --token "$LOCALKB_TOKEN"',
        "",
        "Point the client at the /mcp URL and send Authorization: Bearer <token>.",
        "Host checks are disabled so a tunnel can rewrite the Host header.",
        "Bind to 127.0.0.1 unless you intend to expose the port.",
        "Do not publish the URL or the token.",
    ]
    if not token_set:
        lines.extend(
            [
                "",
                "No bearer token is set. Any client that can reach this port can read excerpts.",
            ]
        )
    if host not in {"127.0.0.1", "localhost", "::1"}:
        lines.extend(
            [
                "",
                f"The server is bound to {host}, so it is reachable beyond this machine.",
            ]
        )
    return "\n".join(lines)


def _search_hit(folder: Path, hit: Hit) -> SearchHitModel:
    page = int(hit.locator) if hit.locator_type == "page" and hit.locator.isdigit() else None
    slide = int(hit.locator) if hit.locator_type == "slide" and hit.locator.isdigit() else None
    sheet = hit.locator if hit.locator_type == "sheet" else None
    return SearchHitModel(
        id=hit.id,
        title=hit.title,
        url=hit.url(folder),
        file=hit.path,
        locator_type=hit.locator_type,
        locator=hit.locator,
        location=hit.location,
        page=page,
        slide=slide,
        sheet=sheet,
        score=round(hit.score, 6),
        text=hit.text,
    )


class _BearerMiddleware:
    """ASGI middleware. Health stays open; every other route requires the bearer token."""

    def __init__(self, app, token: str) -> None:
        self.app = app
        self._expected = f"Bearer {token}"

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http" or scope.get("path") == "/health":
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        if not _constant_equal(headers.get("authorization", ""), self._expected):
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _constant_equal(got: str, expected: str) -> bool:
    if len(got) != len(expected):
        return False
    return hmac.compare_digest(got, expected)
