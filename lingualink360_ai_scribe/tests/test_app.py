import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
import app

client = TestClient(app.app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_home_page():
    response = client.get("/")
    assert response.status_code == 200
    assert "LinguaLink360" in response.text


def test_normalize_soap_fills_missing_sections():
    result = app.normalize_soap({"subjective": "Caregiver reports..."})
    assert result["subjective"] == "Caregiver reports..."
    assert result["objective"] == "Not documented."
    assert result["assessment"] == "Not documented."
    assert result["plan"] == "Not documented."


def test_transcript_validation_rejects_empty():
    response = client.post("/api/scribe/analyze", json={"transcript": ""})
    assert response.status_code == 422


def test_export_rejects_invalid_payload():
    response = client.post("/api/scribe/export", json={"transcript": "hello", "soap": {}})
    assert response.status_code == 422
