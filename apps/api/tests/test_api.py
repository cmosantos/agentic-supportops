from fastapi.testclient import TestClient


INCIDENT = {
    "title": "VPN unavailable",
    "description": "Requester cannot connect to the corporate VPN.",
    "category": "network",
    "priority": "high",
    "requester": "alex@example.com",
}


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "agentic-supportops-api"}


def test_create_incident(client: TestClient) -> None:
    response = client.post("/incidents", json=INCIDENT)
    assert response.status_code == 201
    body = response.json()
    assert body["id"] == 1
    assert body["status"] == "open"
    assert body["title"] == INCIDENT["title"]


def test_list_incidents(client: TestClient) -> None:
    client.post("/incidents", json=INCIDENT)
    response = client.get("/incidents")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["category"] == "network"


def test_retrieve_incident(client: TestClient) -> None:
    created = client.post("/incidents", json=INCIDENT).json()
    response = client.get(f"/incidents/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


def test_incident_not_found(client: TestClient) -> None:
    response = client.get("/incidents/999")
    assert response.status_code == 404
    assert response.json() == {
        "detail": {"code": "incident_not_found", "message": "Incident not found"}
    }

