"""AI providers. OpenAI for real use; Mock for demos/tests with no key and no data leaving the machine."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, AsyncIterator

import httpx

from .config import Settings
from .facts import extract_facts

log = logging.getLogger("scribe.provider")


class ProviderError(Exception):
    def __init__(self, code: str, message: str, status: int = 502):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class Provider:
    name = "base"
    model = ""

    async def stream_json(self, system: str, user: str, schema: dict, schema_name: str,
                          max_tokens: int = 6000) -> AsyncIterator[str]:
        raise NotImplementedError
        yield ""  # pragma: no cover

    async def complete_json(self, system: str, user: str, schema: dict, schema_name: str,
                            max_tokens: int = 2000) -> str:
        parts = [d async for d in self.stream_json(system, user, schema, schema_name, max_tokens)]
        return "".join(parts)

    async def transcribe(self, data: bytes, filename: str, content_type: str, prompt: str) -> str:
        raise NotImplementedError


# --------------------------------------------------------------------------------------
# OpenAI
# --------------------------------------------------------------------------------------

TRANSCRIBE_HINT = (
    "Speech-language therapy session dictation by a clinician in Pakistan. Speakers may switch between "
    "English and Urdu mid-sentence; transcribe each word in the language spoken and keep English "
    "clinical terms in English. Preserve numbers exactly (e.g. 14 out of 20, 70 percent), phoneme "
    "targets (/s/, /r/, s-blends), word positions (initial, medial, final), cue levels (minimal, "
    "moderate, maximal cues), and terms such as articulation, phonology, fluency, stuttering, voice, "
    "receptive, expressive, MLU, AAC, PECS, oral motor, feeding, swallowing. Transcribe verbatim."
)


def _map_openai_error(exc: Exception, model: str) -> ProviderError:
    import openai

    if isinstance(exc, openai.AuthenticationError):
        return ProviderError("auth", "OpenAI rejected the API key. Check OPENAI_API_KEY in .env.", 503)
    if isinstance(exc, openai.PermissionDeniedError):
        return ProviderError("permission", f"Your OpenAI project does not have access to '{model}'.", 503)
    if isinstance(exc, openai.NotFoundError):
        return ProviderError("model_not_found", f"Model '{model}' was not found for this API key. Change it in .env.", 503)
    if isinstance(exc, openai.RateLimitError):
        msg = str(exc)
        if "quota" in msg.lower():
            return ProviderError("quota", "The OpenAI account is out of credit (quota exceeded).", 503)
        return ProviderError("rate_limit", "OpenAI rate limit reached. Wait a few seconds and try again.", 429)
    if isinstance(exc, openai.APITimeoutError):
        return ProviderError("timeout", "The AI provider stopped responding. Please try again.", 504)
    if isinstance(exc, openai.APIConnectionError):
        return ProviderError("connection", "Could not reach OpenAI. Check the internet connection.", 502)
    if isinstance(exc, openai.BadRequestError):
        return ProviderError("bad_request", f"OpenAI rejected the request for model '{model}'. "
                             "It may not support structured outputs; try gpt-4.1-mini or a gpt-5 model.", 502)
    if isinstance(exc, openai.APIStatusError):
        return ProviderError("provider_error", f"OpenAI returned an error ({exc.status_code}). Please retry.", 502)
    return ProviderError("provider_error", "The AI request failed unexpectedly. Please retry.", 502)


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, cfg: Settings, http_client: httpx.AsyncClient | None = None):
        from openai import AsyncOpenAI

        self.cfg = cfg
        self.model = cfg.clinical_model
        self.transcription_model = cfg.transcription_model
        timeout = httpx.Timeout(connect=cfg.connect_timeout, read=cfg.stream_idle_timeout, write=30.0, pool=10.0)
        kwargs: dict[str, Any] = {"api_key": cfg.openai_api_key or "missing", "timeout": timeout,
                                  "max_retries": cfg.max_retries}
        if cfg.openai_base_url:
            kwargs["base_url"] = cfg.openai_base_url
        if http_client is not None:
            kwargs["http_client"] = http_client
        self.client = AsyncOpenAI(**kwargs)

    def _params(self, max_tokens: int) -> dict[str, Any]:
        p: dict[str, Any] = {"max_completion_tokens": max_tokens}
        if self.cfg.is_reasoning_model:
            if self.cfg.reasoning_effort:
                p["reasoning_effort"] = self.cfg.reasoning_effort
        else:
            p["temperature"] = 0.2
        return p

    async def stream_json(self, system, user, schema, schema_name, max_tokens=6000):
        import openai

        if not self.cfg.openai_api_key:
            raise ProviderError("no_key", "OPENAI_API_KEY is not set. Add it to .env and restart.", 503)
        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_schema",
                                 "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
                stream=True,
                **self._params(max_tokens),
            )
        except openai.OpenAIError as exc:
            raise _map_openai_error(exc, self.model) from exc
        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if getattr(delta, "refusal", None):
                    raise ProviderError("refusal", "The model declined to draft this note. Rephrase and retry.", 422)
                if delta.content:
                    yield delta.content
                if choice.finish_reason == "length":
                    raise ProviderError("too_long", "The draft hit the length limit. Shorten the input and retry.", 422)
        except openai.OpenAIError as exc:
            raise _map_openai_error(exc, self.model) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("timeout", "The AI provider stream was interrupted. Please retry.", 504) from exc
        finally:
            close = getattr(stream, "close", None)
            if close:
                try:
                    await close()
                except Exception:  # pragma: no cover
                    pass

    async def transcribe(self, data, filename, content_type, prompt):
        import openai

        if not self.cfg.openai_api_key:
            raise ProviderError("no_key", "OPENAI_API_KEY is not set. Add it to .env and restart.", 503)
        full_prompt = TRANSCRIBE_HINT + (f"\nPrevious words: {prompt[-200:]}" if prompt else "")
        try:
            result = await asyncio.wait_for(
                self.client.audio.transcriptions.create(
                    model=self.transcription_model, file=(filename, data, content_type), prompt=full_prompt),
                timeout=self.cfg.transcribe_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise ProviderError("timeout", "Transcription took too long. Retrying this segment may help.", 504) from exc
        except openai.OpenAIError as exc:
            raise _map_openai_error(exc, self.transcription_model) from exc
        text = result if isinstance(result, str) else getattr(result, "text", "")
        return (text or "").strip()


# --------------------------------------------------------------------------------------
# Mock: rule-based, deterministic, streams like a real model.
# --------------------------------------------------------------------------------------

_REPORTED = re.compile(r"\b(mother|mom|mum|father|dad|parent|caregiver|teacher|ammi|abbu|walida|walid|"
                       r"reports?|reported|said|says|states?|bataya|kaha|according)\b", re.I)
_PLAN = re.compile(r"\b(plan|next\s+session|nxt|continue|will\s+\w+|hep|home\s+(?:practice|programme|program)|"
                   r"homework|f/u|follow[-\s]?up|target\s+next|agle\s+session)\b", re.I)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+|;\s*", text)
    return [p.strip(" -•\t") for p in parts if len(p.strip(" -•\t")) > 3]


def build_mock_draft(session_input: str, ctx: dict[str, str]) -> dict[str, Any]:
    facts = extract_facts(session_input)
    findings: list[dict[str, Any]] = []
    for sent in _sentences(session_input)[:14]:
        if _PLAN.search(sent):
            source = "clinician_plan"
        elif _REPORTED.search(sent):
            source = "reported"
        elif re.search(r"\d+\s*(?:/|out of|of)\s*\d+|\d+\s*%", sent, re.I):
            source = "measured"
        else:
            source = "observed"
        fid = f"F{len(findings) + 1}"
        findings.append({"id": fid, "domain": "general", "statement": sent.rstrip(".") + ".",
                         "source": source, "evidence": sent[:140], "significance": "Documented by clinician."})

    def ids(*sources: str) -> list[str]:
        return [f["id"] for f in findings if f["source"] in sources]

    def section(*sources: str, empty: str = "Not documented in session input.") -> list[dict[str, Any]]:
        rows = [{"text": f["statement"], "basis": [f["id"]]} for f in findings if f["source"] in sources]
        return rows or [{"text": empty, "basis": []}]

    measured = ids("measured")
    summary_bits = [f'{m["raw"]} = {m["percent_text"]}' for m in facts["measurements"]]
    goals = [g.strip(" -•") for g in (ctx.get("goals") or "").splitlines() if g.strip(" -•")]
    return {
        "input_language": "english",
        "analysis": {
            "summary": "Mock analysis (AI_PROVIDER=mock): sentences were sorted by rule, not by a clinical model."
                       + (f" Verified measurements: {', '.join(summary_bits)}." if summary_bits else ""),
            "session_focus": findings[0]["statement"] if findings else "Not documented.",
            "findings": findings,
            "goal_progress": [{"goal": g, "status": "unclear", "basis": measured[:2],
                               "note": "Mock mode cannot judge progress."} for g in goals[:4]],
            "trend_vs_previous": {"direction": "not_comparable" if not ctx.get("previous_note") else "mixed",
                                  "explanation": "Mock mode does not compare sessions."},
            "clinical_reasoning": [{"point": "Performance data were recorded for this session.",
                                    "basis": measured[:3], "limitation": "Rule-based mock output."}] if measured else [],
            "risk_flags": [{"flag": f["category"].replace("_", " "), "severity": f["severity"],
                            "evidence": f["match"], "recommended_action": f["guidance"]} for f in facts["red_flags"]],
            "missing_information": [] if facts["cue_levels"] else ["Cue level not stated."],
            "contradictions": [],
        },
        "soap": {
            "subjective": section("reported"),
            "objective": section("observed", "measured"),
            "assessment": [{"text": "Mock assessment: review the objective findings against the goals.",
                            "basis": measured[:3]}] if measured else [{"text": "Not documented in session input.", "basis": []}],
            "plan": section("clinician_plan", empty="Plan not documented in session input."),
        },
        "suggestions": [{"suggestion": "State the cue level for each target.", "rationale": "Supports cue-fading decisions.",
                         "basis": []}] if not facts["cue_levels"] else [],
        "follow_up_questions": ["What did the caregiver report about home practice?"] if not ids("reported") else [],
    }


class MockProvider(Provider):
    name = "mock"
    model = "mock-clinical-v4"

    def __init__(self, delay: float = 0.012, chunk: int = 28):
        self.delay, self.chunk = delay, chunk

    async def stream_json(self, system, user, schema, schema_name, max_tokens=6000):
        m = re.search(r"<session_input>\n(.*?)\n</session_input>", user, re.S)
        session_input = m.group(1) if m else user
        ctx_m = re.search(r"Treatment goals:\s*(.*?)(?:\n[A-Z][a-z ]+:|\n</patient_context>)", user, re.S)
        prev = "Previous session note" in user
        ctx = {"goals": ctx_m.group(1) if ctx_m else "", "previous_note": "yes" if prev else ""}
        if schema_name == "soap_section":
            sec = re.search(r"Section to rewrite: (\w+)", user)
            draft = build_mock_draft(session_input, ctx)
            payload: dict[str, Any] = {"statements": draft["soap"][sec.group(1) if sec else "assessment"]}
        else:
            payload = build_mock_draft(session_input, ctx)
        text = json.dumps(payload, ensure_ascii=False)
        await asyncio.sleep(0.25)  # simulated time-to-first-token
        for i in range(0, len(text), self.chunk):
            yield text[i:i + self.chunk]
            await asyncio.sleep(self.delay)

    async def transcribe(self, data, filename, content_type, prompt):
        await asyncio.sleep(0.4)
        return f"[mock transcript of {len(data) // 1024} KB audio segment]"


def make_provider(cfg: Settings) -> Provider:
    if cfg.provider == "mock":
        return MockProvider()
    return OpenAIProvider(cfg)
