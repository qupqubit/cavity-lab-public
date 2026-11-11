"""Sweep orchestration preserves the source config and reports invalid CAD."""
from pathlib import Path
import json
import sys

import pytest

from cavity_fem.config import Antenna, AntennaSweep, SolveConfig
from cavity_fem import worker
from cavity_fem.resources import MemoryBudgetExceeded


def test_sweep_tracks_target_branch_and_retains_geometry_failures(tmp_path, monkeypatch):
    calls = []
    def fake_solve(path, cfg, output, progress):
        length = cfg.antennas[0].length_mm
        calls.append((length, cfg.spectrum_enabled, cfg.antenna_sweep))
        if length == 40:
            raise ValueError("pin intersects wall")
        return {"config": cfg.model_dump(), "modes": [
            {"mode": 1, "frequency_ghz": 6, "qi": 1000, "ports": [{"name": "A1", "qc": 900}]},
            {"mode": 2, "frequency_ghz": 8+length*.01, "qi": 1000, "ports": [{"name": "A1", "qc": 100/(1+length)}]},
        ], "timings": {"total_s": 0}}
    monkeypatch.setattr(worker, "solve", fake_solve)
    cfg = SolveConfig(target_ghz=8, mode_search="target", spectrum_enabled=True,
                      antennas=[Antenna(name="A1", face=1, length_mm=5)],
                      antenna_sweep=AntennaSweep(antenna="A1", lengths_mm=[0, 5, 40]))
    result = worker.run_analysis("source.step", cfg, tmp_path, progress=lambda _: None)
    sweep = result["antenna_sweep"]
    assert sweep["reference_mode"] == 2
    assert [row["status"] for row in sweep["rows"]] == ["completed", "completed", "invalid_geometry"]
    assert [row["mode"] for row in sweep["rows"][:2]] == [2, 2]
    assert len(calls) == 3  # The original length reuses the full main result.
    assert calls[1][1:] == (False, None)  # No accidental recursive or spectral sweep.
    assert cfg.antennas[0].length_mm == 5
    assert cfg.spectrum_enabled is True
    saved = json.loads(Path(tmp_path, "result.json").read_text())
    assert saved["config"]["antennas"][0]["length_mm"] == 5
    assert "including_sweep_s" in saved["timings"]


def test_sweep_memory_limit_propagates_and_stops_remaining_lengths(tmp_path, monkeypatch):
    calls = []
    failure = MemoryBudgetExceeded("メモリ予算を超えました。", {"budget_bytes": 1024})

    def fake_solve(path, cfg, output, progress):
        length = cfg.antennas[0].length_mm
        calls.append(length)
        if length == 0:
            raise failure
        return {"config": cfg.model_dump(), "modes": [
            {"mode": 1, "frequency_ghz": 8, "qi": 1000,
             "ports": [{"name": "A1", "qc": 900}]}], "timings": {"total_s": 0}}

    monkeypatch.setattr(worker, "solve", fake_solve)
    cfg = SolveConfig(antennas=[Antenna(name="A1", face=1, length_mm=5)],
                      antenna_sweep=AntennaSweep(antenna="A1", lengths_mm=[0, 5, 8]))
    with pytest.raises(MemoryBudgetExceeded) as error:
        worker.run_analysis("source.step", cfg, tmp_path, progress=lambda _: None)
    assert error.value is failure
    assert calls == [5, 0]  # The failed point is not converted to invalid_geometry.
    assert cfg.antennas[0].length_mm == 5
    assert not (tmp_path / "result.json").exists()


def test_worker_main_records_memory_error_without_traceback(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"order": 2}), encoding="utf-8")
    output = tmp_path / "job"
    message = "総自由度 700,000 がメモリ予算に基づく上限を超えました。"
    policy = {"actual_dofs": 700000, "budget_bytes": 3 * 1024 ** 3,
              "estimated_peak_bytes": 20 * 1024 ** 3, "dof_limit": 100000}
    calls = []

    def fail_analysis(path, cfg, folder, progress):
        calls.append((path, cfg, folder))
        raise MemoryBudgetExceeded(message, policy)

    monkeypatch.setattr(worker, "run_analysis", fail_analysis)
    monkeypatch.setattr(sys, "argv", ["worker", "--input", "source.step",
                                     "--config", str(config_path), "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        worker.main()
    assert error.value.code == 2
    assert calls == [("source.step", {"order": 2}, str(output))]
    recorded = json.loads((output / "error.json").read_text(encoding="utf-8"))
    assert recorded == {"kind": "memory_limit", "message": message,
                        "policy": policy, "sampled_at": recorded["sampled_at"]}
    assert recorded["sampled_at"] > 0
    captured = capsys.readouterr()
    assert captured.out == message + "\n"
    assert captured.err == ""
    assert "Traceback" not in captured.out + captured.err
