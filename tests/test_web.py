from fastapi.testclient import TestClient

FORM = {"subject": "Double billing", "body": "My credit card was charged twice, please refund the payment."}


def test_dashboard_renders_when_empty(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "No tickets yet" in response.text


def test_htmx_submit_returns_a_row_and_triggers_a_stats_refresh(client: TestClient) -> None:
    response = client.post("/ui/tickets", data=FORM, headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert response.text.lstrip().startswith("<tr")
    assert "Double billing" in response.text
    assert response.headers["HX-Trigger"] == "ticket-created"
    assert ">1<" in client.get("/ui/stats").text


def test_plain_form_submit_redirects_to_the_dashboard(client: TestClient) -> None:
    response = client.post("/ui/tickets", data=FORM, follow_redirects=False)

    assert response.status_code == 303
    assert "Double billing" in client.get("/").text


def test_dashboard_filters_by_route(client: TestClient) -> None:
    client.post("/ui/tickets", data=FORM)
    client.post("/ui/tickets", data={"subject": "Question", "body": "Hi, quick question."})

    page = client.get("/", params={"route": "human"}).text
    assert "Question" in page
    assert "Double billing" not in page
