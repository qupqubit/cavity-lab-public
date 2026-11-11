"""Physical invariants and independent analytic cavity references."""
import math
from pathlib import Path

import pytest
from netgen.occ import Box, Cylinder, Pnt, Vec

from cavity_fem.config import Port, SolveConfig
from cavity_fem.solver import C0, MU0, loss_budget, solve


@pytest.fixture(scope="module")
def cylinder_vacuum():
    return solve(None, SolveConfig(maxh_mm=4, order=3, n_modes=1,
                                  target_ghz=7.7, threads=1, loss_model="none", oxide_thickness_nm=3, oxide_epsr=5),
                 domain_override=Cylinder(Pnt(0, 0, 0), Vec(0, 0, 1), 15, 20),
                 progress=lambda _: None)


def test_cylindrical_tm010_frequency_geometry_and_surface(cylinder_vacuum):
    radius, height = .015, .020
    bessel_j0_first_zero = 2.404825557695773
    frequency = C0 * bessel_j0_first_zero / (2*math.pi*radius)
    geometry_factor = 2*math.pi*frequency*MU0*radius*height/(2*(radius+height))
    surface_electric = 2/height
    mode = cylinder_vacuum["modes"][0]
    assert mode["frequency_ghz"] * 1e9 == pytest.approx(frequency, rel=1e-4)
    assert mode["geometry_factor_ohm"] == pytest.approx(geometry_factor, rel=.001)
    assert mode["surface_electric_per_m"] == pytest.approx(surface_electric, rel=.0005)
    assert mode["participation"]["oxide"] == pytest.approx(3e-9/5*surface_electric, rel=.0005)
    assert mode["residual"] < 1e-8
    assert mode["energy_balance_relative"] < 1e-8
    assert mode["qi"] is None
    assert mode["q_loaded"] is None
    assert mode["ports"] == []


def test_homogeneous_permittivity_scaling(cylinder_vacuum):
    result = solve(None, SolveConfig(maxh_mm=4, order=3, n_modes=1,
                                    target_ghz=3.85, bulk_epsr=4,
                                    threads=1, loss_model="none", oxide_thickness_nm=3, oxide_epsr=5),
                   domain_override=Cylinder(Pnt(0, 0, 0), Vec(0, 0, 1), 15, 20),
                   progress=lambda _: None)
    vacuum, dielectric = cylinder_vacuum["modes"][0], result["modes"][0]
    assert dielectric["frequency_ghz"] == pytest.approx(vacuum["frequency_ghz"]/2, rel=1e-8)
    assert dielectric["geometry_factor_ohm"] == pytest.approx(vacuum["geometry_factor_ohm"]/2, rel=1e-7)
    assert dielectric["surface_electric_per_m"] == pytest.approx(vacuum["surface_electric_per_m"], rel=1e-7)
    assert dielectric["participation"]["oxide"] == pytest.approx(vacuum["participation"]["oxide"]*4, rel=1e-7)


def test_resistive_port_decay_agrees_with_integrated_power():
    shape = Box(Pnt(0, 0, 0), Pnt(10, 8, 12))
    face_id = next(i+1 for i, face in enumerate(shape.faces) if abs(face.center[2]-12) < 1e-6)
    config = SolveConfig(maxh_mm=4, order=2, n_modes=1, target_ghz=16.2,
                         threads=1, loss_model="none",
                         ports=[Port(name="termination", faces=[face_id], impedance_ohm=1e6)])
    result = solve(None, config, domain_override=shape, progress=lambda _: None)
    mode = result["modes"][0]
    expected_near_pmc = C0/2 * math.sqrt(1/.010**2 + 1/(2*.012)**2)
    assert mode["frequency_ghz"]*1e9 == pytest.approx(expected_near_pmc, rel=.02)
    qc = mode["ports"][0]["qc"]
    assert qc > 0
    assert mode["frequency_imag_ghz"] > 0
    assert mode["q_ports_eigenvalue"] == pytest.approx(qc, rel=1e-6)
    assert mode["q_loaded"] == pytest.approx(qc, rel=1e-10)
    assert mode["energy_balance_relative"] < 1e-8
    assert mode["residual"] < 1e-8
    assert sum(face["p_oxide"] for face in mode["surface_participation"]) == pytest.approx(mode["participation"]["oxide"], rel=1e-12)
    assert all(face["p_oxide"] == 0 for face in mode["surface_participation"] if face["is_port"])


def test_two_independent_ports_have_additive_inverse_quality_factors():
    shape = Box(Pnt(0, 0, 0), Pnt(10, 8, 12))
    lower = next(i+1 for i, face in enumerate(shape.faces) if abs(face.center[2]) < 1e-6)
    upper = next(i+1 for i, face in enumerate(shape.faces) if abs(face.center[2]-12) < 1e-6)
    config = SolveConfig(maxh_mm=4, order=2, n_modes=1, target_ghz=15,
                         threads=1, loss_model="none",
                         ports=[Port(name="lower", faces=[lower], impedance_ohm=1e6),
                                Port(name="upper", faces=[upper], impedance_ohm=2e6)])
    result = solve(None, config, domain_override=shape, progress=lambda _: None)
    mode = result["modes"][0]
    ports = mode["ports"]
    assert len(ports) == 2
    assert all(port["qc"] > 0 for port in ports)
    total_external_q = 1 / sum(1/port["qc"] for port in ports)
    assert mode["q_ports_eigenvalue"] == pytest.approx(total_external_q, rel=1e-6)
    assert mode["q_loaded"] == pytest.approx(total_external_q, rel=1e-10)
    assert sum(face["p_oxide"] for face in mode["surface_participation"]) == pytest.approx(mode["participation"]["oxide"], rel=1e-12)


def test_multiple_pec_modes_exclude_gradient_kernel_far_below_band():
    sides = (.010, .008, .012)
    exact = sorted(C0/2 * math.sqrt(sum((index/length)**2 for index, length in zip(indices, sides))) / 1e9
                   for indices in ((1, 0, 1), (0, 1, 1), (1, 1, 0)))
    result = solve(None, SolveConfig(maxh_mm=4, order=2, n_modes=3,
                                    target_ghz=1, loss_model="none", threads=1),
                   domain_override=Box(Pnt(0, 0, 0), Pnt(10, 8, 12)), progress=lambda _: None)
    assert len(result["modes"]) == 3
    for mode, reference in zip(result["modes"], exact):
        assert mode["frequency_ghz"] == pytest.approx(reference, rel=.012)
        assert mode["residual"] < 1e-8


def test_multiple_complex_port_modes_preserve_full_pencil_and_power():
    shape = Box(Pnt(0, 0, 0), Pnt(10, 8, 12))
    upper = next(i+1 for i, face in enumerate(shape.faces) if abs(face.center[2]-12) < 1e-6)
    config = SolveConfig(maxh_mm=4, order=2, n_modes=3, target_ghz=15,
                         loss_model="none", threads=1,
                         ports=[Port(name="top", faces=[upper], impedance_ohm=1e6)])
    result = solve(None, config, domain_override=shape, progress=lambda _: None)
    assert len(result["modes"]) == 3
    for mode in result["modes"]:
        assert mode["frequency_ghz"] > 10
        assert mode["frequency_imag_ghz"] > 0
        assert mode["residual"] < 1e-8
        assert mode["q_ports_eigenvalue"] == pytest.approx(mode["ports"][0]["qc"], rel=1e-6)




def test_independent_conductor_loss_has_expected_scaling():
    one = SolveConfig(loss_model="surface_rs", rs_nohm=10, oxide_tand=0)
    two = SolveConfig(loss_model="surface_rs", rs_nohm=20, oxide_tand=0)
    q1 = loss_budget(5e9, 100, 0, one)
    q2 = loss_budget(5e9, 100, 0, two)
    assert q1["qi"] == pytest.approx(1e10)
    assert q2["qi"] == pytest.approx(5e9)
    assert q1["inverse_q_residual"] == 0
    assert q1["inverse_q_tls"] == 0




@pytest.mark.parametrize("options", [
    {"loss_model": "surface_rs", "rs_nohm": None},
    {"loss_model": "unknown"},
    {"bulk_epsr": -1},
    {"maxh_mm": float("nan")},
    {"ports": [{"name": "a", "faces": [1]}, {"name": "b", "faces": [1]}]},
    {"ports": [{"name": "a", "faces": [0]}]},
])
def test_invalid_physical_configuration_is_rejected(options):
    with pytest.raises(ValueError):
        SolveConfig(**options)


def test_user_supplied_tls_saturation_and_residual_budget():
    config = SolveConfig(loss_model="tls_qres", photon_number=0, temperature_k=.02,
                         oxide_tand=.002, tls_nc=100, tls_beta=.5, q_res=1e8)
    low = loss_budget(5e9, 100, 2e-7, config)
    expected_tls = 2e-7*.002*math.tanh(6.62607015e-34*5e9/(2*1.380649e-23*.02))
    assert low["inverse_q_tls"] == pytest.approx(expected_tls)
    assert low["qi"] == pytest.approx(1/(expected_tls+1e-8))
    high = loss_budget(5e9, 100, 2e-7, config.model_copy(update={"photon_number": 100}))
    assert high["inverse_q_tls"] == pytest.approx(expected_tls/math.sqrt(2))
    assert high["qi"] > low["qi"]
