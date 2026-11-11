"""Analytic checks that would detect natural-trace and complex-phasor errors."""
import ngsolve as ng
import pytest
from netgen.occ import Box, Cylinder, OCCGeometry, Pnt, Sphere, Vec

from cavity_fem.surface import surface_integrals


def make_mesh(shape, maxh=.5):
    for index, face in enumerate(shape.faces):
        face.name = "face_%d" % (index + 1)
    mesh = ng.Mesh(OCCGeometry(shape).GenerateMesh(maxh=maxh))
    mesh.Curve(3)
    return mesh


def totals(mesh, expression, complex=False):
    gf = ng.GridFunction(ng.HCurl(mesh, order=5, complex=complex))
    gf.Set(expression)
    faces = surface_integrals(mesh, gf, order=12)
    return {key: sum(face[key] for face in faces) for key in faces[0] if key.endswith("mm2")}, faces


def test_cube_constant_e_keeps_normal_component():
    mesh = make_mesh(Box(Pnt(0, 0, 0), Pnt(1, 1, 1)))
    result, faces = totals(mesh, ng.CF((1, 2, 3)))
    assert [face["id"] for face in faces] == list(range(1, 7))
    assert result["e_full2_mm2"] == pytest.approx(84, rel=1e-10)
    assert result["e_normal2_mm2"] == pytest.approx(28, rel=1e-10)
    assert result["e_tangent2_mm2"] == pytest.approx(56, rel=1e-10)


def test_cube_curl_keeps_tangential_component():
    mesh = make_mesh(Box(Pnt(0, 0, 0), Pnt(1, 1, 1)))
    result, _ = totals(mesh, ng.CF((-ng.y, ng.x, 0)))
    # curl(-y, x, 0) = (0, 0, 2).
    assert result["curl_full2_mm2"] == pytest.approx(24, rel=1e-10)
    assert result["curl_normal2_mm2"] == pytest.approx(8, rel=1e-10)
    assert result["curl_t2_mm2"] == pytest.approx(16, rel=1e-10)


def test_complex_phasor_modulus_and_global_phase():
    mesh = make_mesh(Box(Pnt(0, 0, 0), Pnt(1, 1, 1)))
    amplitude = 2 - 1j
    expression = ng.CF((-amplitude * ng.y, amplitude * ng.x, 0))
    result, _ = totals(mesh, expression, complex=True)
    rotated, _ = totals(mesh, (0.6 + 0.8j) * expression, complex=True)
    assert result["curl_t2_mm2"] == pytest.approx(80, rel=1e-10)
    assert result["curl_normal2_mm2"] == pytest.approx(40, rel=1e-10)
    for key, value in result.items():
        assert value >= 0
        assert rotated[key] == pytest.approx(value, rel=1e-10, abs=1e-12)


@pytest.mark.parametrize("shape", [Sphere(Pnt(0, 0, 0), 1),
                                  Cylinder(Pnt(0, 0, 0), Vec(0, 0, 1), 1, 2)])
def test_curved_boundary_against_analytic_field(shape):
    mesh = make_mesh(shape, .4)
    exact = ng.CF((1, 2, 3))
    normal = ng.specialcf.normal(3)
    result, _ = totals(mesh, exact)
    # Compare with analytic CF on the same curved mesh to isolate evaluation
    # correctness from polynomial approximation of the CAD surface geometry.
    expected_normal = ng.Integrate((exact * normal)**2, mesh, ng.BND, order=12)
    expected_full = ng.Integrate(exact * exact, mesh, ng.BND, order=12)
    assert result["e_normal2_mm2"] == pytest.approx(expected_normal, rel=5e-7)
    assert result["e_full2_mm2"] == pytest.approx(expected_full, rel=5e-7)


def test_pec_polynomial_has_nonzero_surface_oxide_energy():
    mesh = make_mesh(Box(Pnt(0, 0, 0), Pnt(1, 1, 1)))
    expression = ng.CF((0, 0, ng.x*(1-ng.x)*ng.y*(1-ng.y)))
    result, _ = totals(mesh, expression)
    # Two z faces: 2 * (integral_0^1 x^2*(1-x)^2 dx)^2 = 1/450.
    assert result["e_normal2_mm2"] == pytest.approx(1/450, rel=1e-9)
    assert result["e_tangent2_mm2"] < 1e-20
