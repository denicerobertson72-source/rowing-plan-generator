from fastapi.testclient import TestClient
from services.api.app.main import app

client = TestClient(app)

def test_health_exposes_versioned_api():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["api_version"] == "v1"

def test_openapi_is_available():
    response = client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    assert "/api/v1/plans/generate" in response.json()["paths"]
