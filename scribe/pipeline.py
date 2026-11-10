"""The one-pass pipeline: code facts -> streamed clinical analysis + SOAP -> code checks.

Yields small event dicts; app.py turns them into Server-Sent Events (or one JSON reply).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, AsyncIterator

from .config import settings
from .facts import check_draft, extract_facts, facts_for_prompt
from .partial_json import parse_partial
from .prompts import (PROMPT_VERSION, REGENERATE_SYSTEM_PROMPT, SYSTEM_PROMPT, build_draft_message,
                      build_regenerate_message)
from .providers import Provider, ProviderError
from .schema import DRAFT_SCHEMA, REGENERATE_SCHEMA, SECTIONS, DraftRequest, RegenerateRequest

log = logging.getLogger("scribe.pipeline")
PARTIAL_INTERVAL = 0.12  # seconds between on-screen updates while streaming

_ai_slots: asyncio.Semaphore | None = None


def ai_slots() -> asyncio.Semaphore:
    global _ai_slots
    if _ai_slots is None:
        _ai_slots = asyncio.Semaphore(settings.max_concurrent_ai)
    return _ai_slots


def _statement_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        value = [value]
    out = []
    for item in value or []:
        if isinstance(item, str):
            item = {"text": item, "basis": []}
        if isinstance(item, dict) and str(item.get("text", "")).strip():
            out.append({"text": str(item["text"]).strip(),
                        "basis": [str(b) for b in item.get("basis") or [] if str(b).strip()]})
    return out or [{"text": "Not documented in session input.", "basis": []}]


def normalize_draft(raw: dict[str, Any]) -> dict[str, Any]:
    analysis = raw.get("analysis") or {}
    for key in ("findings", "goal_progress", "clinical_reasoning", "risk_flags", "missing_information", "contradictions"):
        if not isinstance(analysis.get(key), list):
            analysis[key] = []
    analysis.setdefault("summary", "")
    analysis.setdefault("session_focus", "")
    if not isinstance(analysis.get("trend_vs_previous"), dict):
        analysis["trend_vs_previous"] = {"direction": "not_comparable", "explanation": ""}
    soap = raw.get("soap") or {}
    return {
        "input_language": raw.get("input_language", "english"),
        "analysis": analysis,
        "soap": {s: _statement_list(soap.get(s)) for s in SECTIONS},
        "suggestions": [s for s in raw.get("suggestions") or [] if isinstance(s, dict)][:6],
        "follow_up_questions": [str(q) for q in raw.get("follow_up_questions") or []][:6],
    }


def _stage(stage: str, status: str, **extra: Any) -> dict[str, Any]:
    return {"event": "stage", "data": {"stage": stage, "status": status, **extra}}


async def run_draft(req: DraftRequest, provider: Provider) -> AsyncIterator[dict[str, Any]]:
    run_id = uuid.uuid4().hex[:12]
    t0 = time.perf_counter()
    ctx = req.context.model_dump()
    context_text = req.context.as_text()
    timings: dict[str, float] = {}

    facts = extract_facts(req.input, context_text)
    timings["facts_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    yield {"event": "meta", "data": {"run_id": run_id, "model": provider.model, "provider": provider.name,
                                     "prompt_version": PROMPT_VERSION}}
    yield {"event": "facts", "data": facts}
    yield _stage("facts", "done", ms=timings["facts_ms"])
    yield _stage("analysis", "active")

    user_msg = build_draft_message(req.input, ctx, facts_for_prompt(facts))
    buf: list[str] = []
    last_emit = 0.0
    soap_started = False
    log.info("run=%s start provider=%s model=%s chars=%d", run_id, provider.name, provider.model, len(req.input))

    async def consume() -> None:
        nonlocal last_emit, soap_started
        async for delta in provider.stream_json(SYSTEM_PROMPT, user_msg, DRAFT_SCHEMA, "clinical_draft"):
            if "first_token_ms" not in timings:
                timings["first_token_ms"] = round((time.perf_counter() - t0) * 1000)
            buf.append(delta)
            now = time.perf_counter()
            if now - last_emit >= PARTIAL_INTERVAL:
                last_emit = now
                partial = parse_partial("".join(buf))
                if isinstance(partial, dict):
                    await queue.put({"event": "partial", "data": partial})
                    if not soap_started and "soap" in partial:
                        soap_started = True
                        timings["analysis_ms"] = round((now - t0) * 1000)
                        await queue.put(_stage("analysis", "done", ms=timings["analysis_ms"]))
                        await queue.put(_stage("soap", "active"))

    queue: asyncio.Queue = asyncio.Queue()
    task: asyncio.Task | None = None
    try:
        async with ai_slots():
            task = asyncio.create_task(consume())
            deadline = t0 + settings.pipeline_timeout
            while True:
                if task.done() and queue.empty():
                    break
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    task.cancel()
                    raise ProviderError("timeout", f"The draft took longer than {int(settings.pipeline_timeout)} s and was stopped.", 504)
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=min(remaining, 0.5))
                    yield item
                except asyncio.TimeoutError:
                    continue
            task.result()  # re-raise provider errors
    except ProviderError as exc:
        log.warning("run=%s provider_error code=%s", run_id, exc.code)
        yield {"event": "error", "data": {"code": exc.code, "message": exc.message}}
        return
    except Exception:  # pragma: no cover
        log.exception("run=%s unexpected failure", run_id)
        yield {"event": "error", "data": {"code": "internal", "message": "Unexpected server error. Please retry."}}
        return
    finally:
        # Client closed the tab / pressed Stop: stop paying for tokens immediately.
        if task is not None and not task.done():
            log.info("run=%s cancelled", run_id)
            task.cancel()

    text = "".join(buf)
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        log.warning("run=%s invalid JSON from model; len=%d", run_id, len(text))
        raw = parse_partial(text)
        if not isinstance(raw, dict) or "soap" not in raw:
            yield {"event": "error", "data": {"code": "invalid_output",
                                              "message": "The AI reply was incomplete. Please press Generate again."}}
            return

    draft = normalize_draft(raw)
    if not soap_started:
        timings["analysis_ms"] = round((time.perf_counter() - t0) * 1000)
        yield _stage("analysis", "done", ms=timings["analysis_ms"])
    timings["generation_ms"] = round((time.perf_counter() - t0) * 1000)
    yield _stage("soap", "done", ms=timings["generation_ms"])

    t_check = time.perf_counter()
    checks = check_draft(draft, req.input, context_text, facts)
    timings["checks_ms"] = round((time.perf_counter() - t_check) * 1000, 1)
    timings["total_ms"] = round((time.perf_counter() - t0) * 1000)
    yield _stage("checks", "done", ms=timings["checks_ms"])
    log.info("run=%s done total_ms=%s findings=%d issues=%d", run_id, timings["total_ms"],
             len(draft["analysis"]["findings"]), len(checks["issues"]))
    yield {"event": "final", "data": {"run_id": run_id, "draft": draft, "facts": facts, "checks": checks,
                                      "timings": timings, "model": provider.model,
                                      "prompt_version": PROMPT_VERSION, "status": "ai_draft"}}


async def regenerate_section(req: RegenerateRequest, provider: Provider) -> dict[str, Any]:
    ctx = req.context.model_dump()
    context_text = req.context.as_text()
    facts = extract_facts(req.input, context_text)
    draft = req.draft()
    findings = (draft.get("analysis") or {}).get("findings") or []
    msg = build_regenerate_message(
        req.input, ctx, facts_for_prompt(facts),
        json.dumps(findings, ensure_ascii=False),
        json.dumps(draft.get("soap") or {}, ensure_ascii=False),
        req.section, req.instruction,
    )
    async with ai_slots():
        text = await asyncio.wait_for(
            provider.complete_json(REGENERATE_SYSTEM_PROMPT, msg, REGENERATE_SCHEMA, "soap_section", 1500),
            timeout=settings.pipeline_timeout,
        )
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError("invalid_output", "The AI reply was incomplete. Please retry.") from exc
    statements = _statement_list(raw.get("statements"))
    draft.setdefault("soap", {})[req.section] = statements
    checks = check_draft(draft, req.input, context_text, facts)
    return {"section": req.section, "statements": statements, "checks": checks}
