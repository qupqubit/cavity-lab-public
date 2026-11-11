"""Local API and cancellation tests; subprocesses are controlled fakes."""
import json
import threading
import uuid
import pytest
from fastapi.testclient import TestClient
from netgen.occ import Box, Pnt

from cavity_fem import api
from cavity_fem.config import SolveConfig


class DeferredExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, function, *args):
        self.calls.append((function, args))


@pytest.fixture
def isolated_api(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DATA", tmp_path)
    monkeypatch.setattr(api, "processes", {})
    monkeypatch.setattr(api, "executor", DeferredExecutor())
    with TestClient(api.app, base_url="http://127.0.0.1:8768") as client:
        yield client, tmp_path


def new_job(data):
    gid = str(uuid.uuid4())
    source = data / "geometries" / gid / "input.step"
    source.parent.mkdir(parents=True)
    source.write_text("ISO-10303-21;\nEND-ISO-10303-21;\n")
    job = api.start_job(SolveConfig(geometry_id=gid))
    return data / "jobs" / job["id"], source


def read_status(folder):
    return json.loads((folder / "state.json").read_text())["status"]


def test_upload_vacuum_box_and_explicit_fallback(isolated_api):
    client, data = isolated_api
    step = data / "vacuum.step"
    Box(Pnt(0, 0, 0), Pnt(10, 10, 10)).WriteStep(str(step))
    for mode in ("vacuum_solid", "metal_void"):
        response = client.post("/api/inspect", data={"geometry_mode": mode},
                               files={"file": ("vacuum.step", step.read_bytes(), "application/octet-stream")})
        assert response.status_code == 200
        details = response.json()
        assert details["mode"] == "vacuum_solid"
        assert details["volume_mm3"] == pytest.approx(1000)
        assert len(details["faces"]) == 6
        if mode == "metal_void":
            assert any("真空領域" in warning for warning in details["warnings"])


def test_rejects_invalid_file_and_remote_origin(isolated_api):
    client, _ = isolated_api
    invalid = client.post("/api/inspect", files={"file": ("bad.step", b"not a STEP")})
    assert invalid.status_code == 400
    external = client.post("/api/jobs", json={}, headers={"origin": "https://unrelated.example"})
    assert external.status_code == 403
    invalid_host = client.get("/api/info", headers={"host": "unrelated.example"})
    assert invalid_host.status_code == 400


def test_queue_limit_and_cancellation_free_a_slot(isolated_api):
    client, data = isolated_api
    jobs = [new_job(data)[0] for _ in range(4)]
    with pytest.raises(api.HTTPException) as error:
        new_job(data)
    assert error.value.status_code == 429
    response = client.post("/api/jobs/%s/cancel" % jobs[0].name)
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert "finished_at" in response.json()
    assert read_status(new_job(data)[0]) == "queued"


def test_cancelled_queue_never_spawns(isolated_api, monkeypatch):
    _, data = isolated_api
    folder, source = new_job(data)
    api.cancel_job(folder.name)

    def forbidden_spawn(*args, **kwargs):
        raise AssertionError("A cancelled queue entry started a process")

    monkeypatch.setattr(api.subprocess, "Popen", forbidden_spawn)
    api.run_worker(folder, source)
    assert read_status(folder) == "cancelled"
    assert not api.processes


def test_cancel_during_spawn_terminates_registered_child(isolated_api, monkeypatch):
    _, data = isolated_api
    folder, source = new_job(data)
    spawning = threading.Event()
    release_spawn = threading.Event()
    cancelled = threading.Event()

    class FakeProcess:
        def __init__(self):
            self.terminated = threading.Event()

        def terminate(self):
            self.terminated.set()

        def wait(self, timeout=None):
            assert self.terminated.wait(3), "Cancellation missed the child process"
            return -15

    process = FakeProcess()

    def spawn(*args, **kwargs):
        spawning.set()
        assert release_spawn.wait(3)
        return process

    def cancel():
        # Also check that a valid noncanonical UUID finds the same registry key.
        api.cancel_job(folder.name.upper())
        cancelled.set()

    monkeypatch.setattr(api.subprocess, "Popen", spawn)
    worker = threading.Thread(target=api.run_worker, args=(folder, source), daemon=True)
    canceller = threading.Thread(target=cancel, daemon=True)
    worker.start()
    assert spawning.wait(3)
    canceller.start()
    try:
        # Popen/registration is an atomic region: cancellation waits for it.
        assert not cancelled.wait(.03)
    finally:
        release_spawn.set()
    worker.join(3)
    canceller.join(3)
    assert not worker.is_alive()
    assert not canceller.is_alive()
    assert process.terminated.is_set()
    assert read_status(folder) == "cancelled"
    assert not api.processes


@pytest.mark.parametrize("failure_after_cancel", [False, True])
def test_cancellation_cannot_be_overwritten_by_completion_or_failure(isolated_api, monkeypatch, failure_after_cancel):
    _, data = isolated_api
    folder, source = new_job(data)

    class FakeProcess:
        def terminate(self):
            pass

        def wait(self, timeout=None):
            (folder / "result.json").write_text("{}")
            api.cancel_job(folder.name)
            if failure_after_cancel:
                raise RuntimeError("simulated wait failure after cancellation")
            return 0

    monkeypatch.setattr(api.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    api.run_worker(folder, source)
    assert read_status(folder) == "cancelled"
    assert not api.processes


def test_successful_completion_remains_terminal(isolated_api, monkeypatch):
    _, data = isolated_api
    folder, source = new_job(data)

    class FakeProcess:
        def wait(self, timeout=None):
            (folder / "result.json").write_text("{}")
            return 0

    monkeypatch.setattr(api.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    api.run_worker(folder, source)
    assert api.cancel_job(folder.name)["status"] == "completed"
    assert not api.processes


def test_unknown_ids_and_unfinished_download_return_404(isolated_api):
    client, data = isolated_api
    assert client.get("/api/jobs/not-a-uuid").status_code == 404
    assert client.get("/api/jobs/%s" % uuid.uuid4()).status_code == 404
    folder, _ = new_job(data)
    assert client.get("/api/jobs/%s/result.json" % folder.name).status_code == 404




