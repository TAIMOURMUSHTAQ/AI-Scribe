"""Deterministic (code, not AI) clinical fact extraction and safety checks.

Everything here is instant, free and repeatable:
  * measurements such as 14/20, "14 out of 20", "20 mein se 14" become exact percentages
  * cue levels, red flags and prompt-injection attempts are detected by pattern
  * after the AI writes the note, every number, evidence quote and clinical term is
    checked against what the clinician actually wrote
The AI is told to use the verified numbers; code double-checks that it did.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

# --------------------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------------------

_PUNCT = re.compile(r"[^\w\s/%.؀-ۿ]+", re.UNICODE)
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = (text or "").lower().replace("’", "'").replace("“", '"').replace("”", '"')
    text = _PUNCT.sub(" ", text)
    text = text.replace(" .", " ").rstrip(".")
    return _WS.sub(" ", text).strip()


def _fmt_pct(value: float) -> str:
    return f"{value:.0f}" if abs(value - round(value)) < 1e-9 else f"{value:.1f}"


# --------------------------------------------------------------------------------------
# Measurements and other facts
# --------------------------------------------------------------------------------------

# "14/20" but not dates like 10/11/2026 and not phoneme slashes like /s/
_FRACTION = re.compile(r"(?<![\d/])(\d{1,3})\s*/\s*(\d{1,3})(?![\d/])")
_OUT_OF = re.compile(r"\b(\d{1,3})\s*(?:out\s+of|of|outta)\s*(\d{1,3})\b", re.I)
# Roman Urdu: "20 mein se 14" = 14 out of 20
_URDU_OUT_OF = re.compile(r"\b(\d{1,3})\s*(?:mein|main|mai|men|me)\s+(?:se|sy|say)\s+(\d{1,3})\b", re.I)
_PERCENT = re.compile(r"\b(\d{1,3}(?:\.\d+)?)\s*(?:%|percent\b|per\s+cent\b|fisad\b|feesad\b)", re.I)
# "45 min", "45-min", "45 minutes" (but not "7/10 min cues")
_DURATION = re.compile(r"(?<![/\d])\b(\d{1,3})\s*-?\s*(?:min(?:ute)?s?|mins?)\b", re.I)
_NUMBER = re.compile(r"(?<![\w/])(\d+(?:\.\d+)?)(?![\w])")

_CUE = re.compile(
    r"\b(independent(?:ly)?|without\s+(?:any\s+)?(?:cues?|prompts?|help)|w/o\s+cues?|"
    r"min(?:imal)?|mod(?:erate)?|max(?:imal)?)\b"
    r"(?:\s+(?:verbal|visual|tactile|phonemic|gestural|model(?:ing|ling)?|semantic|written))?"
    r"(?:\s+(?:and|/|&)\s+(?:verbal|visual|tactile|phonemic|gestural|model(?:ing|ling)?))?"
    r"\s*(cues?|cueing|cuing|prompts?|prompting|assist(?:ance)?|support|help)?",
    re.I,
)


@dataclass
class Measurement:
    raw: str
    correct: int
    total: int
    percent: float
    label: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "correct": self.correct,
            "total": self.total,
            "percent": round(self.percent, 1),
            "percent_text": f"{_fmt_pct(self.percent)}%",
            "label": self.label,
        }


def _label_before(text: str, start: int) -> str:
    """A few words before a measurement, within the same clause, as a human label."""
    window = text[max(0, start - 70):start]
    window = re.split(r"[.;\n]|,\s(?=[A-Za-z])", window)[-1]
    words = window.strip(" :-=(").split()
    while words and not words[-1].strip(",:;-=(").strip():
        words.pop()
    return " ".join(words[-6:]).rstrip(",:;-=( ")


def extract_measurements(text: str) -> list[Measurement]:
    found: list[Measurement] = []
    taken: list[tuple[int, int]] = []

    def add(m: re.Match, correct: int, total: int) -> None:
        if total <= 0 or correct > total or total > 500:
            return
        if any(not (m.end() <= a or m.start() >= b) for a, b in taken):
            return
        taken.append((m.start(), m.end()))
        found.append(Measurement(m.group(0).strip(), correct, total, 100.0 * correct / total,
                                 _label_before(text, m.start())))

    for m in _URDU_OUT_OF.finditer(text):
        add(m, int(m.group(2)), int(m.group(1)))
    for m in _OUT_OF.finditer(text):
        add(m, int(m.group(1)), int(m.group(2)))
    for m in _FRACTION.finditer(text):
        add(m, int(m.group(1)), int(m.group(2)))
    found.sort(key=lambda x: text.find(x.raw))
    return found


def _canonical_cue(word: str) -> str:
    w = word.lower()
    if w.startswith(("independent", "without", "w/o")):
        return "independent"
    if w.startswith("min"):
        return "minimal"
    if w.startswith("mod"):
        return "moderate"
    if w.startswith("max"):
        return "maximal"
    return w


def extract_cue_levels(text: str) -> list[str]:
    levels: list[str] = []
    for m in _CUE.finditer(text):
        word, noun = m.group(1), m.group(2)
        is_cue = bool(noun) or word.lower().startswith(("independent", "without", "w/o"))
        if word.lower() in {"min", "mins", "mod", "max"} and not noun:
            is_cue = False  # "30 min" is a duration, not a cue level
        if is_cue:
            c = _canonical_cue(word)
            if c not in levels:
                levels.append(c)
    return levels


# --------------------------------------------------------------------------------------
# Red flags (English + common Roman Urdu) and prompt injection
# --------------------------------------------------------------------------------------

RED_FLAGS: list[tuple[str, str, str, re.Pattern]] = [
    ("swallowing_safety", "high", "Possible swallowing safety concern: consider screening per clinic protocol.",
     re.compile(r"\b(chok(?:e|ed|es|ing)|aspirat\w*|wet\s+voice|voice\s+(?:sounded|was|became|is)\s+wet|gurgl\w*|food\s+(?:got\s+)?stuck|"
                r"cough(?:s|ed|ing)?\s+(?:during|while|when|after|with)\s+(?:meals?|eating|drinking|liquids?|"
                r"water|food|feeds?|swallow\w*)|khana\s+\w*\s*(?:atak|phans)\w*|nigal\w*\s+mein\s+(?:mushkil|dikkat))",
                re.I)),
    ("regression", "medium", "Reported loss of previously acquired skills: confirm and monitor.",
     re.compile(r"\b(regress\w*|lost\s+(?:words?|skills?|speech)|loss\s+of\s+(?:words?|skills?|speech)|"
                r"stopped\s+(?:talking|speaking|using\s+words)|no\s+longer\s+(?:says|talks|speaks|uses)|"
                r"bolna\s+(?:band|chor)\w*)", re.I)),
    ("neurological_or_medical", "high", "Possible medical/neurological event reported: medical follow-up may be needed.",
     re.compile(r"\b(seizures?|convuls\w*|unconscious|fainted|head\s+injur\w*|sudden\s+(?:hearing\s+loss|"
                r"weakness|onset)|facial\s+droop|slurred\s+speech\s+(?:started|suddenly)|ear\s+discharge|"
                r"bleeding\s+from\s+(?:the\s+)?ear|daura|دورہ)", re.I)),
    ("safeguarding", "high", "Possible safeguarding concern: follow clinic safeguarding procedure.",
     re.compile(r"\b(abus\w*|neglect\w*|unexplained\s+bruis\w*|hit\s+by\s+(?:his|her|their)\s+\w+|"
                r"beaten|unsafe\s+at\s+home)", re.I)),
    ("self_harm", "high", "Self-injury or self-harm mentioned: assess risk per clinic protocol.",
     re.compile(r"\b(self[-\s]?harm\w*|self[-\s]?injur\w*|hurt(?:s|ing)?\s+(?:him|her|them)sel(?:f|ves)|"
                r"head[-\s]?bang\w*|suicid\w*|bit(?:es|ing)?\s+(?:his|her|their)\s+(?:own\s+)?(?:hand|arm)s?|"
                r"(?:hit|hits|hitting|bang|bangs|banging|slap|slaps|slapping|punch\w*|scratch\w*)\s+(?:him|her|them)sel(?:f|ves)|"
                r"(?:hit|hits|hitting|bang|bangs|banging|slap|slaps|slapping|punch\w*)\s+(?:his|her|their)\s+(?:own\s+)?(?:head|face)|"
                r"(?:apna|apne)\s+(?:sir|mun[h]?)\s+\w*\s*(?:maar|pat)\w*|sir\s+(?:deewar|zameen)\s+\w*\s*(?:maar|pat)\w*)",
                re.I)),
    ("pain_or_discomfort", "medium", "Pain or discomfort reported: note location and whether it limited participation.",
     re.compile(r"\b(pain(?:ful)?|hurts?|ache|dard)\b", re.I)),
    ("hearing_change", "medium", "Possible hearing change reported: consider audiology review.",
     re.compile(r"\b(not\s+responding\s+to\s+(?:his|her|their)?\s*name|can'?t\s+hear|cannot\s+hear|"
                r"ear\s+infection|ear\s+pain|kaan\s+(?:mein\s+)?dard|sun(?:ta|ti)\s+nahi)", re.I)),
]

_INJECTION = re.compile(
    r"(ignore\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions|rules)|"
    r"disregard\s+(?:the\s+)?(?:previous|above|system)|you\s+are\s+now\s+|system\s+prompt|"
    r"new\s+instructions\s*:|act\s+as\s+(?:a|an)\s+|reveal\s+(?:your|the)\s+(?:prompt|instructions))",
    re.I,
)


def _snippet(text: str, start: int, end: int, pad: int = 45) -> str:
    s, e = max(0, start - pad), min(len(text), end + pad)
    return ("…" if s > 0 else "") + text[s:e].strip().replace("\n", " ") + ("…" if e < len(text) else "")


def detect_red_flags(text: str) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    for category, severity, guidance, pattern in RED_FLAGS:
        m = pattern.search(text)
        if m:
            flags.append({
                "category": category,
                "severity": severity,
                "match": m.group(0),
                "snippet": _snippet(text, m.start(), m.end()),
                "guidance": guidance,
            })
    return flags


def detect_injection(text: str) -> list[str]:
    return [_snippet(text, m.start(), m.end(), 25) for m in _INJECTION.finditer(text)]


_UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
          "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_EN_WORD_NUM = {w: i for i, w in enumerate(_UNITS)} | _TENS | {"hundred": 100, "half": 0.5}
# Roman Urdu numbers, only used to widen what the clinician wrote (never to read the English note).
_UR_WORD_NUM = {"ek": 1, "teen": 3, "chaar": 4, "char": 4, "paanch": 5, "panch": 5, "chay": 6, "chhe": 6, "saat": 7,
                "aath": 8, "nau": 9, "das": 10, "gyarah": 11, "barah": 12, "pandrah": 15, "bees": 20, "tees": 30,
                "chalees": 40, "pachaas": 50, "saath": 60, "sattar": 70, "assi": 80, "nabbe": 90, "sau": 100}
_WORD_NUM_RE = re.compile(r"\b(" + "|".join(sorted(_TENS, key=len, reverse=True)) + r")[-\s]+(" +
                          "|".join(_UNITS[1:10]) + r")\b|\b([a-z]+)\b", re.I)


def _word_numbers(text: str, urdu: bool) -> set[str]:
    out: set[str] = set()
    for m in _WORD_NUM_RE.finditer(text or ""):
        if m.group(1):
            out.add(str(_TENS[m.group(1).lower()] + _UNITS.index(m.group(2).lower())))
            continue
        w = m.group(3).lower()
        v = _EN_WORD_NUM.get(w, _UR_WORD_NUM.get(w) if urdu else None)
        if v is not None and w not in ("one", "half"):  # "one of the", "half the time" are not data
            out.add(_fmt_pct(float(v)))
    return out


def numbers_in(text: str, words: bool = False, urdu: bool = False) -> set[str]:
    """Numbers written as digits, and with words=True also as words ("four-year-old" -> 4)."""
    out: set[str] = set()
    for m in _NUMBER.finditer(text or ""):
        n = m.group(1)
        out.add(n)
        if "." in n:
            out.add(n.rstrip("0").rstrip("."))
    if words:
        out |= _word_numbers(text, urdu)
    return out


def extract_facts(session_input: str, context_text: str = "") -> dict[str, Any]:
    measurements = extract_measurements(session_input)
    context_measurements = extract_measurements(context_text) if context_text else []
    return {
        "measurements": [m.as_dict() for m in measurements],
        "context_measurements": [m.as_dict() for m in context_measurements],
        "percentages_stated": sorted({m.group(1) for m in _PERCENT.finditer(session_input)}, key=float),
        "durations_min": sorted({int(m.group(1)) for m in _DURATION.finditer(session_input)}),
        "cue_levels": extract_cue_levels(session_input),
        "red_flags": detect_red_flags(session_input),
        "injection_attempts": detect_injection(session_input),
        "word_count": len(session_input.split()),
    }


def facts_for_prompt(facts: dict[str, Any]) -> str:
    lines: list[str] = []
    for m in facts["measurements"]:
        label = f' ({m["label"]})' if m["label"] else ""
        lines.append(f'- "{m["raw"]}"{label} = {m["correct"]} of {m["total"]} = {m["percent_text"]}')
    for m in facts.get("context_measurements", []):
        label = f' ({m["label"]})' if m["label"] else ""
        lines.append(f'- [from context] "{m["raw"]}"{label} = {m["percent_text"]}')
    if facts["percentages_stated"]:
        lines.append("- Percentages written by the clinician: " + ", ".join(p + "%" for p in facts["percentages_stated"]))
    if facts["cue_levels"]:
        lines.append("- Cue/prompt levels mentioned: " + ", ".join(facts["cue_levels"]))
    if facts["durations_min"]:
        lines.append("- Durations mentioned (minutes): " + ", ".join(map(str, facts["durations_min"])))
    if facts["red_flags"]:
        lines.append("- Pattern-detected possible red flags (confirm against the text; do not exaggerate): " +
                     "; ".join(f'{f["category"]} ({f["severity"]}): "{f["match"]}"' for f in facts["red_flags"]))
    return "\n".join(lines) or "- No numeric measurements were detected by code."


# --------------------------------------------------------------------------------------
# Post-generation checks
# --------------------------------------------------------------------------------------

CLINICAL_TERMS = [
    # diagnoses / labels
    "apraxia", "dyspraxia", "dysarthria", "aphasia", "autism", "asd", "adhd", "dyslexia", "dysphagia",
    "stuttering", "stammering", "fluency disorder", "developmental stammering", "developmental stuttering",
    "persistent stammering", "persistent stuttering", "cluttering", "cerebral palsy", "down syndrome", "developmental delay",
    "language disorder", "speech sound disorder", "phonological disorder", "articulation disorder",
    "intellectual disability", "hearing loss", "auditory processing disorder", "selective mutism",
    "voice disorder", "vocal nodules", "global developmental delay", "learning disability",
    # medication / medical
    "medication", "risperidone", "methylphenidate", "melatonin", "antibiotic", "botox", "dose", "mg",
    # decisions that the clinician must make
    "referral", "refer to", "referred", "discharge", "discharged", "diagnosis", "diagnosed",
]

NOT_DOCUMENTED = "not documented"

# The same condition in other words: the AI may use any of them if the clinician used one.
TERM_SYNONYMS = {
    "stuttering": r"stutter\w*|stammer\w*|haklah\w*|hakla\w*|disfluen\w*|dysfluen\w*",
    "stammering": r"stutter\w*|stammer\w*|haklah\w*|hakla\w*|disfluen\w*|dysfluen\w*",
    "dysphagia": r"dysphagia|swallowing\s+(?:disorder|difficult\w*)",
    "asd": r"\basd\b|autism",
    "autism": r"\basd\b|autism",
}


def verify_quote(quote: str, source_norm: str, source_tokens: set[str]) -> str:
    """'verified' if the quote appears in the source, 'approximate' if most words do, else 'unverified'."""
    q = (quote or "").strip()
    if not q:
        return "missing"
    parts = [normalize(p) for p in re.split(r"\.\.\.|…", q) if normalize(p)]
    if parts and all(p in source_norm for p in parts):
        return "verified"
    tokens = [t for t in normalize(q).split() if len(t) > 1]
    if not tokens:
        return "unverified"
    stems = {t[:5] for t in source_tokens if len(t) >= 5}
    overlap = sum(1 for t in tokens if t in source_tokens or (len(t) >= 5 and t[:5] in stems)) / len(tokens)
    return "approximate" if overlap >= 0.75 else "unverified"


def _iter_soap(soap: dict[str, Any]) -> Iterable[tuple[str, int, dict[str, Any]]]:
    for section in ("subjective", "objective", "assessment", "plan"):
        items = soap.get(section) or []
        if isinstance(items, str):
            items = [{"text": items, "basis": []}]
        for i, item in enumerate(items):
            if isinstance(item, str):
                item = {"text": item, "basis": []}
            yield section, i, item


def check_draft(draft: dict[str, Any], session_input: str, context_text: str,
                facts: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run code checks on a draft (AI output, possibly edited by the clinician)."""
    facts = facts or extract_facts(session_input, context_text)
    source = f"{session_input}\n{context_text}"
    source_norm = normalize(source)
    source_tokens = set(source_norm.split())
    analysis = draft.get("analysis") or {}
    soap = draft.get("soap") or {}
    issues: list[dict[str, Any]] = []

    # 1. Evidence quotes behind each finding must exist in what the clinician wrote.
    evidence_status: dict[str, str] = {}
    findings = analysis.get("findings") or []
    finding_ids = set()
    for f in findings:
        fid = str(f.get("id", "")).strip()
        finding_ids.add(fid)
        status = verify_quote(f.get("evidence", ""), source_norm, source_tokens)
        evidence_status[fid] = status
        if status == "unverified":
            issues.append({"severity": "medium", "section": "analysis", "ref": fid,
                           "type": "evidence_not_found",
                           "message": f"Finding {fid}: its evidence quote could not be found in your input. Check it before relying on it."})

    # Plan items and gap actions may cite gap ids (AI G#, checklist C#) as well as findings.
    known_ids = finding_ids | {str(g.get("id")) for g in analysis.get("gaps") or [] if isinstance(g, dict)}
    known_ids |= {str(g.get("id")) for g in facts.get("checklist_gaps") or []}

    # 2. Numbers in the note must come from the input, the context or code-computed percentages.
    allowed = numbers_in(source, words=True, urdu=True)
    for m in facts.get("measurements", []) + facts.get("context_measurements", []):
        pct = m["percent"]
        allowed |= {_fmt_pct(pct), str(round(pct)), f"{pct:.1f}", str(m["correct"]), str(m["total"])}
    allowed |= {"0", "1"}

    lowered_source = source.lower()
    for section, i, item in _iter_soap(soap):
        text = str(item.get("text", ""))
        low = text.lower()
        proposed = section == "plan" and item.get("origin") == "ai_proposed"
        if proposed and item.get("decision") != "accepted":
            issues.append({"severity": "medium", "section": "plan", "ref": f"plan:{i}", "type": "plan_pending",
                           "message": "AI-proposed plan item waiting for your decision: accept or reject it."})
        bad = sorted({n for n in numbers_in(text, words=True) if n not in allowed}, key=float)
        if bad:
            issues.append({"severity": "high", "section": section, "ref": f"{section}:{i}", "type": "unsupported_number",
                           "message": f"Number(s) {', '.join(bad)} do not appear in your input or verified facts."})
        for term in CLINICAL_TERMS if not proposed else ():  # proposals are labelled and need the clinician's decision
            in_source = re.search(rf"\b(?:{TERM_SYNONYMS.get(term, re.escape(term))})\b", lowered_source)
            if re.search(rf"\b{re.escape(term)}\b", low) and not in_source:
                if term.startswith("diagnos") and re.search(r"\bdiagnos\w*\s+(?:on\s+file|per\s+record)", low):
                    continue
                issues.append({"severity": "high", "section": section, "ref": f"{section}:{i}", "type": "unsupported_term",
                               "message": f'"{term}" is not in your input or the patient context. AI must not add diagnoses, medication or referral decisions.'})
        basis = [str(b) for b in (item.get("basis") or [])]
        unknown = [b for b in basis if b not in known_ids]
        if unknown and findings:
            issues.append({"severity": "low", "section": section, "ref": f"{section}:{i}", "type": "unknown_basis",
                           "message": f"Cites unknown finding(s) {', '.join(unknown)}."})
        if not basis and NOT_DOCUMENTED not in low and not item.get("edited"):
            issues.append({"severity": "low", "section": section, "ref": f"{section}:{i}", "type": "no_basis",
                           "message": "This statement has no linked clinical basis."})
        if section == "plan" and not proposed and basis and findings and NOT_DOCUMENTED not in low:
            sources = {str(f.get("source")) for f in findings if str(f.get("id")) in basis}
            if sources and "clinician_plan" not in sources:
                issues.append({"severity": "medium", "section": "plan", "ref": f"plan:{i}", "type": "plan_not_stated",
                               "message": "This plan item is not something you stated as a plan. Accept it only if you agree."})

    # 3. High-severity red flags spotted by code should be reflected somewhere in the note.
    note_text = " ".join(str(item.get("text", "")) for _, _, item in _iter_soap(soap)
                         if not (item.get("origin") == "ai_proposed" and item.get("decision") != "accepted")).lower()
    for flag in facts.get("red_flags", []):
        if flag["severity"] == "high" and flag["match"].lower().split()[0][:4] not in note_text:
            issues.append({"severity": "high", "section": "general", "ref": flag["category"], "type": "red_flag_missing",
                           "message": f'Your input mentions "{flag["match"]}" ({flag["category"].replace("_", " ")}), but the note does not. {flag["guidance"]}'})

    for snippet in facts.get("injection_attempts", []):
        issues.append({"severity": "medium", "section": "general", "ref": "input", "type": "instruction_in_input",
                       "message": f"Instruction-like text was found in the input and treated as plain data: {snippet}"})

    counts = {s: sum(1 for x in issues if x["severity"] == s) for s in ("high", "medium", "low")}
    verified = sum(1 for v in evidence_status.values() if v == "verified")
    return {
        "issues": issues,
        "counts": counts,
        "evidence_status": evidence_status,
        "evidence_verified": verified,
        "evidence_total": len(evidence_status),
        "status": "attention" if counts["high"] else ("review" if counts["medium"] else "clear"),
    }
