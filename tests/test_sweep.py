"""Sweep orchestration preserves the source config and reports invalid CAD."""
from pathlib import Path
import json

from cavity_fem.config import Antenna, AntennaSweep, SolveConfig
from cavity_fem import worker


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
