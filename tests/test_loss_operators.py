"""Independent transmission-line references for loss and unequal TEM ports."""
from collections import Counter
import math

import numpy as np
import pytest
from netgen.occ import Cylinder, Glue, Pnt, Vec

from cavity_fem.config import SolveConfig
from cavity_fem.geometry import shape_info, xyz
from cavity_fem.solver import C0, Z0
from cavity_fem.spectrum import MaxwellSystem


def annulus(z, length):
    return (Cylinder(Pnt(0, 0, z), Vec(0, 0, 1), 3, length)
            - Cylinder(Pnt(0, 0, z), Vec(0, 0, 1), 1, length))


def coax_geometry(material_eps):
    """One or two 5 mm uniform sections, with independently named interfaces."""
    sections = []
    materials = {}
    for index, epsr in enumerate(material_eps):
        name = "section_%d" % index
        section = annulus(index*5, 5)
        section.mat(name)
        sections.append(section)
        materials[name] = {"epsr": epsr, "tand": 0}
    shape = Glue(sections) if len(sections) > 1 else sections[0]
    occurrences = Counter(shape.faces)
    length = len(sections)*5
    records, terminals, walls, interfaces = [], {}, [], []
    for face_id, (face, count) in enumerate(occurrences.items(), 1):
        face.name = "face_%d" % face_id
        low, high = [xyz(p) for p in face.bounding_box]
        records.append({"id": face_id, "center_mm": xyz(face.center), "bbox_mm": [low, high],
                        "area_mm2": float(face.mass), "internal": count == 2})
        if count == 2:
            interfaces.append(face_id)
        elif abs(low[2]) < 1e-6 and abs(high[2]) < 1e-6:
            terminals[0] = face_id
        elif abs(low[2]-length) < 1e-6 and abs(high[2]-length) < 1e-6:
            terminals[length] = face_id
        else:
            walls.append(face_id)
    ports = [{"kind": "coax_tem", "name": name, "faces": [terminals[z]],
              "center_mm": [0, 0, z], "axis": axis, "inner_radius_mm": 1,
              "outer_radius_mm": 3, "dielectric_epsr": epsr}
             for name, z, axis, epsr in [("left", 0, [0, 0, -1], material_eps[0]),
                                       ("right", length, [0, 0, 1], material_eps[-1])]]
    geometry = {**shape_info(shape), "faces": records, "antenna_ports": ports,
                "wall_faces": walls, "oxide_wall_faces": walls,
                "internal_faces": interfaces, "materials": materials}
    return shape, geometry


def test_surface_resistance_matches_independent_coax_attenuation():
    shape, geometry = coax_geometry([2.1])
    resistance_ohm = .002
    config = SolveConfig(maxh_mm=1.5, order=3, threads=1, loss_model="surface_rs",
                         rs_nohm=resistance_ohm*1e9, oxide_tand=0)
    system = MaxwellSystem(shape, geometry, config)
    result = system.driven(5)
    # Series resistance per metre of the two cylindrical conductors. The
    # weak-loss power attenuation over L is exp(-R' L / Zc).
    r_per_m = resistance_ohm/(2*math.pi) * (1/.001 + 1/.003)
    zc = Z0/(2*math.pi*math.sqrt(2.1))*math.log(3)
    expected = 1-math.exp(-r_per_m*.005/zc)
    assert result["absorbed_power_w"] == pytest.approx(expected, rel=.004)
    assert result["power_balance_error"] < 1e-8
    assert 0 < result["output_power_w"] < 1


def test_unequal_dielectric_coax_ports_have_power_normalized_reciprocal_s():
    shape, geometry = coax_geometry([1, 4])
    config = SolveConfig(maxh_mm=1.5, order=3, threads=1, loss_model="none")
    system = MaxwellSystem(shape, geometry, config)
    frequency = 5e9
    forward, reverse = system.driven(frequency/1e9, 0), system.driven(frequency/1e9, 1)
    z1 = Z0/(2*math.pi)*math.log(3)
    z2 = z1/2
    beta1, beta2 = 2*math.pi*frequency/C0, 2*math.pi*frequency/C0*2
    phase1, phase2 = beta1*.005, beta2*.005
    reflection1 = (z2-z1)/(z1+z2)*np.exp(-2j*phase1)
    reflection2 = (z1-z2)/(z1+z2)*np.exp(-2j*phase2)
    transmission = 2*math.sqrt(z1*z2)/(z1+z2)*np.exp(-1j*(phase1+phase2))
    assert forward["scattering"][0] == pytest.approx(reflection1, abs=.001)
    assert reverse["scattering"][1] == pytest.approx(reflection2, abs=.001)
    assert forward["scattering"][1] == pytest.approx(transmission, abs=.001)
    assert reverse["scattering"][0] == pytest.approx(transmission, abs=.001)
    assert forward["scattering"][1] == pytest.approx(reverse["scattering"][0], abs=1e-8)
    assert forward["power_balance_error"] < 1e-8
    assert reverse["power_balance_error"] < 1e-8
    assert not set(geometry["internal_faces"]) & system.wall_faces
