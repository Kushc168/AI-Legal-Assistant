"""FastAPI entry point. Serves the API under /api and the single-page frontend at /."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .config import FRONTEND_DIR, get_settings
from .prompts import loader
from .routes import chat, compare, contracts, risk, workspace

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    workspace.apply_overrides()
    prompts = loader.registry()
    settings = get_settings()
    log.info("Loaded %d prompts from PROMPTS.md", len(prompts))
    log.info("AI mode: %s%s", settings.llm_provider,
             f" ({settings.llm_model})" if settings.llm_provider == "anthropic" else " (no LLM calls)")
    # Contracts or jobs interrupted by a restart are marked failed so the user can retry them.
    with db.connect() as conn:
        conn.execute("UPDATE contracts SET status = 'failed', error = 'Interrupted by a restart. Use Re-index.' "
                     "WHERE status = 'processing'")
        conn.execute("UPDATE risk_reports SET status = 'failed', error = 'Interrupted by a restart.' "
                     "WHERE status IN ('queued', 'running')")
        conn.execute("UPDATE comparisons SET status = 'failed', error = 'Interrupted by a restart.' "
                     "WHERE status IN ('queued', 'running')")
    yield


app = FastAPI(title="AI Legal Assistant", version="1.0.0", lifespan=lifespan)

for module in (contracts, risk, chat, compare, workspace):
    app.include_router(module.router)


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    log.exception("Unhandled error")
    return JSONResponse(status_code=500, content={"detail": "Something went wrong. Check the server log."})


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    return FileResponse(FRONTEND_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("backend.main:app", host=s.host, port=s.port)
