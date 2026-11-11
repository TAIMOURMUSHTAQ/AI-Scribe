"""Request models and the strict JSON schema the model must follow (OpenAI Structured Outputs)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .config import settings

SECTIONS = ("subjective", "objective", "assessment", "plan")


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


_STR = {"type": "string"}
_STR_LIST = {"type": "array", "items": _STR}

STATEMENT = _obj({"text": _STR, "basis": _STR_LIST})
STATEMENTS = {"type": "array", "items": STATEMENT}
# Plan items say who they came from: the clinician's own words, or an AI proposal awaiting a decision.
PLAN_ACTIONS = ["assess", "treat", "caregiver", "safety", "document", "other"]
PLAN_STATEMENT = _obj({"text": _STR, "basis": _STR_LIST,
                       "origin": {"type": "string", "enum": ["clinician", "ai_proposed"]},
                       "action": {"type": "string", "enum": PLAN_ACTIONS},
                       "rationale": _STR})
PLAN_STATEMENTS = {"type": "array", "items": PLAN_STATEMENT}
GAP = _obj({
    "id": _STR,
    "kind": {"type": "string", "enum": ["documentation", "assessment", "question"]},
    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
    "item": _STR,
    "why": _STR,
    "ask": _STR,
    "basis": _STR_LIST,
})

DRAFT_SCHEMA: dict[str, Any] = _obj({
    "input_language": {"type": "string", "enum": ["english", "urdu", "roman_urdu", "mixed", "other"]},
    "analysis": _obj({
        "summary": _STR,
        "session_focus": _STR,
        "findings": {"type": "array", "items": _obj({
            "id": _STR,
            "domain": _STR,
            "statement": _STR,
            "source": {"type": "string", "enum": ["reported", "observed", "measured", "clinician_plan", "context"]},
            "evidence": _STR,
            "significance": _STR,
        })},
        "goal_progress": {"type": "array", "items": _obj({
            "goal": _STR,
            "status": {"type": "string", "enum": ["met", "progressing", "maintained", "regressed", "baseline",
                                                   "not_addressed", "unclear"]},
            "basis": _STR_LIST,
            "note": _STR,
        })},
        "trend_vs_previous": _obj({
            "direction": {"type": "string", "enum": ["improved", "maintained", "declined", "mixed", "not_comparable"]},
            "explanation": _STR,
        }),
        "clinical_reasoning": {"type": "array", "items": _obj({"point": _STR, "basis": _STR_LIST, "limitation": _STR})},
        "risk_flags": {"type": "array", "items": _obj({
            "flag": _STR,
            "severity": {"type": "string", "enum": ["high", "medium", "low"]},
            "evidence": _STR,
            "recommended_action": _STR,
        })},
        "gaps": {"type": "array", "items": GAP},
        "contradictions": _STR_LIST,
    }),
    "soap": _obj({"subjective": STATEMENTS, "objective": STATEMENTS, "assessment": STATEMENTS,
                  "plan": PLAN_STATEMENTS}),
    # Objective assessment guide: what to measure to answer the reported concern. Never results.
    "objective_suggestions": {"type": "array", "items": _obj({
        "id": _STR,
        "priority": {"type": "string", "enum": ["high", "medium", "low"]},
        "measure": _STR,
        "method": _STR,
        "why": _STR,
        "record_as": _STR,
        "basis": _STR_LIST,
    })},
})

REGENERATE_SCHEMA: dict[str, Any] = _obj({"statements": STATEMENTS})
REGENERATE_PLAN_SCHEMA: dict[str, Any] = _obj({"statements": PLAN_STATEMENTS})


class PatientContext(BaseModel):
    discipline: str = Field(default="", max_length=60)
    patient_age: str = Field(default="", max_length=40)
    diagnosis_on_file: str = Field(default="", max_length=400)
    goals: str = Field(default="", max_length=4000)
    previous_note: str = Field(default="", max_length=8000)

    def as_text(self) -> str:
        # The age is included so "3-year-old" in the note is not flagged as an invented number.
        age = f"Age: {self.patient_age}" if self.patient_age else ""
        return "\n".join(v for v in (age, self.diagnosis_on_file, self.goals, self.previous_note) if v)


class DraftRequest(BaseModel):
    input: str = Field(min_length=1, max_length=settings.max_input_chars)
    context: PatientContext = Field(default_factory=PatientContext)
    # Gaps the clinician marked "not relevant": checklist keys or the AI gap's wording. Not raised again.
    dismissed_gaps: list[str] = Field(default_factory=list, max_length=60)

    @field_validator("dismissed_gaps")
    @classmethod
    def trim_dismissed(cls, v: list[str]) -> list[str]:
        return [x.strip()[:300] for x in v if x and x.strip()]

    @field_validator("input")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Session input is empty.")
        return v.strip()


class Statement(BaseModel):
    text: str = Field(default="", max_length=3000)
    basis: list[str] = Field(default_factory=list)
    edited: bool = False
    # Plan only. An AI-proposed item counts as part of the note only once decision == "accepted".
    origin: Literal["clinician", "ai_proposed"] = "clinician"
    rationale: str = Field(default="", max_length=1000)
    action: str = Field(default="", max_length=20)
    decision: Literal["", "pending", "accepted"] = ""

    @property
    def pending(self) -> bool:
        return self.origin == "ai_proposed" and self.decision != "accepted"


class CheckRequest(DraftRequest):
    analysis: dict[str, Any] = Field(default_factory=dict)
    objective_suggestions: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
    soap: dict[str, list[Statement]] = Field(default_factory=dict)

    def draft(self) -> dict[str, Any]:
        return {"analysis": self.analysis, "objective_suggestions": self.objective_suggestions,
                "soap": {k: [s.model_dump() for s in v] for k, v in self.soap.items()}}


class RegenerateRequest(CheckRequest):
    section: Literal["subjective", "objective", "assessment", "plan"]
    instruction: str = Field(default="", max_length=500)


class ExportRequest(BaseModel):
    soap: dict[str, list[Statement]]
    header: dict[str, str] = Field(default_factory=dict)
    summary: str = Field(default="", max_length=4000)
    signed_by: str = Field(default="", max_length=120)
    signed_at: str = Field(default="", max_length=60)
