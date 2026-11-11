"""Portable electric-field slices from the actual finite-element solution."""
import json
from pathlib import Path
import numpy as np


def _sample_points(mesh, field, points):
    """Use NGSolve's native batch evaluator, preserving per-point failure masks.

    With the caller's TaskManager active, NGSolve evaluates these MeshPoints
    through its native ParallelFor and task-local heaps. Never pass outside
    points to the complex evaluator: its native path does not check nr == -1.
    """
    values = np.full((len(points), 3), np.nan + 0j, dtype=complex)
    try:
        meshpoints = mesh(*points.T)
        indices = np.flatnonzero(meshpoints['nr'] >= 0)
        if len(indices):
            samples = np.asarray(field(meshpoints[indices]), dtype=complex)
            if samples.shape != (len(indices), 3):
                raise ValueError('unexpected electric-field dimensions')
            values[indices] = samples
        return values
    except (ValueError, RuntimeError, TypeError):
        # Some coefficient functions do not implement batch evaluation. Keep
        # valid samples if one such evaluation fails, as the scalar path did.
        for index, point in enumerate(points):
            try:
                meshpoint = mesh(*point)
                if meshpoint.nr >= 0:
                    values[index] = np.asarray(field(meshpoint), dtype=complex)
            except (ValueError, RuntimeError, TypeError):
                pass
        return values


def export_field_slices(mesh, field, geometry, output_dir, mode, resolution=41):
    """Sample three central planes, masking points outside the electromagnetic domain."""
    bounds = np.asarray(geometry['bbox_mm'], dtype=float)
    center = bounds.mean(axis=0)
    planes = []
    maximum = 0.0
    for label, fixed, horizontal, vertical in [('xy', 2, 0, 1), ('xz', 1, 0, 2), ('yz', 0, 1, 2)]:
        us = np.linspace(bounds[0, horizontal], bounds[1, horizontal], resolution)
        vs = np.linspace(bounds[0, vertical], bounds[1, vertical], resolution)
        # Row-major order: u changes fastest, exactly as in the scalar export.
        points = np.repeat(center[None, :], len(us) * len(vs), axis=0)
        points[:, horizontal] = np.tile(us, len(vs))
        points[:, vertical] = np.repeat(vs, len(us))
        values = _sample_points(mesh, field, points)
        norms = np.linalg.norm(values, axis=1)
        valid = np.isfinite(norms)
        if np.any(valid):
            maximum = max(maximum, float(norms[valid].max()))
        magnitudes = [float(value) if ok else None for value, ok in zip(norms, valid)]
        real = [value.tolist() if ok else None for value, ok in zip(values.real, valid)]
        imag = [value.tolist() if ok else None for value, ok in zip(values.imag, valid)]
        planes.append({'plane': label, 'coordinate_mm': float(center[fixed]), 'u_mm': us.tolist(), 'v_mm': vs.tolist(),
                       'magnitude': magnitudes, 'e_real': real, 'e_imag': imag,
                       'units': 'relative E, common maximum = 1'})
    scale = maximum or 1.0
    for plane in planes:
        plane['magnitude'] = [value / scale if value is not None else None for value in plane['magnitude']]
        for key in ('e_real', 'e_imag'):
            plane[key] = [[x/scale for x in value] if value is not None else None for value in plane[key]]
    result = {'mode': mode, 'planes': planes,
              'normalization': {'kind': 'maximum over all three sampled planes', 'scale_before_normalization': scale,
                                'note': 'Eigenmode amplitude and complex phase are arbitrary; no applied power is implied.'}}
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    filename = 'mode_%02d_field.json' % mode
    (out / filename).write_text(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return filename
