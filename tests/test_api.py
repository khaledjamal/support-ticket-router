from fastapi.testclient import TestClient

CLEAR = {
    "subject": "Export crashes",
    "body": "Every time I export a report the app crashes with an error. It blocks my work and I need it fixed today.",
    "customer": "ana@example.com",
}
VAGUE = {"subject": "Question", "body": "Hi, quick question about something I noticed earlier."}


def test_create_ticket_stores_answers_route_and_decision(client: TestClient) -> None:
    response = client.post("/api/tickets", json=CLEAR)

    assert response.status_code == 201
    ticket = response.json()
    assert ticket["route"] == "auto"
    assert ticket["category"] == "technical"
    assert ticket["urgency_level"] in {"high", "critical"}
    assert ticket["customer"] == "ana@example.com"
    assert ticket["decision"]["backend"] == "fake"
    assert set(ticket["decision"]["answers"]) == {"category", "urgency", "churn_risk"}
    assert client.get(f"/api/tickets/{ticket['id']}").json() == ticket


def test_list_tickets_newest_first_with_route_filter(client: TestClient) -> None:
    first = client.post("/api/tickets", json=CLEAR).json()
    second = client.post("/api/tickets", json=VAGUE).json()

    assert [t["id"] for t in client.get("/api/tickets").json()] == [second["id"], first["id"]]
    assert [t["id"] for t in client.get("/api/tickets", params={"route": "human"}).json()] == [second["id"]]
    assert client.get("/api/tickets", params={"route": "nope"}).status_code == 422


def test_stats(client: TestClient) -> None:
    empty = client.get("/api/stats").json()
    assert empty["total"] == 0
    assert empty["automation_rate"] is None
    assert empty["latency_p95_ms"] is None

    client.post("/api/tickets", json=CLEAR)
    client.post("/api/tickets", json=VAGUE)
    stats = client.get("/api/stats").json()

    assert stats["total"] == 2
    assert stats["by_route"] == {"auto": 1, "review": 0, "human": 1}
    assert stats["automation_rate"] == 0.5
    assert stats["errors"] == 0
    assert stats["latency_p50_ms"] <= stats["latency_p95_ms"]


def test_rejects_invalid_tickets(client: TestClient) -> None:
    assert client.post("/api/tickets", json={"subject": "", "body": "x"}).status_code == 422
    assert client.post("/api/tickets", json={"subject": "x"}).status_code == 422
    assert client.post("/api/tickets", json={"subject": "x" * 201, "body": "x"}).status_code == 422


def test_unknown_ticket_is_404(client: TestClient) -> None:
    assert client.get("/api/tickets/999").status_code == 404


def test_healthz_reports_backend(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok", "backend": "fake"}
