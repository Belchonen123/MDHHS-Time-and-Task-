"""Uvicorn entry point — bind only to localhost (127.0.0.1)."""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# Load env from backend/.env, then monorepo root (POC) if present
load_dotenv(_BACKEND_DIR / ".env")
load_dotenv(_BACKEND_DIR.parent / ".env")

from app.api import router as api_router
from app.db import DATA_DIR, STORAGE_DIR, init_db

TEMPLATE_XLSX = _BACKEND_DIR / "templates" / "plan_of_care_template.xlsx"


def run_startup_checks() -> None:
    """Run before the server serves traffic: dirs, required assets, DB, user notices."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    if not TEMPLATE_XLSX.is_file():
        print(
            "ERROR: Missing required template file:\n"
            f"  {TEMPLATE_XLSX}\n"
            "Add templates/plan_of_care_template.xlsx under the backend directory.",
            file=sys.stderr,
        )
        sys.exit(1)
    init_db()
    print("=" * 72, flush=True)
    print(
        "LOCALHOST ONLY: This server binds to 127.0.0.1 - not 0.0.0.0. Do not "
        "expose to the network. No authentication; may handle PHI.",
        flush=True,
    )
    print("=" * 72, flush=True)
    print(
        "Start the SPA: from repo root `npm run dev` (API + Vite), or from "
        "frontend/ `npm run dev` (also starts API + Vite on http://localhost:3456).",
        flush=True,
    )


logger = logging.getLogger("mdhhs_poc")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    run_startup_checks()
    yield


app = FastAPI(title="mdhhs-poc-builder", version="0.1.0", lifespan=lifespan)


@app.exception_handler(HTTPException)
async def _http_exception(request: Request, exc: HTTPException) -> JSONResponse:
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def _validation_exception(request: Request, exc: RequestValidationError) -> JSONResponse:
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(ResponseValidationError)
async def _response_validation(request: Request, exc: ResponseValidationError) -> JSONResponse:
    """e.g. UploadResponse / PlanResponse shape drift — return JSON detail for the UI."""
    logger.exception("Response validation failed: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": jsonable_encoder(exc.errors())},
    )


@app.exception_handler(Exception)
async def _unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    # Log full traceback; return a JSON body so the UI toast can show a hint
    # (Pydantic request validation / HTTPException use handlers above)
    logger.exception("Unhandled: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc!s}"},
    )


# Regex covers any localhost port (Vite may shift ports if 3000/4173 are taken).
_LOCALHOST_BROWSER_ORIGIN = r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_origin_regex=_LOCALHOST_BROWSER_ORIGIN,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8001, reload=False)
