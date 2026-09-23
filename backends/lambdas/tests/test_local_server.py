from conftest import mention_payload, signed_event
from fastapi.testclient import TestClient


def test_local_server_routes_to_handlers(monkeypatch):
    from slack_app import local_server
    from slack_app.handlers import slack_events

    jobs = []
    monkeypatch.setattr(slack_events, "enqueue", lambda job, **kw: jobs.append(job))
    client = TestClient(local_server.app)

    assert client.get("/healthz").json() == {"status": "ok"}

    signed = signed_event(mention_payload())
    response = client.post("/slack/events", content=signed["body"], headers=signed["headers"])
    assert response.status_code == 200
    assert jobs and jobs[0]["user"] == "UALICE"

    response = client.get("/oauth2/start", params={"nonce": "missing"})
    assert response.status_code == 400

    # The CIMD client document is public: authorization servers fetch it themselves.
    metadata = client.get("/oauth2/client-metadata.json")
    assert metadata.status_code == 200
    assert metadata.json()["client_id"].endswith("/oauth2/client-metadata.json")
