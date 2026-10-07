from collections.abc import Iterator
from typing import get_args

import pytest
from fastapi.testclient import TestClient

from triage.config import Settings
from triage.main import create_app
from triage.questions import CATEGORY, URGENCY_LEVELS
from triage.schemas import Category, UrgencyLevel

CLEAR = {
    "subject": "Export crashes",
    "body": "Every time I export a report the app crashes with an error. It blocks my work and I need it fixed today.",
}
VAGUE = {"subject": "Question", "body": "Hi, quick question about something I noticed earlier."}


@pytest.fixture
def audit_all(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings.model_copy(update={"audit_rate": 1.0}))) as client:
        yield client


def test_review_choices_match_the_questions_asked() -> None:
    assert set(get_args(Category)) == set(CATEGORY.criteria)
    assert get_args(UrgencyLevel) == URGENCY_LEVELS


def test_queue_holds_uncertain_tickets_but_not_unaudited_auto_ones(client: TestClient) -> None:
    auto = client.post("/api/tickets", json=CLEAR).json()
    human = client.post("/api/tickets", json=VAGUE).json()

    assert (auto["route"], auto["audit"], auto["needs_review"]) == ("auto", False, False)
    assert (human["route"], human["needs_review"]) == ("human", True)
    assert [t["id"] for t in client.get("/api/review-queue").json()] == [human["id"]]


def test_audited_auto_tickets_join_the_queue(audit_all: TestClient) -> None:
    ticket = audit_all.post("/api/tickets", json=CLEAR).json()

    assert (ticket["route"], ticket["audit"], ticket["needs_review"]) == ("auto", True, True)
    assert [t["id"] for t in audit_all.get("/api/review-queue").json()] == [ticket["id"]]


def test_only_auto_tickets_are_audited(audit_all: TestClient) -> None:
    assert audit_all.post("/api/tickets", json=VAGUE).json()["audit"] is False


def test_review_records_answers_and_leaves_the_queue(client: TestClient) -> None:
    ticket = client.post("/api/tickets", json=VAGUE).json()

    reviewed = client.post(
        f"/api/tickets/{ticket['id']}/review", json={"category": "account", "urgency_level": "low"}
    ).json()

    assert (reviewed["reviewed_category"], reviewed["reviewed_urgency_level"]) == ("account", "low")
    assert reviewed["reviewed_at"] is not None
    assert reviewed["needs_review"] is False
    assert client.get("/api/review-queue").json() == []


def test_review_rejects_unknown_answers_and_tickets(client: TestClient) -> None:
    ticket = client.post("/api/tickets", json=VAGUE).json()
    url = f"/api/tickets/{ticket['id']}/review"

    assert client.post(url, json={"category": "sales", "urgency_level": "low"}).status_code == 422
    assert client.post(url, json={"category": "billing", "urgency_level": "urgent"}).status_code == 422
    assert (
        client.post(
            "/api/tickets/999/review", json={"category": "billing", "urgency_level": "low"}
        ).status_code
        == 404
    )


def test_stats_measure_accuracy_per_route(audit_all: TestClient) -> None:
    right = audit_all.post("/api/tickets", json=CLEAR).json()
    wrong = audit_all.post("/api/tickets", json=CLEAR).json()
    audit_all.post(
        f"/api/tickets/{right['id']}/review",
        json={"category": right["category"], "urgency_level": right["urgency_level"]},
    )
    audit_all.post(
        f"/api/tickets/{wrong['id']}/review",
        json={"category": "shipping", "urgency_level": right["urgency_level"]},
    )

    stats = audit_all.get("/api/stats").json()

    assert stats["accuracy"]["auto"] == {
        "reviewed": 2,
        "category_correct": 1,
        "urgency_correct": 2,
        "both_correct": 1,
    }
    assert stats["accuracy"]["human"]["reviewed"] == 0
    assert stats["pending_review"] == 0


def test_review_page_and_form(client: TestClient) -> None:
    ticket = client.post("/api/tickets", json=VAGUE).json()

    page = client.get("/review").text
    assert "Question" in page
    assert f"/ui/tickets/{ticket['id']}/review" in page

    response = client.post(
        f"/ui/tickets/{ticket['id']}/review",
        data={"category": "account", "urgency_level": "low"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "reviewed" in response.text
    assert "Nothing to review" in client.get("/review").text


def test_plain_review_form_redirects_back_to_the_queue(client: TestClient) -> None:
    ticket = client.post("/api/tickets", json=VAGUE).json()

    response = client.post(
        f"/ui/tickets/{ticket['id']}/review",
        data={"category": "account", "urgency_level": "low"},
        follow_redirects=False,
    )
    assert (response.status_code, response.headers["location"]) == (303, "/review")
