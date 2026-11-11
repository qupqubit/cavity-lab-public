"""Generated hollow metal cavity: automatic cap selection and power accounting."""
import math

import pytest
from netgen.occ import Box, Cylinder, Pnt, Vec

from cavity_fem.antennas import build_antennas
from cavity_fem.boundaries import assign_external_boundaries
from cavity_fem.config import Antenna, Port, SolveConfig
from cavity_fem.geometry import load_domain
from cavity_fem.solver import solve


@pytest.fixture(scope='module')
def metal_cavity(tmp_path_factory):
    # A 10 x 8 x 12 mm cavity in a 2 mm metal shell, with two circular
    # axial openings. Everything is generated here; no supplied CAD is used.
    metal = (Box(Pnt(0, 0, 0), Pnt(14, 12, 16))
             - Box(Pnt(2, 2, 2), Pnt(12, 10, 14))
             - Cylinder(Pnt(7, 6, -1), Vec(0, 0, 1), 1.5, 18))
    path = tmp_path_factory.mktemp('automatic-caps')/'hollow-cavity.step'
    metal.WriteStep(str(path))
    return path


def loaded_cavity(path):
    shape, geometry = load_domain(path, 'metal_void')
    caps = sorted(face['id'] for face in geometry['faces'] if face['artificial_cap'])
    assert len(caps) == 2
    return shape, geometry, caps


def test_legacy_default_keeps_caps_closed(metal_cavity):
    _, geometry, caps = loaded_cavity(metal_cavity)
    cfg = SolveConfig(geometry_mode='metal_void')
    assert cfg.all_artificial_caps is False
    assert cfg.excluded_artificial_cap_faces == []
    result = assign_external_boundaries(geometry, cfg)
    assert result['external_ports'] == []
    assert set(caps) <= set(result['wall_faces'])
    assert result['external_boundary_selection']['method'] == 'explicit_only'


@pytest.mark.parametrize('excluded', [[0], [-1], [1, 1], [1.5]])
def test_exclusions_are_positive_unique_integer_ids(excluded):
    with pytest.raises(ValueError):
        SolveConfig(excluded_artificial_cap_faces=excluded)


@pytest.mark.parametrize('enabled', [False, True])
def test_non_cap_or_missing_exclusions_are_rejected(metal_cavity, enabled):
    _, geometry, caps = loaded_cavity(metal_cavity)
    wall = next(face['id'] for face in geometry['faces'] if face['id'] not in caps)
    for invalid in (wall, max(face['id'] for face in geometry['faces'])+1):
        cfg = SolveConfig(all_artificial_caps=enabled, excluded_artificial_cap_faces=[invalid])
        with pytest.raises(ValueError, match='人工キャップではありません'):
            assign_external_boundaries(geometry, cfg)


def test_excluded_cap_remains_pec_and_other_cap_opens(metal_cavity):
    _, geometry, caps = loaded_cavity(metal_cavity)
    cfg = SolveConfig(all_artificial_caps=True, analysis_mode='all_openings_qc',
                      excluded_artificial_cap_faces=[caps[0]])
    result = assign_external_boundaries(geometry, cfg)
    assert caps[0] in result['wall_faces'] and caps[0] in result['oxide_wall_faces']
    assert caps[1] not in result['wall_faces'] and caps[1] not in result['oxide_wall_faces']
    assert len(result['external_ports']) == 1
    port = result['external_ports'][0]
    assert port['source_faces'] == [caps[1]] and port['kind'] == 'radiation'
    assert port['radiation_epsr'] == 1 and port['pml'] is False
    selection = result['external_boundary_selection']
    assert selection['automatic_source_face_ids'] == [caps[1]]
    assert selection['excluded_artificial_cap_faces'] == [caps[0]]
    assert selection['remaining_artificial_caps_pec'] == [caps[0]]
    assert selection['method'] == 'unassigned_artificial_caps'
    assert selection['all_physical_openings_identified'] is False
    # Assignment must not alter the original geometry or replayable config.
    assert 'external_ports' not in geometry and cfg.ports == []


def test_explicit_override_wins_and_automatic_names_remain_unique(metal_cavity):
    _, geometry, caps = loaded_cavity(metal_cavity)
    name = 'opening_face_%d' % caps[1]
    cfg = SolveConfig(all_artificial_caps=True, excluded_artificial_cap_faces=[caps[0]],
                      ports=[Port(name=name, faces=[caps[0]], impedance_ohm=1234)])
    result = assign_external_boundaries(geometry, cfg)
    manual, automatic = result['external_ports']
    assert manual['selection'] == 'explicit' and manual['impedance_ohm'] == 1234
    assert manual['faces'] == [caps[0]]
    assert automatic['name'] == name+'_'
    assert automatic['source_faces'] == [caps[1]]
    assert result['external_boundary_selection']['remaining_artificial_caps_pec'] == []
    assert result['external_boundary_selection']['explicit_source_face_ids'] == [caps[0]]


def test_all_excluded_without_any_port_has_clear_error(metal_cavity):
    _, geometry, caps = loaded_cavity(metal_cavity)
    cfg = SolveConfig(all_artificial_caps=True, analysis_mode='all_openings_qc',
                      excluded_artificial_cap_faces=caps)
    with pytest.raises(ValueError, match='1つ以上'):
        assign_external_boundaries(geometry, cfg)


def test_antenna_source_skipped_and_remaining_cap_resolved_to_generated_faces(metal_cavity):
    shape, geometry, caps = loaded_cavity(metal_cavity)
    antenna = Antenna(name='opening_face_%d' % caps[1], face=caps[0], length_mm=3,
                      pin_radius_mm=.4, coax_outer_radius_mm=1.1, feed_length_mm=2)
    _, rebuilt = build_antennas(shape, geometry, [antenna])
    cfg = SolveConfig(all_artificial_caps=True, antennas=[antenna], analysis_mode='all_openings_qc')
    result = assign_external_boundaries(rebuilt, cfg)
    assert len(result['antenna_ports']) == 1 and len(result['external_ports']) == 1
    opening = result['external_ports'][0]
    assert opening['name'] == antenna.name+'_'
    assert opening['source_faces'] == [caps[1]]
    expected = {face['id'] for face in rebuilt['faces'] if face.get('source_face') == caps[1]
                and not face.get('internal') and face.get('category') not in ('interface', 'terminal', 'antenna_metal')}
    assert expected and set(opening['faces']) == expected
    terminals = {face for port in result['antenna_ports'] for face in port['faces']}
    assert not set(opening['faces']) & terminals
    assert not set(result['wall_faces']) & (terminals | set(result['internal_faces']) | expected)
    assert not set(result['oxide_wall_faces']) & (terminals | set(result['internal_faces']) | expected)
    assert result['external_boundary_selection']['automatic_source_face_ids'] == [caps[1]]
    assert result['external_boundary_selection']['artificial_cap_candidates'] == caps


def test_resolved_external_ports_dispatch_to_advanced_solver(metal_cavity, monkeypatch):
    import cavity_fem.spectrum as spectrum
    _, _, caps = loaded_cavity(metal_cavity)
    observed = []
    def selected(domain, geometry, config, *args):
        observed.extend(geometry['external_ports'])
        return {'advanced': True}
    monkeypatch.setattr(spectrum, 'solve_advanced', selected)
    cfg = SolveConfig(geometry_mode='metal_void', ports=[Port(name='manual', faces=[caps[0]])])
    assert solve(metal_cavity, cfg, progress=lambda _: None) == {'advanced': True}
    assert observed[0]['source_faces'] == [caps[0]]


def test_automatic_and_manual_two_openings_have_same_fem_energy_and_power(metal_cavity):
    _, _, caps = loaded_cavity(metal_cavity)
    common = dict(geometry_mode='metal_void', analysis_mode='all_openings_qc',
                  maxh_mm=3, order=2, n_modes=1, target_ghz=19.5, threads=1,
                  loss_model='none', oxide_thickness_nm=3, oxide_epsr=5)
    progress = []
    automatic = solve(metal_cavity, SolveConfig(all_artificial_caps=True, **common), progress=progress.append)
    manual = solve(metal_cavity, SolveConfig(ports=[Port(name='opening_face_%d' % face, faces=[face],
                                                      kind='radiation') for face in caps], **common),
                   progress=lambda _: None)
    first, second = automatic['modes'][0], manual['modes'][0]
    for field in ('frequency_ghz', 'frequency_imag_ghz', 'cavity_energy_j_normalized',
                  'total_energy_j_normalized', 'outgoing_power_w_normalized', 'q_external_cavity',
                  'q_external_total_energy', 'q_ports_eigenvalue', 'surface_electric_per_m'):
        assert first[field] == pytest.approx(second[field], rel=1e-8)
    omega = 2*math.pi*first['frequency_ghz']*1e9
    assert len(first['ports']) == 2
    for port, reference in zip(first['ports'], second['ports']):
        assert port['power_w_normalized'] > 0
        assert port['qc_status'] == 'finite'
        assert port['qc'] == pytest.approx(reference['qc'], rel=1e-8)
        assert port['qc'] == pytest.approx(omega*first['cavity_energy_j_normalized']/port['power_w_normalized'])
    assert first['q_external_cavity'] == pytest.approx(1/sum(1/port['qc'] for port in first['ports']))
    assert first['q_external_total_energy'] == pytest.approx(first['q_ports_eigenvalue'], rel=1e-6)
    assert first['residual'] < 1e-8
    assert all(face['p_oxide'] == 0 for face in first['surface_participation'] if face['is_port'])
    assert sum(face['p_oxide'] for face in first['surface_participation']) == pytest.approx(first['participation']['oxide'])
    assert automatic['geometry']['external_boundary_selection']['automatic_source_face_ids'] == caps
    assert manual['geometry']['external_boundary_selection']['automatic_source_face_ids'] == []
    assert 'spectrum' not in automatic and 'spectrum' not in manual
    assert any('人工キャップから自動指定' in entry for entry in progress)
    assert any('S11は実行しません' in entry for entry in progress)
