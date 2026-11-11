"""Isolated worker. A failed native mesh never takes down the browser server."""
import argparse
import json
import time
from pathlib import Path
from .solver import solve
from .config import SolveConfig
from .estimates import ProgressTracker
from .resources import MemoryBudgetExceeded


def run_analysis(path, config, output, progress=print):
    cfg = config if isinstance(config, SolveConfig) else SolveConfig(**config)
    started = time.perf_counter()
    tracker = ProgressTracker(output, cfg, progress)
    tracker.start_block()
    result = solve(path, cfg, output, progress=tracker)
    tracker.finish_block()
    if cfg.antenna_sweep is None:
        return result
    name = cfg.antenna_sweep.antenna
    rows = []
    reference_mode = (result["modes"][0] if cfg.mode_search == "lowest" else
                      min(result["modes"], key=lambda m: abs(m["frequency_ghz"]-cfg.target_ghz)))
    previous_frequency = reference_mode["frequency_ghz"]
    original_length = next(a.length_mm for a in cfg.antennas if a.name == name)
    for index, length in enumerate(sorted(cfg.antenna_sweep.lengths_mm)):
        progress("アンテナ挿入長スイープ %d/%d：%s = %g mm" % (index+1, len(cfg.antenna_sweep.lengths_mm), name, length))
        subdir = "sweep_%02d" % index
        if length == original_length:
            current, subdir = result, None
        else:
            data = cfg.model_dump()
            data.update(antenna_sweep=None, spectrum_enabled=False,
                        target_ghz=previous_frequency, mode_search="target")
            for antenna in data["antennas"]:
                if antenna["name"] == name:
                    antenna["length_mm"] = length
            try:
                tracker.start_block()
                current = solve(path, SolveConfig(**data), Path(output)/subdir, progress=tracker)
            except ValueError as exc:
                rows.append({"length_mm": length, "status": "invalid_geometry", "error": str(exc)})
                continue
            finally:
                tracker.finish_block()
        mode = min(current["modes"], key=lambda item: abs(item["frequency_ghz"]-previous_frequency))
        previous_frequency = mode["frequency_ghz"]
        port = next((p for p in mode["ports"] if p["name"] == name), {})
        rows.append({"length_mm": length, "status": "completed", "frequency_ghz": previous_frequency,
                     "qi": mode["qi"], "qc": port.get("qc"), "ports": mode["ports"],
                     "mode": mode["mode"], "job_subdir": subdir})
    result["antenna_sweep"] = {"antenna": name, "rows": rows,
        "reference_mode": reference_mode["mode"],
        "mode_tracking": "nearest_previous_frequency",
        "warnings": ["各長さで形状・メッシュ・固有場を再計算します。前の周波数に最も近いモードを追跡するため、交差・強い混成では電場を確認してください。",
                     "広帯域S11と主画面の電場は基本設定の挿入長に対応します。スイープ各点ではQcと固有モードを計算します。"]}
    result["timings"]["including_sweep_s"] = time.perf_counter()-started
    Path(output, "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    cfg = json.loads(Path(args.config).read_text())
    try:
        run_analysis(args.input, cfg, args.output, progress=lambda text: print(text, flush=True))
    except MemoryBudgetExceeded as exc:
        folder = Path(args.output)
        folder.mkdir(parents=True, exist_ok=True)
        (folder/'error.json').write_text(json.dumps(
            {'kind': 'memory_limit', 'message': str(exc), 'policy': exc.policy,
             'sampled_at': time.time()}, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        print(str(exc), flush=True)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
