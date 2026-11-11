import concurrent.futures
import hashlib
import html
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Query, Body
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import SolveConfig
from .geometry import inspect_step, load_domain, preview
from .antennas import build_antennas
from . import __version__
from .estimates import make_plan, estimate, read_json, write_json
from .resources import memory_policy, runtime_memory_status, cpu_policy

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("CAVITY_FEM_DATA", str(ROOT / "data"))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="Cavity Lab", version=__version__)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])
executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
geometry_lock = threading.Lock()
# State transitions and the child-process registry share one lock. RLock lets
# callers make a read/modify/write transition atomic through save_state().
state_lock = threading.RLock()
processes = {}


@app.middleware("http")
async def same_origin(request: Request, call_next):
    response = None
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        expected_origin = "%s://%s" % (request.url.scheme, request.headers.get("host", ""))
        if origin and origin != expected_origin:
            response = JSONResponse({"detail": "ローカル画面と同じオリジンから実行してください。"}, status_code=403)
    if response is None:
        response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    # This is a local app whose files may be replaced by a ZIP upgrade. Never
    # combine cached UI files or stale API state with a newer installation.
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def safe_id(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        raise HTTPException(404, "IDが見つかりません。")


def geometry_path(gid):
    path = DATA / "geometries" / safe_id(gid) / "input.step"
    if not path.is_file():
        raise HTTPException(404, "形状が見つかりません。")
    return path


def job_path(jid):
    path = DATA / "jobs" / safe_id(jid)
    if not (path / "state.json").is_file():
        raise HTTPException(404, "計算が見つかりません。")
    return path


def save_state(folder, **changes):
    with state_lock:
        path = folder / "state.json"
        state = json.loads(path.read_text()) if path.exists() else {}
        state.update(changes)
        temp = folder / "state.tmp"
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        temp.replace(path)
        return state


def describe_job(folder, include_result=True):
    with state_lock:
        state = json.loads((folder / "state.json").read_text())
    log_path = folder / "solver.log"
    state["log"] = log_path.read_text(errors="replace")[-20000:] if log_path.exists() else ""
    lines = [line for line in state["log"].splitlines() if line.strip()]
    if state["status"] == "running" and lines:
        state["stage"] = lines[-1]
    if include_result and state["status"] == "completed":
        state["result"] = json.loads((folder / "result.json").read_text())
    state["estimate"] = job_estimate(folder, state)
    return state


def job_estimate(folder, state):
    plan = read_json(folder/"estimate.json")
    if plan is None:
        plan = make_plan(state.get("config", {}))
    queued_wait = 0
    if state["status"] == "queued":
        for path in (DATA/"jobs").glob("*/state.json"):
            other = read_json(path, {})
            if other.get("id") == state["id"]:
                continue
            if other.get("status") == "running" or (other.get("status") == "queued" and other.get("created_at", 0) < state["created_at"]):
                previous_plan = read_json(path.parent/"estimate.json") or make_plan(other.get("config", {}))
                queued_wait += estimate(previous_plan, other, read_json(path.parent/"progress.json", {}))["remaining_s"]
    return estimate(plan, state, read_json(folder/"progress.json", {}), queue_wait=queued_wait)


def stop_worker(process):
    """Stop/reap only the captured child; concurrent cancellation may exit it."""
    if process is None:
        return None
    try:
        if process.poll() is None:
            process.kill()
    except ProcessLookupError:
        pass  # It exited after poll(), possibly through the cancel endpoint.
    try:
        return process.wait()
    except (ChildProcessError, ProcessLookupError):
        return process.returncode  # Another waiter already reaped this child.


def wait_for_worker(process, folder, policy, timeout=1800):
    """Monitor only this job's child; cancellation and terminal state remain atomic."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return process.wait(timeout=.5)
        except subprocess.TimeoutExpired:
            pass
        if time.monotonic() >= deadline:
            stop_worker(process)
            raise RuntimeError("30分の上限を超えました。メッシュを粗くして再試行してください。")
        live = runtime_memory_status(process.pid, policy['budget_bytes'], policy['reserve_bytes'])
        with state_lock:
            previous = read_json(folder/'state.json', {})
            running = previous.get('status') == 'running'
            if running:
                save_state(folder, memory={**policy, **live, 'sampled_at': time.time()})
        if not running:
            # A cancellation need not wait for a native routine to react to
            # terminate(). Its terminal state is preserved by run_worker().
            return stop_worker(process)
        if live.get('violated'):
            # Stop our worker immediately instead of waiting for a native allocation.
            stop_worker(process)
            with state_lock:
                if read_json(folder/'state.json', {}).get('status') == 'running':
                    save_state(folder, failure_kind='memory_limit')
            raise RuntimeError(live.get('reason') or "メモリの余裕を確保できないため計算を停止しました。")


def run_worker(folder, source):
    process = None
    with state_lock:
        if json.loads((folder / "state.json").read_text())["status"] != "queued":
            return
        save_state(folder, status="running", stage="計算を開始しています", started_at=time.time())
    try:
        config = SolveConfig(**read_json(folder/'config.json', {}))
        policy = memory_policy(config.model_dump())
        save_state(folder, memory=policy)
        if policy['status'] != 'ok':
            if not policy.get('detected'):
                raise RuntimeError("このPCのメモリ情報を取得できないため、計算を開始できません。起動ファイルを開き直して監視機能の導入を確認してください。")
            raise RuntimeError("計算用の空きメモリが不足しています。他のアプリや計算を終了して再試行してください。メッシュ設定欄にこのPCのメモリ量を表示しています。")
        with (folder / "solver.log").open("w") as log:
            # NGSolve receives the same count via SetNumThreads. Keep BLAS
            # serial so nested dense operations do not multiply worker counts.
            env = dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS=str(config.threads), PYTHONUNBUFFERED="1", PYTHONUTF8="1")
            env['CAVITY_FEM_MEMORY_BUDGET_BYTES'] = str(policy['budget_bytes'])
            # Cancellation either prevents spawning or sees the registered
            # child; it cannot fall into a gap between Popen and registration.
            with state_lock:
                if json.loads((folder / "state.json").read_text())["status"] != "running":
                    return
                process = subprocess.Popen([sys.executable, "-m", "cavity_fem.worker", "--input", str(source),
                                            "--config", str(folder/"config.json"), "--output", str(folder)],
                                           cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
                processes[folder.name] = process
            code = wait_for_worker(process, folder, policy)
        with state_lock:
            previous = json.loads((folder / "state.json").read_text())
            if previous["status"] != "running":
                return
            if code != 0 or not (folder / "result.json").is_file():
                failure = read_json(folder/'error.json', {})
                if failure.get('kind') == 'memory_limit' and isinstance(failure.get('message'), str):
                    captured = failure.get('policy')
                    captured = captured if isinstance(captured, dict) else {}
                    save_state(folder, failure_kind='memory_limit', memory={
                        **previous.get('memory', {}), **captured, 'violated': True,
                        'reason': failure['message'], 'sampled_at': failure.get('sampled_at') or time.time()})
                    raise RuntimeError(failure['message'])
                raise RuntimeError((folder/"solver.log").read_text(errors="replace")[-3500:])
            save_state(folder, status="completed", stage="計算が完了しました", finished_at=time.time())
    except Exception as exc:
        with state_lock:
            if json.loads((folder / "state.json").read_text())["status"] == "running":
                save_state(folder, status="failed", error=str(exc), stage="計算に失敗しました", finished_at=time.time())
    finally:
        # Watchdog/state-file errors must not orphan a live FEM process while
        # the single-worker executor advances to the next queued job.
        try:
            stop_worker(process)
        finally:
            with state_lock:
                if processes.get(folder.name) is process:
                    processes.pop(folder.name, None)


@app.get("/api/health")
def health():
    """Let the local launcher identify its own server before opening a browser."""
    return {"app": "cavity-lab-public", "status": "ok", "version": __version__,
            "instance_id": os.environ.get("CAVITY_FEM_INSTANCE_ID"),
            "project_root": str(ROOT.resolve()), "data_root": str(DATA),
            "development": False, "local_only": True}


@app.get("/api/info")
def info():
    import ngsolve
    return {"solver": "NGSolve", "version": ngsolve.__version__, "example_available": True,
            "local_only": True, "app_version": __version__,
            "memory": memory_policy(), "cpu": cpu_policy(),
            "sample": {"name": "Circular vacuum cavity", "geometry_mode": "vacuum_solid",
                       "radius_mm": 15, "height_mm": 20,
                       "provenance": "Generated from an analytic cylinder; no supplied CAD"}}


@app.get("/api/memory")
def inspect_memory(order: int = Query(2, ge=1, le=3), n_modes: int = Query(3, ge=1, le=12)):
    """Refresh physical memory independently of CAD and other input validity."""
    config = {'order': order, 'n_modes': n_modes}
    return {'memory': {**memory_policy(config), 'sampled_at': time.time()}, 'config': config}


@app.post("/api/estimate")
def estimate_analysis(config: SolveConfig):
    source = geometry_path(config.geometry_id) if config.geometry_id else None
    plan = make_plan(config, DATA, source)
    wait = 0
    for path in (DATA/"jobs").glob("*/state.json"):
        state = read_json(path, {})
        if state.get("status") in ("queued", "running"):
            old_plan = read_json(path.parent/"estimate.json") or make_plan(state.get("config", {}))
            wait += estimate(old_plan, state, read_json(path.parent/"progress.json", {}))["remaining_s"]
    return {"estimate": estimate(plan, queue_wait=wait), "memory": memory_policy(config)}


def import_geometry(content, filename, mode="auto"):
    if len(content) > 40*1024*1024:
        raise HTTPException(413, "STEPは40MB以下にしてください。")
    if b"ISO-10303-21" not in content[:512]:
        raise HTTPException(400, "STEP Part21形式ではありません。")
    gid = str(uuid.uuid4())
    folder = DATA/"geometries"/gid
    folder.mkdir(parents=True)
    path = folder / "input.step"
    path.write_bytes(content)
    (folder/"metadata.json").write_text(json.dumps({"filename": Path(filename).name}, ensure_ascii=False))
    try:
        with geometry_lock:
            details = inspect_step(path, mode)
    except Exception as exc:
        raise HTTPException(422, "STEPの解析領域を読み込めませんでした: %s" % exc)
    details.update(id=gid, filename=Path(filename).name)
    return details


@app.post("/api/inspect")
async def inspect(file: UploadFile = File(...), geometry_mode: str = Form("auto")):
    content = await file.read(40*1024*1024+1)
    return await __import__("asyncio").to_thread(import_geometry, content, file.filename or "input.step", geometry_mode)


@app.post("/api/example")
def example(selection: Optional[dict] = Body(default=None)):
    mode = (selection or {}).get("geometry_mode", "auto")
    return import_geometry((ROOT / "examples/cylinder-vacuum.step").read_bytes(), "cylinder-vacuum.step", mode)


@app.post("/api/geometries/{gid}/inspect")
def reinspect(gid: str, selection: dict):
    path = geometry_path(gid)
    try:
        with geometry_lock:
            details = inspect_step(path, selection.get("geometry_mode", "auto"), int(selection.get("solid_index", 0)))
        details.update(id=gid, filename=json.loads((path.parent/"metadata.json").read_text())["filename"])
        return details
    except Exception as exc:
        raise HTTPException(422, str(exc))


@app.post("/api/jobs")
def start_job(config: SolveConfig):
    if config.geometry_id is None:
        raise HTTPException(422, "STEPを先に読み込んでください。")
    source = geometry_path(config.geometry_id)
    with state_lock:
        active = [p for p in (DATA/"jobs").glob("*/state.json") if json.loads(p.read_text())["status"] in ("queued", "running")]
        if len(active) >= 4:
            raise HTTPException(429, "待機中を含め4件までです。完了をお待ちください。")
        jid = str(uuid.uuid4())
        folder = DATA / "jobs" / jid
        folder.mkdir(parents=True)
        (folder/"config.json").write_text(config.model_dump_json(indent=2))
        write_json(folder/"estimate.json", make_plan(config, DATA, source))
        metadata = read_json(source.parent/"metadata.json", {})
        save_state(folder, id=jid, status="queued", stage="待機中", created_at=time.time(), config=config.model_dump(), filename=metadata.get("filename"))
    executor.submit(run_worker, folder, source)
    return describe_job(folder)


@app.post("/api/geometries/{gid}/antennas")
def antenna_preview(gid: str, config: SolveConfig):
    path = geometry_path(gid)
    try:
        with geometry_lock:
            domain, details = load_domain(path, config.geometry_mode, config.solid_index)
            domain, details = build_antennas(domain, details, config.antennas,
                                            config.bulk_epsr, config.bulk_tand)
            details["preview"] = preview(domain)
        details.update(id=gid, filename=json.loads((path.parent/"metadata.json").read_text())["filename"])
        return details
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc))


@app.get("/api/jobs")
def list_jobs():
    return sorted([describe_job(p.parent, False) for p in (DATA/"jobs").glob("*/state.json")],
                  key=lambda j: j["created_at"], reverse=True)


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    return describe_job(job_path(jid))


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str):
    folder = job_path(jid)
    process = None
    with state_lock:
        state = json.loads((folder/"state.json").read_text())
        if state["status"] in ("queued", "running"):
            save_state(folder, status="cancelled", stage="キャンセルしました", finished_at=time.time())
            process = processes.get(folder.name)
    if process is not None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass  # The child may have exited between state update and signal.
    return describe_job(folder)


@app.get("/api/jobs/{jid}/result.json")
def download_result(jid: str):
    path = job_path(jid)/"result.json"
    if not path.exists():
        raise HTTPException(404, "計算結果はまだありません。")
    return FileResponse(path, media_type="application/json", filename="cavity-result.json")


@app.get("/api/jobs/{jid}/mode/{mode}.vtu")
def download_field(jid: str, mode: int):
    if mode < 1 or mode > 12:
        raise HTTPException(404)
    path = job_path(jid)/("mode_%02d.vtu" % mode)
    if not path.exists():
        raise HTTPException(404, "電磁界データがありません。")
    return FileResponse(path, filename=path.name)


@app.get("/api/jobs/{jid}/mode/{mode}/field.json")
def field_slice(jid: str, mode: int):
    if mode < 1 or mode > 12:
        raise HTTPException(404)
    path = job_path(jid)/("mode_%02d_field.json" % mode)
    if not path.is_file():
        raise HTTPException(404, "この計算には電場断面がありません。更新後の設定で再計算してください。")
    return FileResponse(path, media_type="application/json")


@app.get("/")
def index():
    directory = ROOT/"static"
    template = (directory/"index.html").read_text(encoding="utf-8")
    digest = hashlib.sha256(__version__.encode("utf-8"))
    # Hash bytes rather than mtime/size: ZIP extraction can preserve both even
    # when same-length content changes. One token identifies the complete UI.
    for name, content in (("index.html", template.encode("utf-8")),
                          ("style.css", (directory/"style.css").read_bytes()),
                          ("app.js", (directory/"app.js").read_bytes())):
        digest.update(name.encode("utf-8")+b"\0"+content+b"\0")
    asset_version = quote(__version__, safe="")+"-"+digest.hexdigest()[:16]
    content = template.replace("__ASSET_VERSION__", asset_version)
    content = content.replace("__APP_VERSION__", html.escape(__version__, quote=True))
    return HTMLResponse(content, headers={"X-App-Version": __version__, "X-Asset-Version": asset_version})


class UncachedStaticFiles(StaticFiles):
    def is_not_modified(self, response_headers, request_headers):
        # The default validator uses only mtime and file size. Ignore validators
        # left by an older release and send current bytes even for old URLs.
        return False


app.mount("/static", UncachedStaticFiles(directory=ROOT/"static"), name="static")


def main():
    import uvicorn
    # Interrupted native jobs from an earlier server instance must not appear live.
    for state_path in (DATA/"jobs").glob("*/state.json"):
        state = json.loads(state_path.read_text())
        if state["status"] in ("running", "queued"):
            save_state(state_path.parent, status="failed", stage="サーバー停止により中断", error="前回の計算は中断されました。再実行してください。")
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("CAVITY_FEM_PORT", "8768")))


if __name__ == "__main__":
    main()
