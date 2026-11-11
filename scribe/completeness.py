"""Deterministic documentation checklist: what a complete session note usually states.

Code, not AI, so these gaps are reliable and repeatable. The AI is told about them and adds
the gaps that need clinical judgement (untested areas, caregiver questions, considerations).

Each gap has a stable `key`, so a clinician can dismiss it ("not relevant") and it stays dismissed
when the note is drafted again.
"""
from __future__ import annotations

import re
from typing import Any

# A reported or observed difficulty ...
_DIFFICULTY = re.compile(
    r"\b(difficult\w*|struggl\w*|hard\s+(?:to|for)|trouble|problems?\s+(?:with|in|to|speaking|talking)|"
    r"unable\s+to|can'?t\s+(?:say|speak|talk|pronounce|express|understand|follow)|cannot\s+(?:say|speak|talk|express)|"
    r"stammer\w*|stutter\w*|block(?:s|ing)?|pauses|prolongations?|repetitions?|hesitat\w*|dysfluen\w*|disfluen\w*|"
    r"mushkil|dikkat|diqqat|pareshan\w*|atak\w*|haklah\w*|hakla\w*|"
    r"bol\s+nahi\s+(?:pa|sak)\w*|nahi\s+bol\s+(?:pa|sak)\w*)",
    re.I,
)
# "no secondary behaviour", "denies stammering", "no family history of stammering": not a complaint.
_NEGATED = re.compile(r"\b(no|not|never|without|denies|denied|nil|absent|negative|nahi|nahin|koi\s+nahi)\b[^.;,]{0,40}$", re.I)
_HISTORY_ONLY = re.compile(r"family\s+history|ghar\s+mein\s+kisi|khandan", re.I)

# Fluency (stammering / stuttering) assessments have their own essentials.
_FLUENCY = re.compile(r"\b(stammer\w*|stutter\w*|disfluen\w*|dysfluen\w*|blocks?|prolongations?|SSI\b|"
                      r"haklah\w*|hakla\w*|%\s*ss|syllables?\s+stuttered|fluency)", re.I)
_FLUENCY_FREQ = re.compile(r"(%\s*ss|syllables?\s+stuttered|\d+(?:\.\d+)?\s*%|per\s+(?:minute|100\s+(?:words|syllables)))", re.I)
_ARTIC_TASK = re.compile(r"\b(correct\w*|accura\w*|trials?|target\w*|word\s+level|phrase\s+level|sentence\s+level|sahi)\b", re.I)
# A sentence that stops mid-way ("child hesitates while").
_DANGLING = re.compile(r"\b(while|when|and|but|or|with|because|during|on|in|at|to|the|a|an|of|for|if|as|than|that|"
                       r"which|after|before|until|is|was|has|aur|jab|ke|k|mein|se|ko)\s*$", re.I)
# ... is quantified when the sentence says how much, how often or since when.
_SEVERITY = re.compile(r"\b(mild\w*|moderate\w*|severe\w*|significant\w*|slight\w*|profound\w*|marked\w*|"
                       r"very|extremely|somewhat|\d+\s*(?:/|out\s+of)\s*10|\d+\s*%|zyada|bohat|thora)\b", re.I)
_FREQUENCY = re.compile(r"\b(always|often|frequent\w*|sometimes|occasional\w*|rarely|daily|every\s+\w+|"
                        r"\d+\s*(?:times?|x)\b|times?\s+(?:a|per)\s+\w+|per\s+(?:minute|min|hour|day|week|session|sentence|100\s+words)|"
                        r"\d+(?:\.\d+)?\s*%\s*ss|"
                        r"roz|kabhi|aksar|hamesha|har\s+\w+)", re.I)
_ONSET = re.compile(r"\b(since|for\s+(?:the\s+)?(?:past|last)?\s*\w*\s*(?:days?|weeks?|months?|years?)|"
                    r"\d+\s*(?:days?|weeks?|months?|years?)\s+(?:ago|now)|started|began|onset|recently|"
                    r"se\s+\w*\s*(?:din|hafte|mahine|saal)|pichle)\b", re.I)

_REPORTED = re.compile(r"\b(mother|mom|mum|father|dad|parents?|caregiver|teacher|wife|husband|family|patient\s+(?:reports?|says|said|states?)|"
                       r"reports?|reported|said|says|states?|complain\w*|c/o|finds|feels|"
                       r"ammi|abbu|walida|walid|bataya|kaha|according)\b", re.I)
_CAREGIVER_REPORT = _REPORTED
_HOME = re.compile(r"\b(hep|home\s*(?:practice|programme|program|work|exercises?)|homework|ghar\s+pe|practi[cs]ed?\s+at\s+home|"
                   r"carry[-\s]?over\s+(?:at\s+)?home|(?:parent|home|caregiver)\s+carry[-\s]?over)\b", re.I)
# A plan is often written without the word "plan": "weekly 45-min sessions", "tx 1x/wk", "homework", "review in 3 months".
PLAN_LIKE = re.compile(r"\b(plan\w*|next\s+session|nxt|continue|f/u|follow[-\s]?up|agle\s+session|"
                       r"will\s+(?:target|work|continue|start|focus|review|begin|introduce)|weekly|fortnightly|"
                       r"\d+\s*x\s*/\s*(?:wk|week)|(?:sessions?|times?)\s+(?:per|a)\s+week|per\s+week|homework|hep|"
                       r"next\s+review|review\s+(?:on|in|after)|re-?assess\w*\s+(?:in|after)|start\w*\s+\w+\s+programme)\b", re.I)
_PLAN = PLAN_LIKE
_LEVEL = re.compile(r"\b(isolation|syllables?|words?|phrases?|sentences?|conversation\w*|connected\s+speech|"
                    r"structured|spontaneous|imitation|reading|narrative)\b", re.I)
_ANY_CUE = re.compile(r"\b(cue\w*|cuing|prompt\w*|independent\w*|model(?:l)?ing|imitation|gestur\w*|assist\w*|"
                      r"without\s+(?:help|support))", re.I)
_PERFORMANCE_WORDS = re.compile(r"\b(accura\w*|correct\w*|trials?|produced|production|attempts?|sahi)\b", re.I)

ASK_HOW_MUCH = ("How much of a problem is it: severity (mild / moderate / severe, or 0–10), how often it happens, "
                "since when, in which situations, and what effect it has on daily communication?")


# Answers added from the gap panel: "Clarification (<what it answers>): <answer>"
_CLARIFICATION = re.compile(r"^\s*Clarification\s*\((.*?)\)\s*:\s*(.*)$", re.I | re.M)


def split_clarifications(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Return the notes with each clarification line reduced to its answer, plus (label, answer) pairs.

    Labels are left out of the scanned text so words in a label ("next session") cannot satisfy a check.
    """
    pairs = [(m.group(1).strip(), m.group(2).strip()) for m in _CLARIFICATION.finditer(text or "")]
    return _CLARIFICATION.sub(lambda m: m.group(2), text or ""), pairs


def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", s.lower()).strip()


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+|;\s*", text or "")
    return [p.strip(" -•\t") for p in parts if len(p.strip(" -•\t")) > 3]


def _short(s: str, n: int = 90) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def unquantified_difficulties(text: str) -> list[dict[str, Any]]:
    """Sentences that describe a difficulty without saying how severe, how often or since when.

    A clarification answering that sentence ("Clarification (how much: <sentence>): moderate, daily")
    counts towards it.
    """
    body = _CLARIFICATION.sub("", text or "")
    _, pairs = split_clarifications(text)
    out = []
    for sent in _sentences(body):
        # Skip negated mentions ("no secondary behaviour", "no family history of stammering").
        hits = [h for h in _DIFFICULTY.finditer(sent) if not _NEGATED.search(sent[:h.start()])]
        if not hits or _HISTORY_ONLY.search(sent[max(0, hits[0].start() - 30):hits[0].end()]):
            continue
        key = _norm(sent)
        answers = " ".join(a for label, a in pairs
                           if label.lower().startswith("how much") and _norm(label.split(":", 1)[-1])[:40] in key)
        scope = f"{sent} {answers}"
        missing = [label for label, rx in (("severity", _SEVERITY), ("frequency", _FREQUENCY), ("since when", _ONSET))
                   if not rx.search(scope)]
        # A measurement, or a count with a rate ("5 blocks per minute"), quantifies it.
        if re.search(r"\d+\s*(?:/|out\s+of|of)\s*\d+", scope) or (re.search(r"\d", scope) and "frequency" not in missing):
            missing = [m for m in missing if m != "severity"]
        if len(missing) >= 2:
            out.append({"sentence": sent, "missing": missing, "reported": bool(_REPORTED.search(sent))})
    return out


def checklist_gaps(session_input: str, ctx: dict[str, str], facts: dict[str, Any],
                   dismissed: list[str] | None = None) -> list[dict[str, Any]]:
    """Documentation gaps a code checklist can detect reliably."""
    dismissed_set = {d.strip().lower() for d in dismissed or []}
    raw_text = session_input or ""
    text, _ = split_clarifications(raw_text)
    discipline = (ctx.get("discipline") or "").lower()
    is_slp = not discipline or "speech" in discipline or "language" in discipline
    has_measure = bool(facts.get("measurements")) or bool(facts.get("percentages_stated"))
    gaps: list[dict[str, Any]] = []
    is_fluency = any(not _NEGATED.search(text[max(0, m.start() - 60):m.start()]) for m in _FLUENCY.finditer(text))

    def add(key: str, kind: str, priority: str, item: str, why: str, ask: str, label: str = "") -> None:
        if key.lower() in dismissed_set:
            return
        gaps.append({"key": key, "kind": kind, "priority": priority, "item": item, "why": why, "ask": ask,
                     "basis": [], "origin": "checklist", "label": label})

    quantify = unquantified_difficulties(raw_text)
    if is_fluency:
        # Observed disfluencies are quantified by %SS (flagged below); one "how much" for the complaint is enough.
        quantify = [d for d in quantify if d["reported"]][:1]
    for d in quantify[:4]:
        who = "Reported difficulty" if d["reported"] else "Difficulty"
        add(f"quantify:{d['sentence'][:60].lower()}", "documentation", "high",
            f'{who} not quantified: "{_short(d["sentence"])}" (missing {", ".join(d["missing"])})',
            "Without severity and frequency there is no baseline, so progress or worsening cannot be shown in later notes.",
            ASK_HOW_MUCH, label=f"how much: {_short(d['sentence'], 70)}")

    # Cue level belongs to practice tasks with targets, not to stuttering measures or standardised tests.
    if has_measure and not facts.get("cue_levels") and not _ANY_CUE.search(text) and (
            not is_fluency or _ARTIC_TASK.search(text)):
        add("cue_level", "documentation", "medium", "Cue or prompt level not stated for the measured tasks",
            "Accuracy means little without the support given; cue level is needed to show cue fading.",
            "What level of cueing was given (independent / minimal / moderate / maximal), and what type (verbal, visual, tactile)?")

    if not has_measure and _PERFORMANCE_WORDS.search(text):
        add("trial_counts", "documentation", "medium", "Performance described without numbers",
            "Trial counts and accuracy are needed to compare sessions and judge goals.",
            "How many trials, and how many were correct?")

    if is_slp and has_measure and not _LEVEL.search(text):
        add("task_level", "documentation", "medium", "Task level not stated (isolation, word, phrase, sentence, conversation)",
            "The same accuracy means different things at word and conversation level.",
            "At what level were the targets practised?")

    if ctx.get("goals", "").strip() and not has_measure:
        add("goal_data", "documentation", "high", "Goals are on file but no measured data was recorded today",
            "Goal progress cannot be judged without data from this session.",
            "Which goal targets were probed today, and with what accuracy?")

    if is_fluency:
        if not _ONSET.search(text):
            add("onset", "documentation", "high", "Time since onset of the stammering not stated",
                "In young children, time since onset is one of the main factors in deciding between monitoring and starting treatment.",
                "When did the stammering start, and has it changed since (better, worse, coming and going)?")
        if not _FLUENCY_FREQ.search(text):
            add("fluency_frequency", "documentation", "medium", "Stammering frequency not measured (% syllables stuttered)",
                "A %SS from a speech sample is the usual baseline for judging change over time.",
                "What was the % syllables stuttered, from how long a sample, and in which speaking situation?")

    tail = _sentences(raw_text.strip())[-1:] if raw_text.strip() else []
    if tail and not re.search(r"[.!?)\"'”]\s*$", raw_text.strip()) and _DANGLING.search(tail[0]):
        add("unfinished", "documentation", "high", f'Entry looks unfinished: "…{_short(tail[0][-60:], 60)}"',
            "The AI will not guess how the sentence ends, so this information is left out of the note.",
            "How does this sentence end?")

    if not facts.get("durations_min"):
        add("duration", "documentation", "low", "Session length not stated",
            "Usually required for the record and billing.", "How long was the session?")

    if not _CAREGIVER_REPORT.search(text):
        add("report", "documentation", "low", "No patient or caregiver report recorded",
            "The Subjective section has nothing to draw on.",
            "What did the patient or caregiver report since the last session (home practice, changes, concerns)?")

    if not _HOME.search(text):
        add("home_programme", "documentation", "low", "Home practice not mentioned",
            "Carry-over depends on practice between sessions.",
            "Was home practice reviewed or given? How often was it done?")

    if not _PLAN.search(text):
        add("plan", "documentation", "medium", "No plan for the next session stated",
            "The Plan will contain only AI proposals unless you add your own.",
            "What will you target next session, and will anything change?")

    for n, g in enumerate(gaps, 1):
        g["id"] = f"C{n}"
    return gaps


def checklist_for_prompt(gaps: list[dict[str, Any]]) -> str:
    if not gaps:
        return "- None."
    return "\n".join(f'- {g["id"]}: {g["item"]}' for g in gaps)


# What an AI gap looks like when it repeats a checklist gap (matched on the AI gap's item + ask).
_SAME_AS = {
    "quantify": r"quantif|severity|how severe|how often|frequency|since when|onset",
    "cue_level": r"\bcue|\bprompt",
    "trial_counts": r"trials?|accuracy|how many",
    "task_level": r"\blevel\b",
    "goal_data": r"measured|accuracy|no data|trials?|score",
    "duration": r"session (?:length|duration)|how long",
    "report": r"caregiver report|patient report|subjective",
    "home_programme": r"home\s*(?:practice|programme|program|work)|homework|\bhep\b",
    "plan": r"\bplan\b|next session",
    "onset": r"onset|since when|time since|when (?:did )?(?:it|the \w+) (?:start|begin)|started",
    "fluency_frequency": r"%\s*ss|syllables stuttered|(?:stutter|stammer)\w* frequency|frequency of (?:stutter|stammer|disfluen|dysfluen)",
    "unfinished": r"incomplete|unfinished|cut off|truncated|ends? mid|trailing",
}
# These checklist gaps are complete on their own, so an AI repeat adds nothing even when labelled "assessment".
_PURE = {"onset", "fluency_frequency", "unfinished"}
_STOP = {"the", "and", "with", "that", "this", "from", "have", "were", "was", "his", "her", "their", "not", "for",
         "reported", "reports", "child", "patient", "documented", "stated"}


def _content(s: str) -> set[str]:
    return {w for w in _norm(s).split() if len(w) > 3 and w not in _STOP}


def drop_checklist_duplicates(ai_gaps: list[dict[str, Any]], checklist: list[dict[str, Any]],
                              dismissed: list[str] | None = None) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Remove AI documentation/question gaps that repeat a checklist gap (shown or dismissed).

    The AI is told not to repeat them, but models sometimes do. Assessment gaps are kept: they add
    clinical judgement even when they touch the same topic. A "how much" gap only counts as a repeat
    when it is about the same sentence. Returns the kept gaps and {dropped AI id: checklist id or ""},
    so references to a dropped gap can be pointed at the checklist gap instead.
    """
    targets: list[tuple[str, re.Pattern, set[str] | None, bool]] = []
    for g in checklist:
        kind = g["key"].split(":", 1)[0]
        if kind in _SAME_AS:
            sent = g["key"].split(":", 1)[1] if kind == "quantify" else None
            targets.append((g["id"], re.compile(_SAME_AS[kind], re.I), _content(sent) if sent else None, kind in _PURE))
    for d in dismissed or []:
        kind = d.split(":", 1)[0]
        if kind in _SAME_AS and kind != "quantify":
            targets.append(("", re.compile(_SAME_AS[kind], re.I), None, kind in _PURE))
    kept, dropped = [], {}
    for g in ai_gaps:
        text = f'{g.get("item", "")} {g.get("ask", "")}'
        match = None
        for cid, rx, words, pure in targets:
            if g.get("kind") not in ("documentation", "question") and not pure:
                continue
            if not rx.search(text):
                continue
            if words is not None and len(words & _content(text)) < max(1, len(words) // 3):
                continue          # a "how much" gap about a different complaint
            match = cid
            break
        if match is None:
            kept.append(g)
        else:
            dropped[str(g.get("id"))] = match
    return kept, dropped
