# LinguaLink360 AI Scribe

A standalone FastAPI + HTML/JavaScript prototype for allied-health clinical dictation, transcription, structured extraction, SOAP drafting, quality checks, and PDF export.

## Features

- Microphone recording with pause, resume, elapsed time and audio-level meter.
- Audio-file upload.
- OpenAI transcription with a clinical terminology hint.
- Editable transcript and explicit approval step before analysis.
- Structured clinical extraction (session context, reported information, observations, therapy activities, targets, goals, measurements, cueing, response, home practice, plan, missing information and contradictions).
- Four-section SOAP generation.
- Individual SOAP-section regeneration. The frontend updates only the selected section, preserving edits in the other sections.
- Automated quality-check warnings against the transcript.
- Copy, TXT export and PDF export.
- Configurable models and request limits.
- Legacy `/transcribe` and `/generate-soap` endpoints retained for the earlier prototype.

## Project structure

```text
lingualink360_ai_scribe/
├── app.py
├── prompts.py
├── index.html
├── requirements.txt
├── .env.example
├── README.md
└── tests/
    └── test_app.py
```

## Installation (Windows PowerShell)

```powershell
cd lingualink360_ai_scribe
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Add your API key to `.env`:

```env
OPENAI_API_KEY=your_actual_key_here
```

Do not paste your API key into `app.py`, `index.html`, screenshots, Git commits, or shared documents.

## Run locally

```powershell
uvicorn app:app --host 127.0.0.1 --port 8501 --reload
```

Open `http://127.0.0.1:8501`. Microphone permissions generally require localhost or HTTPS.

API documentation: `http://127.0.0.1:8501/docs`

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/api/scribe/transcribe` | Audio upload to transcript |
| POST | `/api/scribe/analyze` | Structured clinical extraction |
| POST | `/api/scribe/generate-soap` | Four-part SOAP draft |
| POST | `/api/scribe/validate` | Compare SOAP draft with transcript |
| POST | `/api/scribe/regenerate-section` | Regenerate one SOAP section |
| POST | `/api/scribe/export` | Download a PDF |

JSON endpoints accept `application/json`. Audio transcription accepts multipart form data with an `audio` field.

## Run tests

```powershell
pytest -q
```

Tests cover health, page serving, SOAP normalization and request validation. They do not call the OpenAI API.

## Configuration

- `OPENAI_TRANSCRIPTION_MODEL`: transcription model. Default: `gpt-transcribe` (recommended for completed audio files).
- `OPENAI_CLINICAL_MODEL`: model used for extraction, SOAP and validation.
- `MAX_AUDIO_SIZE_MB`: maximum upload size.
- `MAX_TRANSCRIPT_LENGTH`: maximum transcript length.
- `HOST`, `PORT`, `LOG_LEVEL`: server settings.

Choose model names available to your OpenAI project. Model availability and API access depend on your account and may change.

## Troubleshooting

**OpenAI API key missing:** ensure `.env` exists in the project root and `OPENAI_API_KEY` is set.

**Microphone permission denied:** use Chrome or Edge on `localhost` or HTTPS and allow microphone access.

**Transcription upload rejected:** check file type and `MAX_AUDIO_SIZE_MB`. Browser recording codecs vary.

**Model not found / access denied:** set `OPENAI_TRANSCRIPTION_MODEL` and `OPENAI_CLINICAL_MODEL` to models enabled for your API project.

**PDF export error:** reinstall dependencies with `pip install -r requirements.txt`.

**Port in use:** run with `--port 8502` and open that port in the browser.

## Important scope and production-readiness notes

This is a functional standalone prototype, not a complete production deployment inside the LinguaLink360 SaaS. It does not yet include:
- Laravel authentication/authorization, tenant isolation, patient-record integration or database persistence.
- Production-grade user/session management, audit trails, encryption/key management, malware scanning, rate limiting, CSRF strategy, backup and disaster recovery.
- Resumable/chunked long-recording upload. The current transcription endpoint enforces a configurable upload-size limit.
- Automated clinical benchmarking or a validated clinical accuracy claim.

The prototype does not persist uploaded audio or generated notes on the server. Audio is sent to the configured OpenAI API for transcription; transcript and SOAP text are sent for AI processing. Review the applicable OpenAI contractual/data controls and your organization's privacy requirements before using real patient information.

Do not claim HIPAA, GDPR, or other regulatory compliance based on this prototype. Compliance requires appropriate technical, organizational, contractual and operational controls. Use de-identified test material during development. A qualified clinician must review and approve all generated documentation.


## Transcription model selection

The default transcription model is `gpt-transcribe`, which is the recommended
OpenAI model for completed audio recordings uploaded to this application.
The browser records the therapist's dictation and uploads the completed audio
file to FastAPI, so file transcription is the appropriate workflow.

Do not use the deprecated `gpt-4o-mini-transcribe` or `gpt-4o-transcribe`
models for a new deployment. OpenAI has announced their shutdown for
February 26, 2027. If the product is later changed to stream microphone audio
continuously, evaluate `gpt-live-transcribe` and its Realtime workflow instead.
