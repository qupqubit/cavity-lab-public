"""Local API and cancellation tests; subprocesses are controlled fakes."""
import json
import threading
import uuid
import pytest
from fastapi.testclient import TestClient
from netgen.occ import Box, Pnt

from cavity_fem import api
from cavity_fem.config import SolveConfig


GIB = 1024 ** 3


def known_memory_policy(**changes):
    """Valid deterministic budget; API tests must not depend on the test host."""
    policy = {"detected": True, "status": "ok", "total_bytes": 16 * GIB,
              "available_bytes": 12 * GIB, "reserve_bytes": 3 * GIB,
              "budget_bytes": 6 * GIB, "dof_limit": 180000,
              "note": "テスト用のメモリ予算"}
    policy.update(changes)
    return policy


class ControlledProcess:
    """One fake child with observable lifecycle; no real process is signalled."""
    def __init__(self, pid=43001):
        self.pid = pid
        self.returncode = None
        self.kill_count = 0
        self.terminate_count = 0
        self.wait_calls = []

    def poll(self):
        return self.returncode

    def kill(self):
        self.kill_count += 1
        self.returncode = -9

    def terminate(self):
        self.terminate_count += 1
        self.returncode = -15

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self.returncode is None:
            raise api.subprocess.TimeoutExpired("controlled FEM worker", timeout)
        return self.returncode


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
    monkeypatch.setattr(api, "memory_policy", lambda config=None: known_memory_policy())
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

    class FakeProcess(ControlledProcess):
        def __init__(self):
            super().__init__()
            self.terminated = threading.Event()

        def terminate(self):
            super().terminate()
            self.terminated.set()

        def wait(self, timeout=None):
            assert self.terminated.wait(3), "Cancellation missed the child process"
            return self.returncode

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

    class FakeProcess(ControlledProcess):
        def wait(self, timeout=None):
            if self.returncode is not None:
                return self.returncode
            (folder / "result.json").write_text("{}")
            api.cancel_job(folder.name)
            if failure_after_cancel:
                raise RuntimeError("simulated wait failure after cancellation")
            self.returncode = 0
            return 0

    monkeypatch.setattr(api.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    api.run_worker(folder, source)
    assert read_status(folder) == "cancelled"
    assert not api.processes


def test_successful_completion_remains_terminal(isolated_api, monkeypatch):
    _, data = isolated_api
    folder, source = new_job(data)

    class FakeProcess(ControlledProcess):
        def wait(self, timeout=None):
            (folder / "result.json").write_text("{}")
            self.returncode = 0
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


def test_memory_policy_is_exposed_without_host_ram_dependency(isolated_api):
    client, _ = isolated_api
    expected = known_memory_policy()
    assert client.get("/api/info").json()["memory"] == expected
    response = client.post("/api/estimate", json={"order": 3, "n_modes": 6})
    assert response.status_code == 200
    assert response.json()["memory"] == expected


@pytest.mark.parametrize("unavailable_status", ["unavailable", "insufficient_memory"])
def test_worker_memory_unavailable_prevents_spawn(isolated_api, monkeypatch, unavailable_status):
    client, data = isolated_api
    folder, source = new_job(data)
    policy = known_memory_policy(status=unavailable_status,
                                 detected=unavailable_status != "unavailable",
                                 available_bytes=None if unavailable_status == "unavailable" else GIB,
                                 budget_bytes=0, dof_limit=0)
    monkeypatch.setattr(api, "memory_policy", lambda config=None: dict(policy))

    def forbidden_spawn(*args, **kwargs):
        pytest.fail("Worker spawned without a usable memory budget")

    monkeypatch.setattr(api.subprocess, "Popen", forbidden_spawn)
    api.run_worker(folder, source)
    response = client.get("/api/jobs/%s" % folder.name)
    assert response.status_code == 200
    job = response.json()
    assert job["status"] == "failed"
    assert job["memory"] == policy
    assert ("メモリ情報を取得できない" if unavailable_status == "unavailable"
            else "空きメモリが不足") in job["error"]
    assert "finished_at" in job
    assert not api.processes


def test_queued_worker_receives_fresh_memory_budget(isolated_api, monkeypatch):
    client, data = isolated_api
    original = client.post("/api/estimate", json={}).json()["memory"]
    folder, _ = new_job(data)
    fresh = known_memory_policy(available_bytes=7 * GIB, budget_bytes=3 * GIB, dof_limit=110000)
    checked_configs = []
    captured = {}

    def current_policy(config=None):
        checked_configs.append(config)
        return dict(fresh)

    class CompletedProcess(ControlledProcess):
        def wait(self, timeout=None):
            (folder / "result.json").write_text("{}")
            self.returncode = 0
            return 0

    process = CompletedProcess()

    def spawn(*args, **kwargs):
        captured.update(kwargs)
        return process

    monkeypatch.setattr(api, "memory_policy", current_policy)
    monkeypatch.setattr(api.subprocess, "Popen", spawn)
    function, args = api.executor.calls[0]
    assert read_status(folder) == "queued"
    function(*args)
    assert checked_configs == [json.loads((folder / "config.json").read_text())]
    assert fresh["budget_bytes"] != original["budget_bytes"]
    assert captured["env"]["CAVITY_FEM_MEMORY_BUDGET_BYTES"] == str(fresh["budget_bytes"])
    job = client.get("/api/jobs/%s" % folder.name).json()
    assert job["status"] == "completed"
    assert job["memory"] == fresh
    assert process.kill_count == 0
    assert not api.processes


def test_runtime_memory_violation_stops_only_registered_worker(isolated_api, monkeypatch):
    client, data = isolated_api
    folder, source = new_job(data)
    worker, other = ControlledProcess(), ControlledProcess(pid=43002)
    other_key = str(uuid.uuid4())
    api.processes[other_key] = other
    reason = "計算プロセスのメモリ使用量が予算を超えたため停止しました。"
    sampled = []

    def sample(pid, budget, reserve):
        assert api.processes[folder.name] is worker
        sampled.append((pid, budget, reserve))
        return {"status": "budget_exceeded", "violated": True, "reason": reason,
                "worker_rss_bytes": 7 * GIB, "available_bytes": 5 * GIB}

    monkeypatch.setattr(api.subprocess, "Popen", lambda *args, **kwargs: worker)
    monkeypatch.setattr(api, "runtime_memory_status", sample)
    api.run_worker(folder, source)
    job = client.get("/api/jobs/%s" % folder.name).json()
    assert job["status"] == "failed"
    assert job["error"] == reason
    assert job["memory"]["worker_rss_bytes"] == 7 * GIB
    assert job["memory"]["budget_bytes"] == known_memory_policy()["budget_bytes"]
    assert sampled == [(worker.pid, 6 * GIB, 3 * GIB)]
    assert worker.kill_count == 1
    assert worker.poll() == -9
    assert None in worker.wait_calls  # The stopped worker was reaped.
    assert api.processes == {other_key: other}
    assert other.kill_count == other.terminate_count == 0
    assert other.poll() is None
    assert other.wait_calls == []


def test_worker_resource_error_json_surfaces_human_message(isolated_api, monkeypatch):
    client, data = isolated_api
    folder, source = new_job(data)
    message = "メモリ予算の上限を超えました。最大要素寸法を大きくして再試行してください。"

    class ResourceFailure(ControlledProcess):
        def wait(self, timeout=None):
            (folder / "error.json").write_text(json.dumps(
                {"kind": "memory_limit", "message": message}, ensure_ascii=False))
            (folder / "solver.log").write_text("Traceback: internal resource guard details")
            self.returncode = 1
            return 1

    monkeypatch.setattr(api.subprocess, "Popen", lambda *args, **kwargs: ResourceFailure())
    api.run_worker(folder, source)
    job = client.get("/api/jobs/%s" % folder.name).json()
    assert job["status"] == "failed"
    assert job["error"] == message
    assert "Traceback" not in job["error"]
    assert not api.processes


def test_unexpected_monitor_exception_reaps_only_captured_child(isolated_api, monkeypatch):
    client, data = isolated_api
    folder, source = new_job(data)
    worker, other = ControlledProcess(), ControlledProcess(pid=43002)
    other_key = str(uuid.uuid4())
    api.processes[other_key] = other

    def unavailable_monitor(*args):
        assert worker.poll() is None
        raise RuntimeError("simulated monitor transport failure")

    monkeypatch.setattr(api.subprocess, "Popen", lambda *args, **kwargs: worker)
    monkeypatch.setattr(api, "runtime_memory_status", unavailable_monitor)
    api.run_worker(folder, source)
    job = client.get("/api/jobs/%s" % folder.name).json()
    assert job["status"] == "failed"
    assert "simulated monitor transport failure" in job["error"]
    assert worker.kill_count == 1
    assert worker.poll() == -9
    assert any(timeout != .5 for timeout in worker.wait_calls)
    assert api.processes == {other_key: other}
    assert other.kill_count == other.terminate_count == 0
    assert other.wait_calls == []


@pytest.mark.parametrize("cancel_at", ["sampling", "sampling_error", "kill"])
def test_cancellation_remains_terminal_around_memory_monitor(isolated_api, monkeypatch, cancel_at):
    client, data = isolated_api
    folder, source = new_job(data)

    class CancellingProcess(ControlledProcess):
        def kill(self):
            if cancel_at == "kill":
                api.cancel_job(folder.name)
            super().kill()

        def wait(self, timeout=None):
            code = super().wait(timeout)
            # Late output must not replace the user's terminal cancellation.
            (folder / "result.json").write_text("{}")
            (folder / "error.json").write_text(json.dumps(
                {"kind": "memory_limit", "message": "late resource error"}))
            return code

    process = CancellingProcess()

    def sample(*args):
        if cancel_at in ("sampling", "sampling_error"):
            api.cancel_job(folder.name)
        if cancel_at == "sampling_error":
            raise RuntimeError("monitor failed after cancellation")
        return {"status": "budget_exceeded", "violated": True, "reason": "late violation"}

    monkeypatch.setattr(api.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(api, "runtime_memory_status", sample)
    api.run_worker(folder, source)
    job = client.get("/api/jobs/%s" % folder.name).json()
    assert job["status"] == "cancelled"
    assert "error" not in job
    assert "finished_at" in job
    assert process.terminate_count == 1
    assert process.kill_count == (1 if cancel_at == "kill" else 0)
    assert process.poll() is not None
    assert not api.processes
