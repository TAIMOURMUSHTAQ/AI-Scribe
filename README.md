# LinguaLink360 AI Scribe v4

The therapist types, pastes or dictates raw session notes (English, Urdu or Roman Urdu, shorthand is fine).
The service runs a **clinical analysis first**, then writes a **SOAP note where every line links to the
findings it came from**. Code checks the numbers and safety points, and the therapist edits and signs.

```
notes / dictation ──► code: exact percentages, cue levels, red flags          (instant)
                  ──► AI: clinical analysis ─► SOAP note with basis links     (streams onto the screen)
                  ──► code: invented numbers, new diagnoses, unverified quotes (instant)
                  ──► therapist edits, rewrites a section, signs, exports PDF
```

## What changed from v3, and why it is faster

| v3 problem | v4 fix |
|---|---|
| Synchronous OpenAI client inside async routes froze the whole server during each AI call | Async client throughout; one slow call never blocks others |
| Default 10-minute timeout + 2 retries → "loading forever" | 10 s connect, 40 s idle-stream and 150 s total limits, 1 retry, clear error messages |
| Audio uploaded only after Stop, so the wait grew with recording length | Dictation is cut at natural pauses every 20–40 s and transcribed **while you keep talking**; after Stop only the last few seconds remain |
| 4 separate AI round-trips with a click between each, transcript re-sent every time | **One** streamed AI call: analysis appears within seconds, the SOAP note follows |
| Spinner until an 18-field JSON finished | Partial results render live (streamed JSON parsed as it arrives) |
| Percentages and checks done by the AI | Code computes 14/20 = 70% exactly; safety checks run in code in milliseconds |
| Quality check was another AI call | Re-check after every edit is code-only and instant |
| Review boxes showed literal `\n` | Fixed (new editor) |

The system prompt is static, so OpenAI's automatic prompt caching makes repeat calls faster and cheaper.

## Run it (Windows PowerShell)

```powershell
cd lingualink360_ai_scribe_v4
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env          # paste your OPENAI_API_KEY
uvicorn app:app --host 127.0.0.1 --port 8501
```

Open http://127.0.0.1:8501. The microphone needs `localhost` or HTTPS, in Chrome or Edge.

**No key yet or showing it to someone?** Set `AI_PROVIDER=mock`. Everything works except the clinical
wording, which is rule-based, and nothing leaves the machine.

Tests (no key, no network): `python -m pytest -q` (49 tests, including the real OpenAI code path
run against a simulated OpenAI server).

## Deploy to Render (staging)

[render.yaml](render.yaml) is a Render Blueprint (Python web service, Singapore region, health check on `/health`).

1. Push this folder to a GitHub repo (`.env` is git-ignored and must stay out of git).
2. In Render: **New > Blueprint**, pick the repo, and fill in the secret values it asks for:
   - `OPENAI_API_KEY`: a separate staging key with a monthly spending cap
   - `BASIC_AUTH_USER` and `BASIC_AUTH_PASSWORD`: the sign-in the browser will ask for. A Render URL is public,
     so without these anyone who finds it can use your OpenAI credit.
3. Open `https://<service>.onrender.com`. HTTPS is automatic, so dictation works.

Staging is for made-up data only. See [SYSTEM_REVIEW.md](SYSTEM_REVIEW.md) for what is still needed before real use.

## Using it

1. **Patient context** (optional): discipline, age, diagnosis on file, goals, previous session note.
   These enable goal-progress and trend analysis. Patient ID, date and clinician name are **never sent to the AI**;
   they only go on the PDF.
2. **Session notes**: type, paste, upload audio or press **Dictate**. Dictated text appears in the
   box every 20–40 seconds. "Try a sample" loads three made-up cases.
3. **Analyse and draft note** (Ctrl + Enter). The middle column fills first:
   - numbers checked by code
   - red flags
   - **What may be missing**: gaps in the record (including how severe, how often and since when for
     every reported difficulty), areas not assessed or not documented, and questions for the patient or
     caregiver. Fixed checklist items are tagged "Checklist"; the rest come from the AI. For each one:
     **Add answer** (goes into your notes as a `Clarification (…)` line), **Plan for next session**, or
     **Not relevant** (stays dismissed when you draft again). After answering, press **Update note**.
   - progress against each goal, and the trend since the previous note
   - findings F1, F2 … colour-coded as reported, observed, measured, your plan or context, each with
     the exact quote from your notes and whether code found that quote
   - clinical reasoning, and contradictions
4. **SOAP note** (right): every line has chips (F1, F3…). Click one to jump to the finding; click
   "Show in notes" to highlight the source text. Edit any line directly, add or remove lines, or
   **Rewrite** one section with an optional instruction (e.g. "shorter", "parent-friendly").
   The Plan holds your own next steps plus **AI proposals**, each with its reason. **Accept** or
   **Reject** each proposal; only accepted ones go into the note, marked † in the TXT and PDF.
5. **Checks** banner: invented numbers, diagnoses, medication or referrals you did not write, plan items
   you did not state, red flags missing from the note, and instruction-like text in the input. Click an
   item to jump to the line.
6. **Sign note** (needs clinician name, and a decision on every AI proposal) → Copy, TXT or PDF.
   Signing locks the note; "Unlock to amend" reopens it.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/scribe/status` | Engine, models, limits (never the key) |
| POST | `/api/scribe/transcribe` | multipart `audio`, optional `prompt` (previous words), `segment` |
| POST | `/api/scribe/draft` | `{input, context}` → Server-Sent Events: `meta`, `facts`, `stage`, `partial`…, `final` or `error` |
| POST | `/api/scribe/draft?stream=false` | Same, one JSON reply (for Laravel queued jobs) |
| POST | `/api/scribe/regenerate` | Rewrite one section `{…, section, instruction}` |
| POST | `/api/scribe/check` | Code-only re-check of an edited note |
| POST | `/api/scribe/export` | PDF |

`context` = `{discipline, patient_age, diagnosis_on_file, goals, previous_note}` (all optional).
The `final` payload holds `draft` (analysis + SOAP statements with `basis` ids), `facts`, `checks`,
`timings`, `model`, `prompt_version` and `status: "ai_draft"`.

**Laravel:** set `SERVICE_TOKEN` and call from the server with header `X-Service-Token`. Follow the
project rule that the browser talks only to Laravel: Laravel checks permissions and consent, calls
`/draft?stream=false` from a queued job (or proxies the stream), and stores the result as an AI draft.

## Project layout

```
app.py                 routes, SSE streaming with heartbeat, token check
scribe/facts.py        code-based facts + all safety checks
scribe/completeness.py code checklist of documentation gaps ("how much / how often / since when")
scribe/prompts.py      versioned prompts (scribe_v4.0)
scribe/schema.py       strict JSON schema + request models
scribe/providers.py    OpenAI (async, timeouts, error mapping) and Mock
scribe/pipeline.py     facts → streamed AI → checks
scribe/partial_json.py renders half-finished JSON while streaming
scribe/pdf.py          in-memory PDF
static/                index.html, styles.css, app.js (no build step)
tests/                 49 tests
```

## Choosing a model

- `gpt-5.4-mini` + `OPENAI_REASONING_EFFORT=low`: default since scribe_v4.1. Better gaps and plan proposals;
  a made-up sample drafted in about 10–13 s.
- `gpt-4.1-mini` with `OPENAI_REASONING_EFFORT` empty: faster to first words, weaker clinical reasoning.
- Before switching models for real use, run the same 5–10 made-up cases on both and compare edits needed.

## Not production-ready yet

This is a standalone prototype. Before any real patient use it still needs: Laravel authentication and
clinic isolation, recorded consent, an approved AI data policy, audit records for each AI run, and
clinical-lead review of the prompt and of an evaluation set. Use **made-up data only** until then. The AI
drafts; the clinician decides and signs.

## To run the app everytime
cd C:\Projects\lingualink360_ai_scribe_v4
.\.venv\Scripts\Activate.ps1
uvicorn app:app --host 127.0.0.1 --port 8501