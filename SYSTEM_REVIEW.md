# LinguaLink360 AI Scribe v4: How It Works, and Known Gaps

Review date: 10 November 2026. This covers the code as it stands in this folder. The main
[README.md](README.md) covers setup and the API.

## How the system works

The service is a FastAPI server ([app.py](app.py)) with a plain HTML/JS page ([static/](static/)) and no build step.
When the therapist presses **Analyse and draft note**, it runs in three stages: code first, then AI, then code again.

```
Browser                         Server
───────                         ──────
notes + patient context ──►  1. facts.py      code pulls out exact numbers, cue levels, red flags  (~1 ms)
                             2. pipeline.py   ONE streamed OpenAI call → analysis, then SOAP note
   ◄── live partial JSON ───                  (partial_json.py renders it as it arrives)
                             3. facts.py      code checks the AI's output against the original notes
   ◄── final draft + checks
therapist edits ──────────►  /check            code-only re-check after each edit (no AI, instant)
"Rewrite" a section ──────►  /regenerate       one small AI call for that section only
Sign → PDF ───────────────►  /export           PDF built in memory (pdf.py)
```

### 1. Code facts ([scribe/facts.py](scribe/facts.py))

Before any AI call, code finds:

- measurements such as `14/20`, `14 out of 20` and the Roman Urdu `20 mein se 14`, with the exact percentage worked out
- cue levels (minimal, moderate, maximal, independent)
- session durations
- red flags: swallowing safety, regression, seizures or other medical events, safeguarding, self-harm, pain, hearing change
- text that looks like an attempt to give the AI instructions ("ignore previous instructions…")

These are passed into the prompt as `<verified_facts>`, so the AI never does its own arithmetic on clinical numbers.

### 2. One AI call ([scribe/prompts.py](scribe/prompts.py), [scribe/providers.py](scribe/providers.py), [scribe/pipeline.py](scribe/pipeline.py))

The model must return JSON in a fixed format ([scribe/schema.py](scribe/schema.py)), using OpenAI structured outputs. The reply has two parts:

- **Clinical analysis.** Each finding gets an ID (F1, F2…), a source type (reported, observed, measured, clinician's plan or context), a word-for-word quote from the notes, a domain and its clinical significance. The analysis also covers progress against each goal, the trend since the previous note, clinical reasoning, risk flags, missing information and contradictions.
- **SOAP note.** Every line lists the finding IDs it is based on. The Plan section may only contain next steps the clinician actually stated. The AI's own ideas go in a separate "suggestions" list.

The reply streams onto the screen as it arrives. Time limits (10 s to connect, 40 s with no new data, 150 s in total) stop it from loading forever.
If the tab is closed or Stop is pressed, the AI request is cancelled so no more tokens are paid for.

### 3. Code checks after the AI ([scribe/facts.py](scribe/facts.py), `check_draft`)

Each check covers something the AI could get wrong:

| Check | Severity |
|---|---|
| A number in the note that is not in the notes, the context or the computed facts | high |
| A diagnosis, medication, referral or discharge that the clinician did not write | high |
| A high-severity red flag found in the notes but missing from the note | high |
| A finding's quote that cannot be found in the notes | medium |
| A plan item that is not one of the clinician's own stated plans | medium |
| Instruction-like text found in the input | medium |
| A note line with no linked finding, or one that cites a finding ID that doesn't exist | low |

### 4. Therapist review ([static/app.js](static/app.js))

The therapist can edit any line, add or remove lines, rewrite one section with an instruction, or accept a suggestion into the Plan.
Every edit re-runs the code checks instantly, with no AI involved. Signing needs the clinician's name and asks for a second confirmation if high-severity checks are still unresolved.
The note can then be copied, or downloaded as TXT or PDF.

### Mock mode

Setting `AI_PROVIDER=mock` swaps in a rule-based fake provider. The whole app works for demos and tests with no API key and no data leaving the machine.
Only the clinical wording is rule-based instead of AI-written.

---

## Gaps and improvements

### Safety-check gaps (confirmed by test)

Each of these was reproduced by running the code on sample input.

| # | Gap | Example | Suggested fix |
|---|---|---|---|
| 1 | **Numbers written as words get past the check** | The note line "Accuracy improved to *seventy percent*" did not trigger an invented-number warning. `fourteen out of twenty` is not picked up as a measurement, and dictated text often comes out this way. | Convert number words (English, plus Urdu such as *bees*, *chaudah*) to digits before measurements are extracted and before the number check runs. |
| 2 | **"No" doesn't cancel a red flag** | "*No choking* during meals. *Denies pain*." raised a swallowing flag and a pain flag. The check then warns that the note doesn't mention choking. | Before raising a flag, look a few words back for negations: no, not, denies, without, *nahi*, *nahin*. |
| 3 | **Red flags in Urdu script are missed** | "کھانا نگلنے میں مشکل" (difficulty swallowing) raised no flag. Only one Urdu-script word, دورہ (seizure), is in the patterns. | Add Urdu-script patterns for each red-flag category. |
| 4 | **Not every "X of Y" is an accuracy score** | "2 of 3 sessions this month" is treated as a 67% accuracy score. | Treat it as accuracy only when nearby words mean trials, words, correct or accuracy. Otherwise label it a count. |

### Product and design gaps

5. **Nothing is saved.** Drafts, edits and signatures exist only in browser memory, so a refresh or crash loses the session. There is a "leave page?" warning, but nothing more. Even for the prototype, a local autosave of the current draft would help.
6. **Signing doesn't prove anything.** The signature is a name typed into a box plus the browser's clock. Nothing records who signed, what the AI wrote compared with what the clinician changed, or which model and prompt version were used. This needs a server-side audit record before any real patient use. That belongs in the Laravel integration.
7. **Turning on `SERVICE_TOKEN` breaks the built-in page.** The page never sends the token, so every call returns 401. That's fine if only Laravel calls the service, but the README should say so. Also, the token comparison in [app.py](app.py) should use `secrets.compare_digest` so its timing doesn't reveal anything about the token.
8. **The PDF can't show Urdu.** [scribe/pdf.py](scribe/pdf.py) uses the standard Helvetica font, so a patient name or edited line in Urdu script prints as empty boxes. Registering a Noto Naskh Arabic or Noto Nastaliq Urdu font fixes this.
9. **No way to measure quality.** The 27 tests cover the plumbing, not the clinical output. There is no set of example cases to compare models (`gpt-4.1-mini` vs `gpt-5.4-mini`) or prompt changes against. 10–20 made-up cases with expected findings, goal statuses and red flags, plus a script that scores the output, would close this.
10. **Rewriting one section can drift.** The rewrite call sees the findings and the current note, but not the goal-progress analysis or the trend. A rewritten Assessment can end up disagreeing with the goal statuses shown in the analysis.

### Housekeeping

- The README setup steps are Windows-only. On macOS or Linux the commands differ:
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
  cp .env.example .env        # then add OPENAI_API_KEY
  uvicorn app:app --host 127.0.0.1 --port 8501
  ```
- There are two virtual environments: `.venv` (Python 3.14) and `.venv-1` (Python 3.15). Keep one. `.venv-1` is not in `.gitignore`.
- `MAX_CONTEXT_CHARS` in [scribe/config.py](scribe/config.py) is defined but never used. Each context field has its own size limit in [scribe/schema.py](scribe/schema.py).
- The transcription model is set to `gpt-transcribe`. The status endpoint reports "ready" without making a real call, so test dictation once with a real recording to confirm the model name is accepted.
- `.env` holds a live OpenAI key. It is git-ignored, but don't share this folder as a zip.

### Still needed before real patient use (from the main README)

Laravel authentication and clinic isolation, recorded consent, an approved AI data policy, an audit record for each AI run,
and clinical-lead review of the prompt and of an evaluation set. Use made-up data only until then.

## Suggested order

1. **Safety checks (#1–#4).** Small, self-contained changes in `facts.py`, each with new tests. About an hour.
2. **Autosave (#5).** Stops lost work during testing and demos.
3. **Evaluation set (#9).** Shows whether each later change improves the output or makes it worse.
4. **Urdu in the PDF (#8) and rewrite drift (#10).**
5. **Audit trail and signing (#6), plus the token (#7).** These belong to the Laravel integration.
