from fastapi.testclient import TestClient


def evidence_by_source(body: dict, source: str) -> dict:
    return next(item["payload"] for item in body["evidence"] if item["source"] == source)


def test_inc_002_collects_automapping_evidence(seeded_client: TestClient) -> None:
    body = seeded_client.post("/incidents/INC-002/investigate").json()
    permissions = evidence_by_source(body, "get_mailbox_permissions")
    assert permissions["full_access"] is True
    assert permissions["automapping"] is False


def test_inc_003_collects_send_as_evidence(seeded_client: TestClient) -> None:
    body = seeded_client.post("/incidents/INC-003/investigate").json()
    permissions = evidence_by_source(body, "get_mailbox_permissions")
    assert permissions["full_access"] is True
    assert permissions["send_as"] is False


def test_inc_014_collects_critical_disk_evidence(seeded_client: TestClient) -> None:
    body = seeded_client.post("/incidents/INC-014/investigate").json()
    assert evidence_by_source(body, "get_disk_usage")["disk_percent"] == 98.4


def test_inc_019_distinguishes_connectivity_from_dns(seeded_client: TestClient) -> None:
    body = seeded_client.post("/incidents/INC-019/investigate").json()
    assert evidence_by_source(body, "check_external_connectivity")["external_reachable"] is True
    assert evidence_by_source(body, "check_dns_resolution")["resolved"] is False


def test_inc_021_collects_high_cpu_and_alerts(seeded_client: TestClient) -> None:
    body = seeded_client.post("/incidents/INC-021/investigate").json()
    assert evidence_by_source(body, "get_metrics")["cpu_percent"] == 97.6
    assert evidence_by_source(body, "get_recent_alerts")["alerts"][0]["severity"] == "critical"


def test_inc_023_collects_degraded_application_context(seeded_client: TestClient) -> None:
    body = seeded_client.post("/incidents/INC-023/investigate").json()
    application = evidence_by_source(body, "get_application_health")
    assert application["status"] == "degraded"
    assert application["error_rate_percent"] == 12.4


def test_api_retrieves_persisted_evidence_and_steps(seeded_client: TestClient) -> None:
    investigated = seeded_client.post("/incidents/INC-002/investigate")
    assert investigated.status_code == 200
    evidence = seeded_client.get("/incidents/INC-002/evidence")
    investigation = seeded_client.get("/incidents/INC-002/investigation")
    assert evidence.status_code == 200
    assert len(evidence.json()) == 3
    assert len(investigation.json()["steps"]) == 3
    assert all(step["status"] == "completed" for step in investigation.json()["steps"])


def test_unknown_incident_returns_structured_404(seeded_client: TestClient) -> None:
    response = seeded_client.post("/incidents/INC-999/investigate")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "incident_not_found"


def test_incident_without_playbook_returns_structured_error(seeded_client: TestClient) -> None:
    response = seeded_client.post("/incidents/INC-001/investigate")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "investigation_not_supported"
