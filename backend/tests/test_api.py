from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health_marks_simulated_data():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["data_mode"] == "SIMULATED"

def test_ports_and_seed_vessels():
    assert len(client.get("/api/ports").json()) >= 10
    vessels = client.get("/api/vessels").json()
    assert len(vessels) >= 1
    assert all(v["id"] and v["name"] for v in vessels)

def test_analytics_empty_or_consistent():
    data = client.get("/api/analytics").json()
    assert data["total_voyages"] == data["completed"] + data["active"] + data["interrupted"]
