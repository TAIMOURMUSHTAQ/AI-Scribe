import json

from fastapi.testclient import TestClient

import app as appmod
from scribe.providers import MockProvider

appmod.app.state.provider = MockProvider(delay=0, chunk=400)
client = TestClient(appmod.app)
NOTE = ("45 min. /s/ initial words 14/20 with min verbal cues. Phrase level 6 out of 10 mod cues. "
        "Ammi ne bataya k ghar pe practice ki. Plan: continue phrase level next session.")


def sse_events(text):
    out = []
    for block in text.split("\n\n"):
        lines = [l for l in block.split("\n") if l and not l.startswith(":")]
        if not lines:
            continue
        ev = lines[0].removeprefix("event: ")
        out.append((ev, json.loads(lines[1].removeprefix("data: "))))
    return out


def test_pages_and_status():
    assert client.get("/health").json()["status"] == "ok"
    assert "LinguaLink360" in client.get("/").text
    st = client.get("/api/scribe/status").json()
    assert st["provider"] == "mock" and "key" not in json.dumps(st).lower().replace("api_key_configured", "")


def test_draft_streams_in_order():
    r = client.post("/api/scribe/draft", json={"input": NOTE, "context": {"goals": "/s/ phrase 80%"}})
    assert r.headers["content-type"].startswith("text/event-stream")
    evs = sse_events(r.text)
    names = [e for e, _ in evs]
    assert names[:2] == ["meta", "facts"] and names[-1] == "final"
    final = evs[-1][1]
    assert final["status"] == "ai_draft"
    assert final["facts"]["measurements"][0]["percent_text"] == "70%"
    assert set(final["draft"]["soap"]) == {"subjective", "objective", "assessment", "plan"}
    assert final["checks"]["evidence_total"] == len(final["draft"]["analysis"]["findings"])


def test_draft_json_mode_for_laravel():
    r = client.post("/api/scribe/draft?stream=false", json={"input": NOTE})
    assert r.status_code == 200 and r.json()["status"] == "ai_draft"


def test_blank_input_rejected():
    assert client.post("/api/scribe/draft", json={"input": "   "}).status_code == 422


def test_check_and_regenerate_and_export():
    final = sse_events(client.post("/api/scribe/draft", json={"input": NOTE}).text)[-1][1]
    body = {"input": NOTE, "analysis": final["draft"]["analysis"], "soap": final["draft"]["soap"]}
    body["soap"]["assessment"] = [{"text": "Consistent with dysarthria, 95% expected.", "basis": [], "edited": True}]
    chk = client.post("/api/scribe/check", json=body).json()
    assert chk["counts"]["high"] >= 2
    regen = client.post("/api/scribe/regenerate", json={**body, "section": "plan", "instruction": "shorter"}).json()
    assert regen["section"] == "plan" and regen["statements"]
    pdf = client.post("/api/scribe/export", json={"soap": final["draft"]["soap"], "header": {"patient_id": "T-1"},
                                                  "signed_by": "Dr. Test"})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_transcribe_validation():
    assert client.post("/api/scribe/transcribe", files={"audio": ("a.txt", b"hello", "text/plain")}).status_code == 415
    assert client.post("/api/scribe/transcribe", files={"audio": ("a.webm", b"", "audio/webm")}).status_code == 400
    ok = client.post("/api/scribe/transcribe", files={"audio": ("a.webm", b"x" * 4000, "audio/webm;codecs=opus")},
                     data={"segment": "2"})
    assert ok.status_code == 200 and ok.json()["segment"] == 2
