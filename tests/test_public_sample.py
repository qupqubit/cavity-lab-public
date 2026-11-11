"""The distributed example is elementary geometry with neutral material inputs."""
import json
import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cavity_fem import __version__, api
from cavity_fem.antennas import build_antennas
from cavity_fem.config import Antenna, SolveConfig
from cavity_fem.geometry import load_domain


SAMPLE = Path(__file__).resolve().parents[1]/'examples/cylinder-vacuum.step'


def test_sample_cylinder_has_analytic_volume_and_bbox():
    shape, info = load_domain(SAMPLE, 'vacuum_solid')
    assert shape.mass == pytest.approx(math.pi*15**2*20, rel=1e-12)
    assert len(info['faces']) == 3
    for actual, expected in zip(info['bbox_mm'], ([-15, -15, 0], [15, 15, 20])):
        assert actual == pytest.approx(expected, abs=1e-6)
    assert not any(face['artificial_cap'] for face in info['faces'])


def test_defaults_are_explicit_illustrative_loss_inputs():
    config = SolveConfig()
    assert config.geometry_mode == 'vacuum_solid'
    assert config.loss_model == 'surface_rs' and config.rs_nohm == 10
    assert (config.oxide_thickness_nm, config.oxide_epsr, config.oxide_tand) == (0, 1, 0)
    assert config.q_res is None
    antenna = Antenna(name='generic feed', face=1)
    assert (antenna.pin_radius_mm, antenna.coax_outer_radius_mm) == (.5, 1.67)
    assert antenna.dielectric_epsr == 2.1 and antenna.feed_length_mm == 3


@pytest.mark.parametrize('length', [0, 5])
def test_generic_antenna_volume_and_internal_interface(length):
    shape, info = load_domain(SAMPLE, 'vacuum_solid')
    top = next(face['id'] for face in info['faces'] if abs(face['center_mm'][2]-20) < 1e-6)
    antenna = Antenna(name='generic', face=top, length_mm=length)
    modified, details = build_antennas(shape, info, [antenna])
    exact = math.pi*15**2*20-math.pi*.5**2*length+math.pi*(1.67**2-.5**2)*3
    assert modified.mass == pytest.approx(exact, rel=1e-10)
    assert len(details['antenna_ports']) == 1
    assert details['internal_faces']
    assert not set(details['internal_faces']) & set(details['wall_faces'])
    assert not set(details['antenna_ports'][0]['faces']) & set(details['wall_faces'])
    expected_z = 376.730313668/(2*math.pi*math.sqrt(2.1))*math.log(1.67/.5)
    assert details['antenna_ports'][0]['reference_impedance_ohm'] == pytest.approx(expected_z)


def test_generic_antenna_rejects_through_wall_collision():
    shape, info = load_domain(SAMPLE, 'vacuum_solid')
    top = next(face['id'] for face in info['faces'] if abs(face['center_mm'][2]-20) < 1e-6)
    with pytest.raises(ValueError, match='衝突|接触'):
        build_antennas(shape, info, [Antenna(name='too long', face=top, length_mm=21)])


def test_public_api_sample_info_and_health(tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'DATA', tmp_path)
    with TestClient(api.app, base_url='http://127.0.0.1:8768') as client:
        health = client.get('/api/health').json()
        assert health['app'] == 'cavity-lab-public'
        assert health['version'] == __version__ == '0.1.3'
        assert health['development'] is False
        info = client.get('/api/info').json()
        assert set(info) == {'solver', 'version', 'example_available', 'local_only', 'app_version', 'sample', 'memory'}
        assert info['memory']['model']['swap_included'] is False
        sample = client.post('/api/example').json()
        assert sample['mode'] == 'vacuum_solid'
        assert sample['filename'] == 'cylinder-vacuum.step'
        assert sample['volume_mm3'] == pytest.approx(math.pi*15**2*20)
        assert sample['preview']['triangles']
        top = next(face['id'] for face in sample['faces'] if abs(face['center_mm'][2]-20) < 1e-6)
        response = client.post('/api/geometries/%s/antennas' % sample['id'],
                               json={'antennas': [{'name': 'generic', 'face': top, 'length_mm': 5}]})
        assert response.status_code == 200
        assert len(response.json()['antenna_ports']) == 1


def test_public_api_route_surface_is_explicit():
    allowed = {'/api/health', '/api/info', '/api/estimate', '/api/inspect', '/api/example',
               '/api/geometries/{gid}/inspect', '/api/geometries/{gid}/antennas', '/api/jobs',
               '/api/jobs/{jid}', '/api/jobs/{jid}/cancel', '/api/jobs/{jid}/result.json',
               '/api/jobs/{jid}/mode/{mode}.vtu', '/api/jobs/{jid}/mode/{mode}/field.json'}
    assert {route.path for route in api.app.routes if route.path.startswith('/api/')} == allowed


def test_saved_analytic_validation_has_no_local_paths():
    report = json.loads((SAMPLE.parent/'validation.json').read_text(encoding='utf-8'))
    assert report['relative_errors']['frequency'] < 2e-4
    assert report['field_export']['vtu_exists']
    assert report['field_export']['planes'] == 3
    assert '/Users/' not in json.dumps(report)
