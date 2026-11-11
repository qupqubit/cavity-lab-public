"""Field slices retain scalar sampling semantics with native batch evaluation."""
import json

import ngsolve as ng
import numpy as np
import pytest
from netgen.occ import Box, Cylinder, Glue, OCCGeometry, Pnt, Vec

from cavity_fem.fields import export_field_slices


@pytest.fixture(scope='module', autouse=True)
def field_threads():
    # NGSolve 6.2.2506 has no thread-count getter; leave the test suite at one.
    ng.SetNumThreads(2)
    try:
        yield
    finally:
        ng.SetNumThreads(1)


@pytest.fixture(scope='module')
def cavity_field():
    # Synthetic two-material cavity with a through-hole; no imported user CAD.
    hole = Cylinder(Pnt(0, 0, -1.2), Vec(0, 0, 1), .23, 2.4)
    left = Box(Pnt(-1, -1, -1), Pnt(0, 1, 1)) - hole
    right = Box(Pnt(0, -1, -1), Pnt(1, 1, 1)) - hole
    left.mat('vacuum')
    right.mat('dielectric')
    mesh = ng.Mesh(OCCGeometry(Glue([left, right])).GenerateMesh(maxh=.4))
    mesh.Curve(3)
    material = mesh.MaterialCF({'vacuum': 1, 'dielectric': 2})
    expression = ng.CF((material * (1 + ng.x + 1j * ng.y),
                        ng.y + 2j * ng.z, ng.z - 1j * ng.x))
    field = ng.GridFunction(ng.HCurl(mesh, order=2, complex=True))
    field.Set(expression)
    return mesh, field


def export(tmp_path, mesh, field, bounds=None, mode=2, resolution=13):
    geometry = {'bbox_mm': bounds or [[-1.2, -1.2, -1.2], [1.2, 1.2, 1.2]]}
    with ng.TaskManager():
        filename = export_field_slices(mesh, field, geometry, tmp_path, mode, resolution)
    assert filename == 'mode_%02d_field.json' % mode
    text = (tmp_path / filename).read_text()
    assert 'NaN' not in text and 'Infinity' not in text
    result = json.loads(text)
    assert set(result) == {'mode', 'planes', 'normalization'}
    assert result['mode'] == mode
    assert [plane['plane'] for plane in result['planes']] == ['xy', 'xz', 'yz']
    return result


def assert_scalar_reference(result, mesh, field):
    """Independent original point-at-a-time evaluation, including its mask."""
    samples = []
    max_norm = 0.0
    for plane, axes in zip(result['planes'], [(2, 0, 1), (1, 0, 2), (0, 1, 2)]):
        assert set(plane) == {'plane', 'coordinate_mm', 'u_mm', 'v_mm',
                              'magnitude', 'e_real', 'e_imag', 'units'}
        fixed, horizontal, vertical = axes
        rows = []
        for v in plane['v_mm']:
            for u in plane['u_mm']:
                point = np.zeros(3)
                point[fixed], point[horizontal], point[vertical] = plane['coordinate_mm'], u, v
                try:
                    mp = mesh(*point)
                    if mp.nr < 0:
                        raise ValueError('outside')
                    value = np.asarray(field(mp), dtype=complex)
                    norm = float(np.linalg.norm(value))
                    if not np.isfinite(norm):
                        raise ValueError('nonfinite')
                    rows.append(value)
                    max_norm = max(max_norm, norm)
                except (ValueError, RuntimeError, TypeError):
                    rows.append(None)
        samples.append(rows)
    scale = max_norm or 1.0
    assert result['normalization'] == {
        'kind': 'maximum over all three sampled planes',
        'scale_before_normalization': pytest.approx(scale, rel=2e-12),
        'note': 'Eigenmode amplitude and complex phase are arbitrary; no applied power is implied.'}
    for plane, rows in zip(result['planes'], samples):
        assert plane['units'] == 'relative E, common maximum = 1'
        assert len(plane['magnitude']) == len(rows)
        for index, expected in enumerate(rows):
            if expected is None:
                assert plane['magnitude'][index] is None
                assert plane['e_real'][index] is None
                assert plane['e_imag'][index] is None
            else:
                actual = (np.asarray(plane['e_real'][index])
                          + 1j * np.asarray(plane['e_imag'][index]))
                np.testing.assert_allclose(actual, expected / scale, rtol=2e-11, atol=2e-12)
                assert plane['magnitude'][index] == pytest.approx(
                    np.linalg.norm(expected) / scale, rel=2e-11, abs=2e-12)


def test_complex_hollow_two_material_cavity_matches_scalar(tmp_path, cavity_field):
    mesh, field = cavity_field
    calls = []

    def checked_batch(points):
        # Invalid meshpoints would be unsafe in the native complex evaluator.
        assert isinstance(points, np.ndarray)
        assert np.all(points['nr'] >= 0)
        calls.append(len(points))
        return field(points)

    result = export(tmp_path, mesh, checked_batch)
    assert len(calls) == 3
    assert all(0 < count < 13 * 13 for count in calls)
    assert_scalar_reference(result, mesh, field)
    for plane in result['planes']:
        assert plane['magnitude'][6 * 13 + 6] is None  # Through-hole center.
        assert plane['magnitude'][0] is None  # Beyond the cavity exterior.


def test_complex_phase_and_common_normalization_are_preserved(tmp_path, cavity_field):
    mesh, field = cavity_field
    original = export(tmp_path, mesh, field, mode=1)
    phase = .6 + .8j
    rotated = export(tmp_path, mesh, phase * field, mode=2)
    assert_scalar_reference(rotated, mesh, phase * field)
    assert rotated['normalization']['scale_before_normalization'] == pytest.approx(
        original['normalization']['scale_before_normalization'], rel=2e-12)
    for first, second in zip(original['planes'], rotated['planes']):
        valid = np.array([value is not None for value in first['magnitude']])
        assert valid.tolist() == [value is not None for value in second['magnitude']]
        f = np.array([r for r in first['e_real'] if r is not None]) + 1j * np.array([
            r for r in first['e_imag'] if r is not None])
        s = np.array([r for r in second['e_real'] if r is not None]) + 1j * np.array([
            r for r in second['e_imag'] if r is not None])
        np.testing.assert_allclose(s, phase * f, rtol=2e-11, atol=2e-12)


@pytest.mark.parametrize('bad', [float('nan'), float('inf')])
def test_nonfinite_samples_are_masked_individually(tmp_path, cavity_field, bad):
    mesh, _ = cavity_field
    field = ng.CF((ng.IfPos(ng.x - .3, bad, 1), 1j * ng.y, ng.z))
    with np.errstate(invalid='ignore'):
        result = export(tmp_path, mesh, field)
        assert_scalar_reference(result, mesh, field)
    xy = result['planes'][0]
    assert any(value is not None for value in xy['magnitude'])
    assert xy['magnitude'][6 * 13 + 10] is None  # In-domain nonfinite x=.8.


def test_batch_failure_falls_back_without_losing_valid_samples(tmp_path, cavity_field):
    mesh, native_field = cavity_field

    def scalar_only_field(points):
        if isinstance(points, np.ndarray):
            raise TypeError('This coefficient function only accepts scalar points')
        if ng.x(points) > .5:
            raise RuntimeError('A single sample cannot be evaluated')
        return native_field(points)

    result = export(tmp_path, mesh, scalar_only_field)
    assert_scalar_reference(result, mesh, scalar_only_field)


@pytest.mark.parametrize('all_outside', [False, True])
def test_zero_field_and_empty_planes_keep_safe_unit_scale(tmp_path, cavity_field, all_outside):
    mesh, _ = cavity_field
    bounds = [[2, 2, 2], [3, 3, 3]] if all_outside else None
    field = ng.CF((0j, 0j, 0j))
    result = export(tmp_path, mesh, field, bounds=bounds)
    assert_scalar_reference(result, mesh, field)
    assert result['normalization']['scale_before_normalization'] == 1.0
    valid = [value for plane in result['planes'] for value in plane['magnitude'] if value is not None]
    if all_outside:
        assert valid == []
    else:
        assert valid and all(value == 0 for value in valid)
