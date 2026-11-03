import pytest
from fastapi.testclient import TestClient
from app.main import app

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client

def test_health_marks_simulated_data(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["data_mode"] == "SIMULATED"

def test_ports_and_seed_vessels(client):
    assert len(client.get("/api/ports").json()) >= 10
    vessels = client.get("/api/vessels").json()
    assert len(vessels) >= 1
    assert all(v["id"] and v["name"] for v in vessels)

def test_route_planner_returns_three_strategies(client):
    vessel = client.get("/api/vessels").json()[0]
    response = client.post("/api/routes/plan", json={
        "vessel_id": vessel["id"],
        "origin_id": "INMUM",
        "destination_id": "INMAA"
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["data_mode"] == "SIMULATED"
    options = data["options"]
    assert {option["strategy"] for option in options} == {"fastest", "efficient", "safest"}
    assert all(option["distance_nm"] > 0 for option in options)
    assert all(option["fuel_tons"] > 0 and option["co2_tons"] > 0 for option in options)
    assert all(len(option["route"]) >= 2 for option in options)

def test_analytics_totals_are_consistent(client):
    data = client.get("/api/analytics").json()
    assert data["total_voyages"] == data["completed"] + data["active"] + data["interrupted"]
    assert data["distance_nm"] >= 0
    assert data["fuel_tons"] >= 0
