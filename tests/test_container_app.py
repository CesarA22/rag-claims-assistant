"""T-43 / T-44 / D-03: the container shell — one origin, and a live lifespan.

The image serves the client and the API from a single port, which is what keeps
S8's decision to add no CORS middleware true in a deployment and not only behind
the Vite dev proxy. Both tests are about the wrapper; `create_app()` is untouched
and every other test still drives it directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.main import create_app, mount_client

INDEX = "<!doctype html><title>InsurCo</title>"


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    """A stand-in for web/dist, which is generated and not in the repository."""
    (tmp_path / "index.html").write_text(INDEX, encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("export const x = 1\n", encoding="utf-8")
    return tmp_path


async def test_api_under_api_prefix_and_client_at_root(llm, retriever, repo, dist):
    """T-43 / D-03: /api/healthz reaches the API and / serves the built client.

    `web/src/api.ts` hardcodes BASE='/api' and the dev proxy strips the prefix
    before forwarding, so mounting the API at /api is what reproduces the dev
    contract in the image. The static mount is registered second on purpose:
    Starlette matches in order, and a StaticFiles mount at / registered first
    would swallow every API path.
    """
    api = create_app(llm=llm, retriever=retriever, repo=repo, provider_name="fake")
    shell = mount_client(api, dist=dist)

    async with AsyncClient(
        transport=ASGITransport(app=shell), base_url="http://test"
    ) as client:
        health = await client.get("/api/healthz")
        assert health.status_code == 200
        assert health.json()["provider"] == "fake"
        # The trace id middleware belongs to the mounted app; a mount does not
        # strip it, which is why the API keeps its RFC 9457 behaviour.
        assert health.headers.get("x-trace-id")

        # No route moved: the prefix is a mount, not a router prefix.
        assert await client.get("/healthz") != health
        assert (await client.get("/healthz")).status_code == 404

        # The API's docs live with the API. The shell's own would be an empty
        # schema served from three paths the client should own.
        assert (await client.get("/api/docs")).status_code == 200
        assert (await client.get("/api/openapi.json")).status_code == 200

        index = await client.get("/")
        assert index.status_code == 200
        assert index.text == INDEX
        assert (await client.get("/assets/app.js")).status_code == 200


async def test_shell_drives_the_mounted_apps_lifespan():
    """T-44 / D-03: the shell runs the mounted app's startup and shutdown.

    Starlette routes only http and websocket scopes into a mounted app — the
    lifespan scope never reaches it. Without the forwarding in `mount_client`,
    `create_app()`'s startup hook never runs, `state.retriever` stays the
    two-chunk `InMemoryRetriever`, and the container answers every question from
    fixtures while reporting itself healthy. That is the failure this pins.
    """
    fired: list[str] = []
    child = FastAPI()

    @child.on_event("startup")
    async def _up() -> None:
        fired.append("startup")

    @child.on_event("shutdown")
    async def _down() -> None:
        fired.append("shutdown")

    shell = mount_client(child, dist=Path("no-such-directory"))

    async with shell.router.lifespan_context(shell):
        assert fired == ["startup"]
    assert fired == ["startup", "shutdown"]


def test_missing_dist_leaves_the_api_mounted(llm, retriever, repo):
    """T-43 / D-03: no client build is not a broken API.

    `pytest` runs from a checkout where `web/dist` does not exist. The static
    mount is skipped rather than raising, so the wrapper stays importable and
    the API half is unaffected.
    """
    shell = mount_client(
        create_app(llm=llm, retriever=retriever, repo=repo, provider_name="fake"),
        dist=Path("no-such-directory"),
    )
    assert [route.path for route in shell.routes] == ["/api"]
