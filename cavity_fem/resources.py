"""CPU defaults and physical-memory policy; no FEM imports are required.

The peak-memory curve is a deliberately conservative planning heuristic, not a
measurement or an upper bound on sparse factorization fill. Runtime monitoring
provides an additional stop condition, but cannot guarantee avoidance of OOM.
Swap is reported for context and never increases the permitted memory budget.
"""
import math
import os

try:
    import psutil
except ImportError:  # A missing monitor must not silently disable protection.
    psutil = None


GIB = 1024 ** 3
MAX_THREADS = 1024
CPU_TARGET_FRACTION = 0.8
MEMORY_BUDGET_ENV = "CAVITY_FEM_MEMORY_BUDGET_BYTES"
MODEL_VERSION = "physical-memory-heuristic-v1"
NOTE = ("疎行列のメモリ量を安全側に見積もる概算式です。実測値や厳密な上限ではなく、"
        "形状・行列の接続構造で必要量は変わります。監視してもメモリ不足を完全には防げません。"
        "スワップ容量は予算に含めません。")


def cpu_policy():
    """Return a local recommendation without changing process CPU affinity.

    Use the smallest available logical-count/affinity observation. Optional
    APIs are probed independently, keeping this compatible with Python 3.9 and
    platforms without affinity support. Physical cores are informational only.
    This sets a thread count, not a CPU-utilization limit or cgroup quota.
    """
    observations = {}

    def count(query):
        try:
            value = query()
            return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None
        except Exception:
            return None

    def affinity_count(query):
        def read():
            cpus = set(query())
            if any(not isinstance(cpu, int) or isinstance(cpu, bool) or cpu < 0 for cpu in cpus):
                return None
            return len(cpus)
        return count(read)

    logical = count(lambda: os.cpu_count())
    if logical is not None:
        observations['os.cpu_count'] = logical
    elif psutil is not None:
        logical = count(lambda: psutil.cpu_count(logical=True))
        if logical is not None:
            observations['psutil.cpu_count'] = logical

    available = count(lambda: os.process_cpu_count())
    if available is not None:
        observations['os.process_cpu_count'] = available
    affinity = affinity_count(lambda: os.sched_getaffinity(0))
    if affinity is not None:
        observations['os.sched_getaffinity'] = affinity
    physical = None
    if psutil is not None:
        physical = count(lambda: psutil.cpu_count(logical=False))
        affinity = affinity_count(lambda: psutil.Process(os.getpid()).cpu_affinity())
        if affinity is not None:
            observations['psutil.Process.cpu_affinity'] = affinity

    available = min(observations.values()) if observations else None
    detected = available is not None
    # Integer arithmetic implements floor(0.8*N) without float rounding.
    threads = min(MAX_THREADS, max(1, 4*available//5)) if detected else 1
    return {
        'detected': detected,
        'logical_cpus': logical,
        'physical_cores': physical,
        'available_cpus': available,
        'recommended_threads': threads,
        'target_fraction': CPU_TARGET_FRACTION,
        'max_threads': MAX_THREADS,
        'method': 'minimum_detected_cpu_limits' if detected else 'fallback_one_thread',
        'reason': ('検出できた論理CPU数と実行プロセスのCPU制限の最小値から、約80%を初期スレッド数にします。'
                   if detected else '利用可能なCPU数を取得できないため、初期値を1スレッドにします。'),
        'observations': observations,
        'note': 'スレッド数の目安です。CPU使用率を80%に制限する機能ではありません。手動指定と保存済みの値は変更しません。',
    }


def recommended_threads():
    """Evaluate at configuration creation, rather than at module import."""
    return cpu_policy()['recommended_threads']


class MemoryBudgetExceeded(RuntimeError):
    """The worker cannot safely proceed under its current memory policy."""

    def __init__(self, message, policy=None):
        super().__init__(message)
        self.policy = policy or {}


def _setting(cfg, name, default):
    if cfg is None:
        return default
    return cfg.get(name, default) if isinstance(cfg, dict) else getattr(cfg, name, default)


def _model(cfg=None):
    order = max(1, min(3, int(_setting(cfg, "order", 2))))
    modes = max(1, min(12, int(_setting(cfg, "n_modes", 3))))
    requested_eigenpairs = max(10, 2 * modes + 4)
    ncv = max(20, 2 * requested_eigenpairs + 1)
    # Reserve multiple complex companion-space work vectors even for the
    # simpler real closed-cavity path, so mode switches do not remove margin.
    vector_count = 3 * ncv + 2 * requested_eigenpairs + 16
    return {
        "version": MODEL_VERSION,
        "kind": "conservative heuristic, not measured",
        "baseline_bytes": int(.75 * GIB),
        "factorization_scale_bytes": int(1.5 * GIB),
        "reference_dofs": 100000,
        "dof_exponent": 1.5,
        "order": order,
        "order_factor": max(.75, min(1.5, order / 2)),
        "requested_modes": modes,
        "eigenpairs_reserved": requested_eigenpairs,
        "arpack_ncv_reserved": ncv,
        "workspace_vectors": vector_count,
        "companion_rows_per_dof": 2,
        "bytes_per_complex_value": 16,
        "swap_included": False,
    }


def memory_peak(ndof, cfg=None):
    """Estimated total worker peak RSS in bytes for a total HCurl DOF count."""
    if isinstance(ndof, bool) or int(ndof) != ndof or ndof < 0:
        raise ValueError("ndof must be a nonnegative integer")
    ndof = int(ndof)
    model = _model(cfg)
    factorization = (model["factorization_scale_bytes"] *
                     (ndof / model["reference_dofs"]) ** model["dof_exponent"] *
                     model["order_factor"])
    vectors = (ndof * model["companion_rows_per_dof"] *
               model["bytes_per_complex_value"] * model["workspace_vectors"])
    return math.ceil(model["baseline_bytes"] + factorization + vectors)


def _dof_limit(budget_bytes, cfg=None):
    if budget_bytes < memory_peak(1, cfg):
        return 0
    low, high = 0, 100000
    while memory_peak(high, cfg) <= budget_bytes:
        low, high = high, high * 2
    while high - low > 1:
        middle = (low + high) // 2
        if memory_peak(middle, cfg) <= budget_bytes:
            low = middle
        else:
            high = middle
    return low


def _snapshot():
    if psutil is None:
        return {"detected": False, "error": "メモリ監視ライブラリpsutilが見つかりません。"}
    try:
        vm = psutil.virtual_memory()
        data = {"detected": True, "total_bytes": int(vm.total),
                "available_bytes": int(vm.available)}
    except Exception as exc:
        return {"detected": False, "error": "物理メモリの状態を取得できません。",
                "error_type": type(exc).__name__}
    try:
        data["swap_used_bytes"] = int(psutil.swap_memory().used)
    except Exception:
        data["swap_used_bytes"] = None
    try:
        data["process_rss_bytes"] = int(psutil.Process(os.getpid()).memory_info().rss)
    except Exception:
        data["process_rss_bytes"] = None
    return data


def memory_policy(cfg=None, snapshot=None):
    """Return a JSON-safe policy for a newly starting worker.

    Optional snapshot keys are total_bytes, available_bytes, swap_used_bytes,
    process_rss_bytes and detected. total/available aliases aid integrations.
    The ordinary launch policy never adds the API process's RSS to availability.
    """
    source = _snapshot() if snapshot is None else dict(snapshot)
    result = {"detected": False, "status": "unavailable", "total_bytes": None,
              "available_bytes": None, "swap_used_bytes": source.get("swap_used_bytes"),
              "process_rss_bytes": source.get("process_rss_bytes"),
              "reserve_bytes": 0, "budget_bytes": 0, "dof_limit": 0,
              "estimated_peak_bytes": None, "model": _model(cfg), "note": NOTE, "reason": None}
    try:
        total = int(source.get("total_bytes", source.get("total", 0)))
        available = int(source.get("available_bytes", source.get("available", -1)))
        if source.get("detected", True) is False or total <= 0 or available < 0:
            raise ValueError("invalid memory snapshot")
    except (ValueError, TypeError, OverflowError):
        result["reason"] = source.get("error", "物理メモリの容量と空き容量を確認できません。")
        return result
    available = min(total, available)
    reserve = max(2 * GIB, math.ceil(.15 * total))
    budget = max(0, min(total // 2, math.floor(.65 * available), available - reserve))
    limit = _dof_limit(budget, cfg)
    result.update(detected=True, status="ok" if limit > 0 else "insufficient_memory",
                  total_bytes=total, available_bytes=available, reserve_bytes=reserve,
                  budget_bytes=budget, dof_limit=limit,
                  estimated_peak_bytes=memory_peak(limit, cfg) if limit else None)
    if not limit:
        result["reason"] = "PCに残す空き容量を確保すると、解析を開始するためのメモリが不足します。"
    return result


def _failure_message(policy, cfg):
    if not policy["detected"]:
        return ("物理メモリの空き容量を取得できないため、安全のため計算を停止しました。"
                "起動環境のpsutilとメモリ監視を確認してください。")
    if policy.get("status") == "invalid_inherited_budget":
        return "起動時のメモリ予算が不正なため、安全のため計算を停止しました。アプリを再起動してください。"
    order = int(_setting(cfg, "order", 2))
    mesh_advice = ("まず要素次数を{before}から{after}に下げて試してください。"
                   "最大要素サイズを大きくする方法もあります。".format(before=order, after=order-1)
                   if order > 1 else "最大要素サイズを大きくして再実行してください。")
    return (
        "現在のメモリ予算で計算を続行できません。"
        "総自由度 {actual:,}、このPCの自動上限 {limit:,}、予算 {budget:.2f} GiB、"
        "推定ピーク {peak:.2f} GiB（最大要素サイズ {h} mm、次数 {order}）。"
        "物理メモリ {total:.2f} GiB、現在の空き {available:.2f} GiB、"
        "PCに残す空き容量 {reserve:.2f} GiB。"
        "{mesh_advice}"
        "アンテナ付近や細い隙間の局所メッシュは粗くなりにくい場合があります。"
        "他のアプリや計算を終了してから新しい計算を開始すると、空き容量に応じて予算を再計算します。"
        "予算が増える場合もありますが、物理メモリの半分を超えることはありません。"
        "モード数・スレッド数・周波数点数を減らしてもメッシュ自由度は減りません。"
        "粗い設定は試行用とし、最終的な周波数・Qcはメッシュ収束を確認してください。"
    ).format(actual=policy["actual_dofs"], limit=policy["dof_limit"],
             budget=policy["budget_bytes"] / GIB,
             peak=policy["estimated_peak_bytes"] / GIB,
             total=policy["total_bytes"] / GIB,
             available=policy["available_bytes"] / GIB,
             reserve=policy["reserve_bytes"] / GIB, mesh_advice=mesh_advice,
             h=_setting(cfg, "maxh_mm", "不明"), order=order)


def check_mesh_memory(ndof, cfg, progress=lambda _: None):
    """Check the mesh against a peak budget, counting its existing RSS only once.

    A parent-provided launch budget can only tighten the current ceiling. The
    worker's already allocated RSS is added back to availability for the total
    peak estimate; unrelated processes' memory is never added back.
    """
    peak = memory_peak(ndof, cfg)
    snapshot = _snapshot()
    current = memory_policy(cfg, snapshot)
    rss = snapshot.get("process_rss_bytes")
    if not current["detected"] or rss is None or rss < 0:
        current.update(detected=False, status="unavailable", actual_dofs=int(ndof),
                       estimated_peak_bytes=peak, budget_bytes=0, dof_limit=0)
        raise MemoryBudgetExceeded(_failure_message(current, cfg), current)
    raw_available = current["available_bytes"]
    # The peak budget covers the complete worker, including the mesh already
    # in memory. Adding only this worker's RSS back prevents double counting.
    adjusted = dict(snapshot, available_bytes=min(current["total_bytes"], raw_available + rss))
    policy = memory_policy(cfg, adjusted)
    policy.update(available_bytes=raw_available, worker_rss_bytes=rss,
                  budget_available_bytes=adjusted["available_bytes"], actual_dofs=int(ndof))
    inherited = os.environ.get(MEMORY_BUDGET_ENV)
    if inherited is not None:
        try:
            inherited_budget = int(inherited)
            if inherited_budget < 0:
                raise ValueError("negative budget")
        except (ValueError, TypeError):
            policy.update(status="invalid_inherited_budget", budget_bytes=0, dof_limit=0,
                          estimated_peak_bytes=peak)
            raise MemoryBudgetExceeded(_failure_message(policy, cfg), policy)
        policy["inherited_budget_bytes"] = inherited_budget
        policy["budget_bytes"] = min(policy["budget_bytes"], inherited_budget)
    policy["dof_limit"] = _dof_limit(policy["budget_bytes"], cfg)
    policy["estimated_peak_bytes"] = peak
    policy["estimated_additional_bytes"] = max(0, peak - rss)
    # The runtime check uses the same reserve; do not proceed when other apps
    # have consumed it, even if the worker itself is still below its budget.
    over = peak > policy["budget_bytes"] or rss > policy["budget_bytes"]
    low = raw_available < policy["reserve_bytes"]
    policy["status"] = "system_reserve_low" if low else "budget_exceeded" if over else "ok"
    progress("メモリ確認: 総自由度 {:,} / 自動上限 {:,}、予算 {:.2f} GiB、推定ピーク {:.2f} GiB".format(
        int(ndof), policy["dof_limit"], policy["budget_bytes"] / GIB, peak / GIB))
    if over or low:
        message = _failure_message(policy, cfg)
        if low:
            message = ("他のアプリ用に確保する空きメモリが不足したため停止しました。"
                       "現在の空き {:.2f} GiB、確保する空き {:.2f} GiB。".format(
                           raw_available / GIB, policy["reserve_bytes"] / GIB)) + message
        raise MemoryBudgetExceeded(message, policy)
    return policy


def runtime_memory_status(pid, budget_bytes, reserve_bytes):
    """Inspect one registered worker and descendants; never stop any process.

    Summed RSS can count shared pages more than once, deliberately erring on the
    conservative side. Child processes that exit during sampling are ignored.
    An exited root worker is not a violation; unreadable live state fails closed.
    """
    policy = memory_policy()
    result = {"detected": policy["detected"], "status": "unavailable", "violated": True,
              "reason": "物理メモリの状態を取得できないため、安全のため計算を停止します。", "pid": pid,
              "worker_rss_bytes": None, "process_count": 0,
              "budget_bytes": budget_bytes, "reserve_bytes": reserve_bytes,
              "total_bytes": policy["total_bytes"], "available_bytes": policy["available_bytes"],
              "swap_used_bytes": policy["swap_used_bytes"]}
    try:
        budget_bytes, reserve_bytes = int(budget_bytes), int(reserve_bytes)
        if budget_bytes <= 0 or reserve_bytes < 0:
            raise ValueError("invalid budget")
    except (TypeError, ValueError, OverflowError):
        result.update(status="invalid_budget", reason="メモリ予算の設定が不正なため、安全のため計算を停止します。")
        return result
    result.update(budget_bytes=budget_bytes, reserve_bytes=reserve_bytes)
    if not policy["detected"] or psutil is None:
        return result
    # A caller cannot use an oversized budget or undersized reserve to disable
    # the machine-wide floor. Available-memory policy is enforced separately.
    budget_bytes = min(budget_bytes, policy["total_bytes"] // 2)
    reserve_bytes = max(reserve_bytes, policy["reserve_bytes"])
    result.update(budget_bytes=budget_bytes, reserve_bytes=reserve_bytes)
    try:
        process = psutil.Process(int(pid))
        root_rss = int(process.memory_info().rss)
        children = process.children(recursive=True)
    except psutil.NoSuchProcess:
        result.update(status="worker_exited", violated=False, reason=None)
        return result
    except Exception as exc:
        result["reason"] = "計算プロセスの使用メモリを取得できないため、安全のため計算を停止します。"
        result["error_type"] = type(exc).__name__
        return result
    rss, count, seen = root_rss, 1, {process.pid}
    for child in children:
        if child.pid in seen:
            continue
        seen.add(child.pid)
        try:
            rss += int(child.memory_info().rss)
            count += 1
        except psutil.NoSuchProcess:
            continue
        except Exception as exc:
            result["reason"] = "計算の子プロセスの使用メモリを取得できないため、安全のため計算を停止します。"
            result["error_type"] = type(exc).__name__
            return result
    if rss < 0:
        result["reason"] = "計算プロセスの使用メモリを確認できないため、安全のため計算を停止します。"
        return result
    result.update(worker_rss_bytes=rss, process_count=count)
    if rss > budget_bytes:
        result.update(status="budget_exceeded", reason=(
            "計算プロセスの使用メモリ {:.2f} GiBが予算 {:.2f} GiBを超えたため停止します。"
            "最大要素サイズを大きくするか、要素次数を下げて再実行してください。"
        ).format(rss / GIB, budget_bytes / GIB))
    elif policy["available_bytes"] < reserve_bytes:
        result.update(status="system_reserve_low", reason=(
            "PCの空きメモリ {:.2f} GiBが確保する空き容量 {:.2f} GiBを下回ったため停止します。"
            "他のアプリを閉じてから再実行するか、メッシュを粗くしてください。"
        ).format(policy["available_bytes"] / GIB, reserve_bytes / GIB))
    else:
        result.update(status="ok", violated=False, reason=None)
    return result
