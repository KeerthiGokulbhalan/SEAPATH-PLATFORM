import pytest
from fastapi.testclient import TestClient
from app import main as app_module

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    app_module.DB_PATH = tmp_path_factory.mktemp("seapath-test") / "test.sqlite3"
    with TestClient(app_module.app) as test_client:
        yield test_client

def test_health_marks_simulated_data(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["data_mode"] == "SIMULATED"

def test_ports_and_seed_vessels(client):
    assert len(client.get("/api/ports").json()) >= 10
    vessels = client.get("/api/vessels").json()
    assert len(vessels) >= 2
    assert all(v["id"] and v["name"] for v in vessels)

def test_three_routes_and_independent_voyage_lifecycle(client):
    vessels = client.get("/api/vessels").json()
    first, second = vessels[0], vessels[1]
    response = client.post("/api/routes/plan", json={
        "vessel_id": first["id"],
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

    first_run = client.post("/api/voyages", json={
        "vessel_id": first["id"], "origin_id": "INMUM",
        "destination_id": "INMAA", "strategy": "fastest"
    })
    second_run = client.post("/api/voyages", json={
        "vessel_id": second["id"], "origin_id": "INMUM",
        "destination_id": "INMAA", "strategy": "safest"
    })
    assert first_run.status_code == 201, first_run.text
    assert second_run.status_code == 201, second_run.text
    first_id, second_id = first_run.json()["id"], second_run.json()["id"]
    assert first_id != second_id

    # A vessel cannot accidentally overwrite another active voyage.
    duplicate = client.post("/api/voyages", json={
        "vessel_id": first["id"], "origin_id": "INMUM",
        "destination_id": "INMAA", "strategy": "efficient"
    })
    assert duplicate.status_code == 409

    client.post(f"/api/voyages/{first_id}/tick?minutes=30")
    untouched = client.get(f"/api/voyages/{second_id}").json()
    assert untouched["progress"] == 0
    assert untouched["status"] == "IN_PROGRESS"

    interrupted = client.post(f"/api/voyages/{first_id}/interrupt").json()
    assert interrupted["status"] == "INTERRUPTED"
    report = client.get(f"/api/voyages/{first_id}/analytics").json()
    assert report["voyage_id"] == first_id
    assert report["status"] == "INTERRUPTED"

def test_analytics_totals_are_consistent(client):
    data = client.get("/api/analytics").json()
    assert data["total_voyages"] == data["completed"] + data["active"] + data["interrupted"]
    assert data["distance_nm"] >= 0
    assert data["fuel_tons"] >= 0

def test_simulated_weather_is_repeatable_and_bounded(client):
    from app.main import weather_risk
    first = weather_risk(14.5, 78.2)
    second = weather_risk(14.5, 78.2)
    assert first == second
    assert 0 <= first[0] <= 1
    assert first[1] > 0
    assert first[2] > 0
