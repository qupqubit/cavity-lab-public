"""Memory policies use synthetic machine states; no FEM solve is needed."""
import json
from types import SimpleNamespace

import pytest

from cavity_fem import resources as r


def snapshot(total=32, available=24, rss=.5, swap=0):
    return {"detected": True, "total_bytes": int(total * r.GIB),
            "available_bytes": int(available * r.GIB),
            "process_rss_bytes": int(rss * r.GIB), "swap_used_bytes": int(swap * r.GIB)}


@pytest.fixture(autouse=True)
def isolated_budget(monkeypatch):
    monkeypatch.delenv(r.MEMORY_BUDGET_ENV, raising=False)


@pytest.mark.parametrize("total,available", [(8, 4), (16, 8), (32, 24), (128, 100)])
def test_budget_uses_physical_and_available_memory(total, available):
    p = r.memory_policy(snapshot=snapshot(total, available))
    reserve = max(2 * r.GIB, __import__('math').ceil(.15 * total * r.GIB))
    expected = max(0, min(total * r.GIB // 2, int(.65 * available * r.GIB),
                          available * r.GIB - reserve))
    assert p["detected"] and p["reserve_bytes"] == reserve
    assert p["budget_bytes"] == expected
    assert p["dof_limit"] > 0
    assert r.memory_peak(p["dof_limit"]) <= expected
    assert r.memory_peak(p["dof_limit"] + 1) > expected
    json.dumps(p, allow_nan=False)


def test_swap_never_expands_budget():
    p = r.memory_policy(snapshot=snapshot(swap=0))
    q = r.memory_policy(snapshot=snapshot(swap=1024))
    assert p["budget_bytes"] == q["budget_bytes"]
    assert p["dof_limit"] == q["dof_limit"]
    assert q["swap_used_bytes"] == 1024 * r.GIB


@pytest.mark.parametrize("state", [{}, {"total_bytes": None, "available_bytes": 4},
                                  {"detected": False, "total": 8*r.GIB, "available": 4*r.GIB},
                                  {"total": 8*r.GIB, "available": -1}])
def test_unknown_memory_fails_closed(state):
    p = r.memory_policy(snapshot=state)
    assert not p["detected"] and p["status"] == "unavailable"
    assert p["dof_limit"] == p["budget_bytes"] == 0


def test_low_memory_is_detected_but_cannot_start():
    p = r.memory_policy(snapshot=snapshot(total=8, available=1))
    assert p["detected"] and p["status"] == "insufficient_memory"
    assert p["budget_bytes"] == p["dof_limit"] == 0


def test_missing_monitor_fails_closed(monkeypatch):
    monkeypatch.setattr(r, "psutil", None)
    p = r.memory_policy()
    assert not p["detected"] and p["budget_bytes"] == 0
    with pytest.raises(r.MemoryBudgetExceeded, match="取得できない"):
        r.check_mesh_memory(100, {"order": 1})


def test_physical_memory_query_failure_fails_closed(monkeypatch):
    def unavailable():
        raise PermissionError()
    monkeypatch.setattr(r, "psutil", SimpleNamespace(virtual_memory=unavailable))
    p = r.memory_policy()
    assert p["status"] == "unavailable" and p["budget_bytes"] == 0
    assert "取得できません" in p["reason"]


def test_unreadable_swap_is_unknown_and_never_blocks_physical_budget(monkeypatch):
    def swap_unavailable():
        raise OSError()
    fake = SimpleNamespace(virtual_memory=lambda: SimpleNamespace(total=32*r.GIB, available=24*r.GIB),
                           swap_memory=swap_unavailable,
                           Process=lambda pid: SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=r.GIB)))
    monkeypatch.setattr(r, "psutil", fake)
    p = r.memory_policy()
    assert p["status"] == "ok" and p["swap_used_bytes"] is None
    assert p["budget_bytes"] == r.memory_policy(snapshot=snapshot())["budget_bytes"]


def test_memory_curve_monotone_and_settings_are_distinguished():
    cfg = {"order": 2, "n_modes": 3}
    assert r.memory_peak(50000, cfg) < r.memory_peak(100000, cfg) < r.memory_peak(200000, cfg)
    assert r.memory_peak(100000, {"order": 1}) < r.memory_peak(100000, {"order": 2})
    assert r.memory_peak(100000, {"order": 2}) < r.memory_peak(100000, {"order": 3})
    assert r.memory_peak(100000, {"n_modes": 1}) < r.memory_peak(100000, {"n_modes": 12})
    # These settings affect execution or sampling, not this peak-space model.
    assert r.memory_peak(100000, cfg) == r.memory_peak(100000, dict(cfg, threads=16,
        spectrum_points=401, field_resolution=101, maxh_mm=.1))
    assert r.memory_peak(0, cfg) == int(.75 * r.GIB)
    with pytest.raises(ValueError):
        r.memory_peak(-1)
    with pytest.raises(ValueError):
        r.memory_peak(1.5)


def test_worker_counts_own_existing_rss_once_and_reports(monkeypatch):
    # The worker started with 20 GiB available; 4 GiB is now its own RSS.
    monkeypatch.setattr(r, "_snapshot", lambda: snapshot(total=32, available=16, rss=4))
    start_budget = r.memory_policy(snapshot=snapshot(total=32, available=20))["budget_bytes"]
    monkeypatch.setenv(r.MEMORY_BUDGET_ENV, str(start_budget))
    messages = []
    p = r.check_mesh_memory(100000, {"order": 2}, messages.append)
    assert p["budget_bytes"] == start_budget
    assert p["available_bytes"] == 16 * r.GIB
    assert p["budget_available_bytes"] == 20 * r.GIB
    assert p["worker_rss_bytes"] == 4 * r.GIB
    assert p["estimated_additional_bytes"] == 0
    assert p["status"] == "ok" and "100,000" in messages[0]


def test_inherited_budget_can_only_tighten_current_ceiling(monkeypatch):
    state = snapshot(total=32, available=12, rss=1)
    monkeypatch.setattr(r, "_snapshot", lambda: state)
    adjusted_ceiling = r.memory_policy(snapshot=dict(state, available_bytes=13*r.GIB))["budget_bytes"]
    monkeypatch.setenv(r.MEMORY_BUDGET_ENV, str(100*r.GIB))
    assert r.check_mesh_memory(10000, {})["budget_bytes"] == adjusted_ceiling
    monkeypatch.setenv(r.MEMORY_BUDGET_ENV, str(2*r.GIB))
    assert r.check_mesh_memory(10000, {})["budget_bytes"] == 2*r.GIB


@pytest.mark.parametrize("budget", ["bad", "-1", "1.5"])
def test_invalid_inherited_budget_fails_closed(monkeypatch, budget):
    monkeypatch.setattr(r, "_snapshot", snapshot)
    monkeypatch.setenv(r.MEMORY_BUDGET_ENV, budget)
    with pytest.raises(r.MemoryBudgetExceeded) as error:
        r.check_mesh_memory(100, {})
    assert error.value.policy["status"] == "invalid_inherited_budget"
    assert error.value.policy["budget_bytes"] == 0


def test_worker_rejection_has_counts_parameters_and_heuristic(monkeypatch):
    monkeypatch.setattr(r, "_snapshot", lambda: snapshot(total=16, available=8, rss=.5))
    with pytest.raises(r.MemoryBudgetExceeded) as error:
        r.check_mesh_memory(700000, {"order": 3, "maxh_mm": .8})
    p = error.value.policy
    assert p["status"] == "budget_exceeded" and p["actual_dofs"] == 700000
    assert p["estimated_peak_bytes"] > p["budget_bytes"]
    assert "700,000" in str(error.value) and "0.8 mm" in str(error.value)
    assert "次数 3" in str(error.value) and "推定" in str(error.value)
    assert "物理メモリ 16.00 GiB" in str(error.value)
    assert "現在の空き 8.00 GiB" in str(error.value)
    assert "PCに残す空き容量 2.40 GiB" in str(error.value)
    assert "次数を3から2" in str(error.value)
    assert "新しい計算を開始" in str(error.value)
    assert "物理メモリの半分を超えることはありません" in str(error.value)


def test_failure_at_order_one_recommends_larger_elements_not_order_zero(monkeypatch):
    monkeypatch.setattr(r, "_snapshot", lambda: snapshot(total=16, available=8, rss=.5))
    with pytest.raises(r.MemoryBudgetExceeded) as error:
        r.check_mesh_memory(700000, {"order": 1, "maxh_mm": 2})
    assert "最大要素サイズを大きくして" in str(error.value)
    assert "次数を1から0" not in str(error.value)


def test_inherited_budget_recomputes_limit_for_actual_order(monkeypatch):
    monkeypatch.setattr(r, "_snapshot", lambda: snapshot(total=32, available=24, rss=.5))
    budget = 3*r.GIB
    monkeypatch.setenv(r.MEMORY_BUDGET_ENV, str(budget))
    with pytest.raises(r.MemoryBudgetExceeded) as error:
        r.check_mesh_memory(190000, {"order": 2, "n_modes": 3, "maxh_mm": 2})
    failed = error.value.policy
    assert failed["budget_bytes"] == budget
    assert r.memory_peak(failed["dof_limit"], {"order": 2}) <= budget
    assert r.memory_peak(failed["dof_limit"]+1, {"order": 2}) > budget
    recovered = r.check_mesh_memory(50000, {"order": 1, "n_modes": 3, "maxh_mm": 2})
    assert recovered["budget_bytes"] == budget
    assert recovered["dof_limit"] > failed["dof_limit"]
    assert recovered["estimated_peak_bytes"] < budget


def test_current_reserve_and_missing_rss_fail_closed(monkeypatch):
    monkeypatch.setattr(r, "_snapshot", lambda: snapshot(total=16, available=2, rss=4))
    with pytest.raises(r.MemoryBudgetExceeded) as error:
        r.check_mesh_memory(100, {})
    assert error.value.policy["status"] == "system_reserve_low"
    monkeypatch.setattr(r, "_snapshot", lambda: dict(snapshot(), process_rss_bytes=None))
    with pytest.raises(r.MemoryBudgetExceeded) as error:
        r.check_mesh_memory(100, {})
    assert not error.value.policy["detected"]


class Gone(Exception):
    pass


class FakeProcess:
    def __init__(self, pid, rss, children=(), failure=None):
        self.pid, self.rss, self._children, self.failure = pid, rss, children, failure

    def memory_info(self):
        if self.failure:
            raise self.failure
        return SimpleNamespace(rss=self.rss)

    def children(self, recursive):
        assert recursive
        return list(self._children)


def monitor(monkeypatch, root, state=None):
    def process(pid):
        assert pid == 123
        if isinstance(root, Exception):
            raise root
        return root
    monkeypatch.setattr(r, "_snapshot", lambda: snapshot() if state is None else state)
    monkeypatch.setattr(r, "psutil", SimpleNamespace(Process=process, NoSuchProcess=Gone))


def test_runtime_counts_descendants_once_and_ignores_exited_child(monkeypatch):
    child = FakeProcess(124, r.GIB)
    gone = FakeProcess(125, 0, failure=Gone())
    monitor(monkeypatch, FakeProcess(123, 2*r.GIB, [child, child, gone]))
    p = r.runtime_memory_status(123, 4*r.GIB, 2*r.GIB)
    assert not p["violated"] and p["status"] == "ok"
    assert p["worker_rss_bytes"] == 3*r.GIB and p["process_count"] == 2
    assert p["reserve_bytes"] >= .15 * 32*r.GIB
    json.dumps(p, allow_nan=False)


def test_runtime_stops_when_worker_exceeds_budget(monkeypatch):
    monitor(monkeypatch, FakeProcess(123, 6*r.GIB))
    p = r.runtime_memory_status(123, 4*r.GIB, 2*r.GIB)
    assert p["violated"] and p["status"] == "budget_exceeded"


def test_runtime_stops_if_other_apps_consume_reserve(monkeypatch):
    monitor(monkeypatch, FakeProcess(123, r.GIB), snapshot(total=32, available=3))
    p = r.runtime_memory_status(123, 8*r.GIB, 2*r.GIB)
    assert p["violated"] and p["status"] == "system_reserve_low"


def test_runtime_cannot_raise_physical_cap_or_drop_reserve(monkeypatch):
    monitor(monkeypatch, FakeProcess(123, 17*r.GIB))
    p = r.runtime_memory_status(123, 100*r.GIB, 0)
    assert p["violated"] and p["budget_bytes"] == 16*r.GIB


def test_runtime_exit_is_not_a_violation(monkeypatch):
    monitor(monkeypatch, Gone())
    p = r.runtime_memory_status(123, 4*r.GIB, 2*r.GIB)
    assert not p["violated"] and p["status"] == "worker_exited"


@pytest.mark.parametrize("root", [PermissionError(), FakeProcess(123, r.GIB,
    [FakeProcess(124, 0, failure=PermissionError())])])
def test_runtime_unreadable_process_fails_closed(monkeypatch, root):
    monitor(monkeypatch, root)
    p = r.runtime_memory_status(123, 4*r.GIB, 2*r.GIB)
    assert p["violated"] and p["status"] == "unavailable"


def test_runtime_unknown_memory_and_invalid_budget_fail_closed(monkeypatch):
    monitor(monkeypatch, FakeProcess(123, r.GIB), {"detected": False})
    assert r.runtime_memory_status(123, 4*r.GIB, 2*r.GIB)["violated"]
    assert r.runtime_memory_status(123, 0, 2*r.GIB)["status"] == "invalid_budget"
