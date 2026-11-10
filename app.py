"""LinguaLink360 AI Scribe v4 - FastAPI entry point.

Run:  uvicorn app:app --host 127.0.0.1 --port 8501
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import time
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from scribe import __version__
from scribe.config import ENV_FILE, ROOT, settings
from scribe.facts import check_draft
from scribe.pdf import build_pdf
from scribe.pipeline import regenerate_section, run_draft
from scribe.prompts import PROMPT_VERSION
from scribe.providers import Provider, ProviderError, make_provider
from scribe.schema import CheckRequest, DraftRequest, ExportRequest, RegenerateRequest

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("scribe")
# Never log request bodies: they contain clinical text.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)

STATIC = ROOT / "static"
HEARTBEAT_SECONDS = 10

app = FastAPI(title="LinguaLink360 AI Scribe", version=__version__,
              description="Clinical analysis and SOAP drafting. All output is a draft for clinician review.")
app.state.provider = make_provider(settings)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def get_provider(request: Request) -> Provider:
    return request.app.state.provider


async def require_token(request: Request) -> None:
    """Optional service token (set SERVICE_TOKEN when Laravel calls this service server-to-server)."""
    if not settings.service_token:
        return
    supplied = request.headers.get("x-service-token") or request.headers.get("authorization", "").removeprefix("Bearer ")
    if not secrets.compare_digest(supplied.encode(), settings.service_token.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing service token.")


class SecurityHeaders:
    """Plain ASGI middleware (BaseHTTPMiddleware interferes with streaming and client disconnects)."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)
        is_api = scope["path"].startswith("/api/")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers += [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer")]
                if is_api and not any(k.lower() == b"cache-control" for k, _ in headers):
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        await self.inner(scope, receive, send_with_headers)


class BasicAuth:
    """Optional HTTP Basic auth for a hosted staging site (BASIC_AUTH_USER + BASIC_AUTH_PASSWORD).

    /health stays open so the hosting platform's health check works. Plain ASGI so streaming is unaffected.
    """

    OPEN_PATHS = {"/health"}

    def __init__(self, inner, user: str, password: str):
        self.inner = inner
        self.expected = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode() if user and password else ""

    async def __call__(self, scope, receive, send):
        if not self.expected or scope["type"] != "http" or scope["path"] in self.OPEN_PATHS:
            return await self.inner(scope, receive, send)
        supplied = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"authorization"), "")
        if secrets.compare_digest(supplied.encode(), self.expected.encode()):
            return await self.inner(scope, receive, send)
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"www-authenticate", b'Basic realm="LinguaLink360 Scribe", charset="UTF-8"'),
                                (b"content-type", b"text/plain; charset=utf-8"), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": b"Sign-in required."})


app.add_middleware(SecurityHeaders)
app.add_middleware(BasicAuth, user=settings.basic_auth_user, password=settings.basic_auth_password)


@app.exception_handler(ProviderError)
async def provider_error_handler(_: Request, exc: ProviderError):
    return JSONResponse(status_code=exc.status, content={"detail": exc.message, "code": exc.code})


# --------------------------------------------------------------------------------------
# Pages and status
# --------------------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def home():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/health")
async def health():
    return {"status": "ok", "service": "LinguaLink360 AI Scribe", "version": __version__}


@app.get("/api/scribe/status")
async def status(provider: Provider = Depends(get_provider)):
    """Safe configuration summary; never returns the key."""
    return {
        "ready": settings.ready,
        "provider": provider.name,
        "clinical_model": provider.model,
        "transcription_model": getattr(provider, "transcription_model", "mock"),
        "prompt_version": PROMPT_VERSION,
        "api_key_configured": bool(settings.openai_api_key),
        "env_file_found": ENV_FILE.is_file(),
        "max_input_chars": settings.max_input_chars,
        "max_audio_mb": settings.max_audio_mb,
        "version": __version__,
    }


# --------------------------------------------------------------------------------------
# Transcription (one short segment at a time, while the clinician is still speaking)
# --------------------------------------------------------------------------------------

ALLOWED_AUDIO = {"audio/webm", "audio/mp4", "audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav",
                 "audio/m4a", "audio/x-m4a", "audio/ogg", "audio/aac", "video/webm", "application/octet-stream"}


@app.post("/api/scribe/transcribe", dependencies=[Depends(require_token)])
async def transcribe(audio: UploadFile = File(...), prompt: str = Form(default=""), segment: int = Form(default=0),
                     provider: Provider = Depends(get_provider)):
    ctype = (audio.content_type or "application/octet-stream").split(";")[0].strip().lower()
    if ctype not in ALLOWED_AUDIO and not ctype.startswith("audio/"):
        raise HTTPException(status_code=415, detail="Unsupported file type. Upload an audio recording.")
    limit = settings.max_audio_mb * 1024 * 1024
    data = await audio.read(limit + 1)
    if not data:
        raise HTTPException(status_code=400, detail="The audio segment is empty.")
    if len(data) > limit:
        raise HTTPException(status_code=413, detail=f"Audio is larger than {settings.max_audio_mb} MB.")
    t0 = time.perf_counter()
    text = await provider.transcribe(data, audio.filename or f"segment-{segment}.webm", ctype, prompt[-300:])
    return {"text": text, "segment": segment, "ms": round((time.perf_counter() - t0) * 1000)}


# --------------------------------------------------------------------------------------
# Draft: clinical analysis + SOAP, streamed
# --------------------------------------------------------------------------------------

def _sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n".encode()


async def _with_heartbeat(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[bytes]:
    """Forward pipeline events as SSE and send a comment every few seconds so proxies keep the line open."""
    queue: asyncio.Queue = asyncio.Queue()
    DONE = object()

    async def pump():
        try:
            async for ev in events:
                await queue.put(ev)
        finally:
            await queue.put(DONE)

    task = asyncio.create_task(pump())
    try:
        yield b": connected\n\n"
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield b": ping\n\n"
                continue
            if ev is DONE:
                break
            yield _sse(ev["event"], ev["data"])
    finally:
        # Client disconnected or stream finished: cancelling the pump runs the pipeline's own
        # cleanup, which stops the AI request so no further tokens are paid for.
        if not task.done():
            task.cancel()


@app.post("/api/scribe/draft", dependencies=[Depends(require_token)])
async def draft(req: DraftRequest, request: Request, stream: bool = True, provider: Provider = Depends(get_provider)):
    """Default: text/event-stream. Use ?stream=false (e.g. from Laravel) to get one JSON reply."""
    if not stream:
        final: dict[str, Any] | None = None
        async for ev in run_draft(req, provider):
            if ev["event"] == "error":
                raise HTTPException(status_code=502, detail=ev["data"]["message"])
            if ev["event"] == "final":
                final = ev["data"]
        return final
    return StreamingResponse(
        _with_heartbeat(run_draft(req, provider)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.post("/api/scribe/regenerate", dependencies=[Depends(require_token)])
async def regenerate(req: RegenerateRequest, provider: Provider = Depends(get_provider)):
    try:
        return await regenerate_section(req, provider)
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Rewriting the section took too long. Please retry.") from exc


@app.post("/api/scribe/check", dependencies=[Depends(require_token)])
async def check(req: CheckRequest):
    """Instant code-only re-check after the clinician edits the note. No AI call."""
    return check_draft(req.draft(), req.input, req.context.as_text())


@app.post("/api/scribe/export", dependencies=[Depends(require_token)])
async def export(req: ExportRequest):
    pdf = await asyncio.to_thread(build_pdf, req)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="lingualink360-soap-note.pdf"'})


if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run("app:app", host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8501")))
