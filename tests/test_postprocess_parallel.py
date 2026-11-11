"""Native postprocessing preserves physics and phasor fields across thread counts.

Every geometry is generated here. The single TM-like mode is isolated so a
degenerate eigenspace rotation cannot be confused with a parallelization error.
"""
import json
from pathlib import Path

import ngsolve as ng
import numpy as np
import pytest
from netgen.occ import Cylinder, Pnt, Vec

from cavity_fem.config import Antenna, SolveConfig
from cavity_fem.solver import solve


def field_vectors(document):
    vectors, magnitudes, masks = [], [], []
    for plane in document['planes']:
        valid = np.asarray([value is not None for value in plane['magnitude']])
        masks.append(valid)
        real = np.asarray([value for value, keep in zip(plane['e_real'], valid) if keep])
        imag = np.asarray([value for value, keep in zip(plane['e_imag'], valid) if keep])
        vectors.append(real+1j*imag)
        magnitudes.extend(value for value in plane['magnitude'] if value is not None)
    return np.concatenate(vectors), np.asarray(magnitudes), masks


@pytest.mark.parametrize('with_coax', [False, True], ids=['closed-target', 'coax-target'])
def test_one_and_two_threads_preserve_quality_factors_and_exported_fields(tmp_path, with_coax):
    records = []
    try:
        for threads in (1, 2):
            shape = Cylinder(Pnt(0, 0, 0), Vec(0, 0, 1), 8, 5)
            top = next(index+1 for index, face in enumerate(shape.faces)
                       if abs(face.center[2]-5) < 1e-6)
            antennas = [Antenna(name='generic feed', face=top, length_mm=2,
                               pin_radius_mm=.5, coax_outer_radius_mm=1.5,
                               feed_length_mm=2, dielectric_epsr=2.1,
                               dielectric_tand=2e-6)] if with_coax else []
            config = SolveConfig(maxh_mm=4, order=2, n_modes=1, target_ghz=14,
                                 threads=threads, loss_model='surface_rs', rs_nohm=25,
                                 oxide_thickness_nm=3, oxide_epsr=5, oxide_tand=.002,
                                 field_resolution=11, antennas=antennas)
            directory = tmp_path/('threads-%d' % threads)
            result = solve(None, config, output_dir=directory, domain_override=shape,
                           progress=lambda _: None)
            mode = result['modes'][0]
            assert mode['residual'] < 1e-7
            assert mode['qi'] > 0 and mode['participation']['oxide'] > 0
            fields = json.loads((directory/mode['field_file']).read_text(encoding='utf-8'))
            vtu = directory/'mode_01.vtu'
            assert vtu.is_file()
            header = vtu.read_bytes().split(b'<AppendedData', 1)[0]
            for field in (b'E_real', b'E_imag', b'E_magnitude', b'H_magnitude'):
                assert field in header
            records.append((result, fields))
    finally:
        ng.SetNumThreads(1)

    (serial, serial_fields), (parallel, parallel_fields) = records
    assert serial['mesh'] == parallel['mesh']
    for result, count in ((serial, 1), (parallel, 2)):
        metadata = result['machine']['postprocess_parallelism']
        assert metadata['task_manager_enabled'] is True
        assert metadata['requested_threads'] == count
        assert metadata['python_loop_parallel'] is False
    one, two = serial['modes'][0], parallel['modes'][0]
    for key in ('frequency_ghz', 'qi', 'q_loaded', 'geometry_factor_ohm',
                'surface_electric_per_m', 'electric_energy_j_normalized',
                'magnetic_energy_j_normalized', 'cavity_energy_j_normalized'):
        assert two[key] == pytest.approx(one[key], rel=2e-7)
    assert set(one['participation']) == set(two['participation'])
    for key in one['participation']:
        assert two['participation'][key] == pytest.approx(one['participation'][key], rel=2e-7, abs=1e-18)
    assert len(one['surface_participation']) == len(two['surface_participation'])
    for first, second in zip(one['surface_participation'], two['surface_participation']):
        assert first['face'] == second['face']
        assert second['p_oxide'] == pytest.approx(first['p_oxide'], rel=2e-7, abs=1e-18)
    assert len(one['ports']) == len(two['ports']) == int(with_coax)
    if with_coax:
        assert one['ports'][0]['qc'] > 0
        for key in ('qc', 'qc_cavity', 'qc_total_energy', 'power_w_normalized'):
            assert two['ports'][0][key] == pytest.approx(one['ports'][0][key], rel=2e-7)

    reference, reference_magnitude, reference_masks = field_vectors(serial_fields)
    candidate, candidate_magnitude, candidate_masks = field_vectors(parallel_fields)
    for first, second in zip(reference_masks, candidate_masks):
        np.testing.assert_array_equal(first, second)
    np.testing.assert_allclose(candidate_magnitude, reference_magnitude, rtol=2e-7, atol=1e-8)
    # E has one arbitrary global complex phase, shared across every sample.
    overlap = np.vdot(candidate, reference)
    assert abs(overlap) > 0
    phase = overlap/abs(overlap)
    np.testing.assert_allclose(candidate*phase, reference, rtol=2e-7, atol=1e-8)
