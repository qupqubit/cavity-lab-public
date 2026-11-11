"""Portable electric-field slices from the actual finite-element solution."""
import json
from pathlib import Path
import numpy as np


def export_field_slices(mesh, field, geometry, output_dir, mode, resolution=41):
    """Sample three central planes, masking points outside the electromagnetic domain."""
    bounds = np.asarray(geometry['bbox_mm'], dtype=float)
    center = bounds.mean(axis=0)
    planes = []
    maximum = 0.0
    for label, fixed, horizontal, vertical in [('xy', 2, 0, 1), ('xz', 1, 0, 2), ('yz', 0, 1, 2)]:
        us = np.linspace(bounds[0, horizontal], bounds[1, horizontal], resolution)
        vs = np.linspace(bounds[0, vertical], bounds[1, vertical], resolution)
        magnitudes, real, imag = [], [], []
        for vv in vs:
            for uu in us:
                point = center.copy(); point[horizontal] = uu; point[vertical] = vv
                try:
                    meshpoint = mesh(*point)
                    if meshpoint.nr < 0:
                        raise ValueError('outside mesh')
                    value = np.asarray(field(meshpoint), dtype=complex)
                    magnitude = float(np.linalg.norm(value))
                    if not np.isfinite(magnitude):
                        raise ValueError('nonfinite sample')
                    maximum = max(maximum, magnitude)
                    magnitudes.append(magnitude); real.append(value.real.tolist()); imag.append(value.imag.tolist())
                except (ValueError, RuntimeError, TypeError):
                    magnitudes.append(None); real.append(None); imag.append(None)
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
