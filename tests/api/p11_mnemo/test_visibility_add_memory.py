from fastapi.testclient import TestClient

from main import app


client = TestClient(app)


def test_add_memory_rejects_invalid_visibility(monkeypatch):
    # Patch remme_store.add so we don't hit real vector store
    import routers.remme as remme_router

    class DummyStore:
        def add(self, text, embedding, **kwargs):
            raise AssertionError("add should not be called for invalid visibility")

    remme_router.remme_store = DummyStore()

    res = client.post(
        "/remme/add",
        json={"text": "hello", "category": "general", "visibility": "not-valid"},
    )
    assert res.status_code == 400
    assert "Invalid visibility" in res.json()["detail"]


def test_add_memory_accepts_valid_visibility(monkeypatch):
    # Ensure we can pass a valid visibility and it flows into add() kwargs
    import routers.remme as remme_router

    calls = {}

    class DummyStore:
        def add(self, text, embedding, **kwargs):
            calls["kwargs"] = kwargs
            # Simulate stored memory shape
            return {"id": "mem-1", "text": text, **kwargs}

    remme_router.remme_store = DummyStore()

    # Patch embedding to avoid hitting actual model
    import remme.utils as remme_utils

    def fake_embedding(text, task_type=None):
        return [0.0, 0.0, 0.0]

    remme_utils.get_embedding = fake_embedding

    res = client.post("/remme/add", json={"text": "hello", "category": "general", "visibility": "private"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    # Verify visibility propagated into add kwargs
    assert calls["kwargs"]["metadata"]["visibility"] == "private"


def test_add_memory_in_space_defaults_to_space_visibility(monkeypatch):
    # When space_id is provided and visibility omitted, default should be "space"
    import routers.remme as remme_router

    calls = {}

    class DummyStore:
        def add(self, text, embedding, **kwargs):
            calls["kwargs"] = kwargs
            return {"id": "mem-2", "text": text, **kwargs}

    remme_router.remme_store = DummyStore()

    import remme.utils as remme_utils

    def fake_embedding(text, task_type=None):
        return [0.0, 0.0, 0.0]

    remme_utils.get_embedding = fake_embedding

    res = client.post(
        "/remme/add",
        json={"text": "space note", "category": "general", "space_id": "space-123"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert calls["kwargs"]["metadata"]["visibility"] == "space"


def test_add_memory_global_defaults_to_private(monkeypatch):
    # When no space_id and no visibility, default should be "private"
    import routers.remme as remme_router

    calls = {}

    class DummyStore:
        def add(self, text, embedding, **kwargs):
            calls["kwargs"] = kwargs
            return {"id": "mem-3", "text": text, **kwargs}

    remme_router.remme_store = DummyStore()

    import remme.utils as remme_utils

    def fake_embedding(text, task_type=None):
        return [0.0, 0.0, 0.0]

    remme_utils.get_embedding = fake_embedding

    res = client.post(
        "/remme/add",
        json={"text": "global note", "category": "general"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert calls["kwargs"]["metadata"]["visibility"] == "private"


def test_add_memory_rejects_space_visibility_without_space(monkeypatch):
    # visibility="space" requires a concrete non-global space_id
    res = client.post(
        "/remme/add",
        json={"text": "bad combo", "category": "general", "visibility": "space"},
    )
    assert res.status_code == 400
    assert "requires a non-global space_id" in res.json()["detail"]

