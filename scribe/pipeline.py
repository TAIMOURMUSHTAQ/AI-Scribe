"""The one-pass pipeline: code facts -> streamed clinical analysis + SOAP -> code checks.

Yields small event dicts; app.py turns them into Server-Sent Events (or one JSON reply).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any, AsyncIterator

from .config import settings
from .completeness import checklist_for_prompt, checklist_gaps, drop_checklist_duplicates
from .facts import check_draft, extract_facts, facts_for_prompt
from .partial_json import parse_partial
from .prompts import (PROMPT_VERSION, REGENERATE_SYSTEM_PROMPT, SYSTEM_PROMPT, build_draft_message,
                      build_regenerate_message)
from .providers import Provider, ProviderError
from .schema import (DRAFT_SCHEMA, PLAN_ACTIONS, REGENERATE_PLAN_SCHEMA, REGENERATE_SCHEMA, SECTIONS, DraftRequest,
                     RegenerateRequest)

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


PLAN_EMPTY = "Plan not documented in session input."
# "Plan not documented in session input." and the model's variations ("No next session plan was documented ...").
_PLACEHOLDER = re.compile(r"not\s+(?:been\s+)?documented|^\s*no\b.{0,40}\b(?:plan|documented|stated)\b", re.I)


def _plan_list(value: Any) -> list[dict[str, Any]]:
    """Plan items keep their origin. AI proposals start as 'pending' until the clinician decides."""
    items = value if isinstance(value, list) else [value] if value else []
    out = []
    for item in items:
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict) or not str(item.get("text", "")).strip():
            continue
        origin = "ai_proposed" if item.get("origin") == "ai_proposed" else "clinician"
        action = item.get("action") if item.get("action") in PLAN_ACTIONS else "other"
        row = {"text": str(item["text"]).strip(),
               "basis": [str(b) for b in item.get("basis") or [] if str(b).strip()],
               "origin": origin, "action": action, "rationale": str(item.get("rationale") or "").strip()}
        if origin == "ai_proposed":
            row["decision"] = "accepted" if item.get("decision") == "accepted" else "pending"
        out.append(row)
    # A "not documented" placeholder is noise once there are AI proposals to decide on.
    real = [r for r in out if not (r["origin"] == "clinician" and _PLACEHOLDER.search(r["text"]))]
    if real:
        return real
    return out or [{"text": PLAN_EMPTY, "basis": [], "origin": "clinician", "rationale": ""}]


def _enforce_plan_rules(draft: dict[str, Any]) -> None:
    """Code backstops for what the prompt asks but a model may not always do.

    1. A "clinician" plan item must rest on something the clinician stated as a plan. If its basis
       cites only other findings, it was the AI's idea: it becomes a proposal awaiting a decision.
    2. At most one proposal about documenting/asking for information (the gaps panel holds the rest),
       as long as at least two other proposals remain.
    """
    sources = {str(f.get("id")): f.get("source") for f in draft["analysis"].get("findings") or []}
    plan = draft["soap"]["plan"]
    for item in plan:
        cited = [sources[b] for b in item["basis"] if b in sources]
        if item["origin"] == "clinician" and cited and "clinician_plan" not in cited:
            item.update(origin="ai_proposed", decision="pending",
                        rationale=item["rationale"] or "You did not write this as a plan; proposed from your findings.")
    proposals = [p for p in plan if p["origin"] == "ai_proposed"]
    docs = [p for p in proposals if p["action"] == "document"]
    if len(docs) > 1 and len(proposals) - len(docs) >= 2:
        extra = {id(p) for p in docs[1:]}
        draft["soap"]["plan"] = [p for p in plan if id(p) not in extra]


def _gap_list(value: Any) -> list[dict[str, Any]]:
    out = []
    for n, g in enumerate(value if isinstance(value, list) else [], 1):
        if isinstance(g, str):
            g = {"item": g}
        if not isinstance(g, dict) or not str(g.get("item", "")).strip():
            continue
        out.append({
            "id": str(g.get("id") or f"G{n}").strip(),
            "kind": g.get("kind") if g.get("kind") in ("documentation", "assessment", "question") else "documentation",
            "priority": g.get("priority") if g.get("priority") in ("high", "medium", "low") else "medium",
            "item": str(g["item"]).strip(), "why": str(g.get("why") or "").strip(),
            "ask": str(g.get("ask") or "").strip(),
            "basis": [str(b) for b in g.get("basis") or [] if str(b).strip()], "origin": "ai",
        })
    return out


def normalize_draft(raw: dict[str, Any]) -> dict[str, Any]:
    analysis = raw.get("analysis") or {}
    for key in ("findings", "goal_progress", "clinical_reasoning", "risk_flags", "contradictions"):
        if not isinstance(analysis.get(key), list):
            analysis[key] = []
    # Older replies (scribe_v4.0) had a plain missing_information list.
    legacy = analysis.pop("missing_information", None)
    analysis["gaps"] = _gap_list(analysis.get("gaps") or legacy or [])
    analysis.setdefault("summary", "")
    analysis.setdefault("session_focus", "")
    if not isinstance(analysis.get("trend_vs_previous"), dict):
        analysis["trend_vs_previous"] = {"direction": "not_comparable", "explanation": ""}
    soap = raw.get("soap") or {}
    draft = {
        "input_language": raw.get("input_language", "english"),
        "analysis": analysis,
        "soap": {s: (_plan_list(soap.get(s)) if s == "plan" else _statement_list(soap.get(s))) for s in SECTIONS},
    }
    _enforce_plan_rules(draft)
    return draft


def _remap_basis(draft: dict[str, Any], dropped: dict[str, str], kept_gap_ids: set[str]) -> None:
    """Point references to removed AI gaps at the checklist gap they repeated; drop ones that no longer exist."""
    finding_ids = {str(f.get("id")) for f in draft["analysis"].get("findings") or []}

    def fix(ids: list[str]) -> list[str]:
        out = []
        for b in ids:
            b = dropped.get(b, b)
            if b and b not in out and (b in finding_ids or b in kept_gap_ids or re.fullmatch(r"C\d+", b)):
                out.append(b)
        return out

    for items in draft["soap"].values():
        for item in items:
            item["basis"] = fix(item.get("basis") or [])
    for key in ("goal_progress", "clinical_reasoning"):
        for item in draft["analysis"].get(key) or []:
            if isinstance(item, dict):
                item["basis"] = fix(item.get("basis") or [])


def build_facts(session_input: str, ctx: dict[str, str], context_text: str,
                dismissed: list[str] | None = None) -> dict[str, Any]:
    """Code facts plus the code documentation checklist."""
    facts = extract_facts(session_input, context_text)
    facts["checklist_gaps"] = checklist_gaps(session_input, ctx, facts, dismissed)
    return facts


def provider_is_reasoning(provider: Provider) -> bool:
    cfg = getattr(provider, "cfg", None)
    return bool(cfg and cfg.is_reasoning_model)


def draft_max_tokens(provider: Provider) -> int:
    # Reasoning models spend part of the budget thinking before they write.
    return 16000 if provider_is_reasoning(provider) else 8000


def _stage(stage: str, status: str, **extra: Any) -> dict[str, Any]:
    return {"event": "stage", "data": {"stage": stage, "status": status, **extra}}


async def run_draft(req: DraftRequest, provider: Provider) -> AsyncIterator[dict[str, Any]]:
    run_id = uuid.uuid4().hex[:12]
    t0 = time.perf_counter()
    ctx = req.context.model_dump()
    context_text = req.context.as_text()
    timings: dict[str, float] = {}

    facts = build_facts(req.input, ctx, context_text, req.dismissed_gaps)
    timings["facts_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    yield {"event": "meta", "data": {"run_id": run_id, "model": provider.model, "provider": provider.name,
                                     "prompt_version": PROMPT_VERSION}}
    yield {"event": "facts", "data": facts}
    yield _stage("facts", "done", ms=timings["facts_ms"])
    yield _stage("analysis", "active")

    user_msg = build_draft_message(req.input, ctx, facts_for_prompt(facts),
                                   checklist_for_prompt(facts["checklist_gaps"]), req.dismissed_gaps)
    buf: list[str] = []
    last_emit = 0.0
    soap_started = False
    log.info("run=%s start provider=%s model=%s chars=%d", run_id, provider.name, provider.model, len(req.input))

    async def consume() -> None:
        nonlocal last_emit, soap_started
        async for delta in provider.stream_json(SYSTEM_PROMPT, user_msg, DRAFT_SCHEMA, "clinical_draft",
                                                draft_max_tokens(provider)):
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
    draft["analysis"]["gaps"], dropped = drop_checklist_duplicates(draft["analysis"]["gaps"], facts["checklist_gaps"],
                                                                   req.dismissed_gaps)
    _remap_basis(draft, dropped, {str(g.get("id")) for g in draft["analysis"]["gaps"]})
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
    facts = build_facts(req.input, ctx, context_text, req.dismissed_gaps)
    draft = req.draft()
    analysis = draft.get("analysis") or {}
    # Findings plus goal progress, trend and gaps, so a rewritten section stays consistent with them.
    basis_json = {k: analysis.get(k) for k in ("findings", "goal_progress", "trend_vs_previous", "gaps", "risk_flags")
                  if analysis.get(k)}
    basis_json["checklist_gaps"] = [{"id": g["id"], "item": g["item"]} for g in facts["checklist_gaps"]]
    msg = build_regenerate_message(
        req.input, ctx, facts_for_prompt(facts),
        json.dumps(basis_json, ensure_ascii=False),
        json.dumps(draft.get("soap") or {}, ensure_ascii=False),
        req.section, req.instruction,
    )
    async with ai_slots():
        text = await asyncio.wait_for(
            provider.complete_json(REGENERATE_SYSTEM_PROMPT, msg,
                                   REGENERATE_PLAN_SCHEMA if req.section == "plan" else REGENERATE_SCHEMA,
                                   "soap_section", 4000 if provider_is_reasoning(provider) else 1500),
            timeout=settings.pipeline_timeout,
        )
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError("invalid_output", "The AI reply was incomplete. Please retry.") from exc
    statements = _plan_list(raw.get("statements")) if req.section == "plan" else _statement_list(raw.get("statements"))
    draft.setdefault("soap", {})[req.section] = statements
    checks = check_draft(draft, req.input, context_text, facts)
    return {"section": req.section, "statements": statements, "checks": checks}
