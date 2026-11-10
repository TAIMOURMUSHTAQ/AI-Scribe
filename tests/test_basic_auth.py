import base64

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import BasicAuth


def make_client(user="", password=""):
    inner = FastAPI()

    @inner.get("/health")
    def health():
        return {"status": "ok"}

    @inner.get("/")
    def home():
        return {"page": "home"}

    inner.add_middleware(BasicAuth, user=user, password=password)
    return TestClient(inner)


def auth(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def test_off_when_not_configured():
    assert make_client().get("/").status_code == 200


def test_requires_credentials_when_configured():
    c = make_client("staff", "s3cret")
    r = c.get("/")
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Basic")
    assert c.get("/", headers=auth("staff", "wrong")).status_code == 401
    assert c.get("/", headers=auth("staff", "s3cret")).status_code == 200


def test_health_stays_open_for_render():
    assert make_client("staff", "s3cret").get("/health").status_code == 200
