"""Opening candidates from generated primitives; no third-party CAD fixtures."""
import math

import pytest
from netgen.occ import Box, Cylinder, Pnt, Vec

from cavity_fem.geometry import inspect_step, load_domain


def _write(tmp_path, shape, name="generated-metal.step"):
    path = tmp_path / name
    shape.WriteStep(str(path))
    return path


def _caps(info):
    detection = info["opening_detection"]
    candidates = [f for f in info["faces"] if f["artificial_cap"]]
    assert detection["candidate_face_ids"] == [f["id"] for f in candidates]
    assert detection["candidate_count"] == len(candidates)
    assert detection["selected_region_index"] == info["selected"]
    assert detection["face_id_space"] == "source_faces"
    assert detection["physical_completeness_proven"] is False
    assert detection["exterior_space_excluded"] is False
    assert all(bool(f["cap_planes"]) == f["artificial_cap"] for f in info["faces"])
    return candidates


def test_closed_hollow_box_has_no_opening_candidates(tmp_path):
    metal = Box(Pnt(0, 0, 0), Pnt(12, 12, 12)) - Box(Pnt(2, 2, 2), Pnt(10, 10, 10))
    _, info = load_domain(_write(tmp_path, metal))
    assert info["volume_mm3"] == pytest.approx(8**3)
    assert len(info["solids"]) == 1
    assert _caps(info) == []
    assert info["opening_detection"]["method"] == "bounding_box_caps"
    assert info["opening_detection"]["status"] == "no_bbox_caps"


def test_rectangular_through_tube_detects_both_ends_and_stable_ids(tmp_path):
    metal = Box(Pnt(0, 0, 0), Pnt(12, 12, 12)) - Box(Pnt(-1, 3, 3), Pnt(13, 9, 9))
    path = _write(tmp_path, metal)
    _, info = load_domain(path)
    _, again = load_domain(path)
    assert info["volume_mm3"] == pytest.approx(12 * 6**2)
    caps = _caps(info)
    assert len(caps) == 2
    assert sorted(f["center_mm"][0] for f in caps) == pytest.approx([0, 12])
    assert all(f["area_mm2"] == pytest.approx(36) for f in caps)
    assert all(f["cap_planes"][0]["axis"] == "x" for f in caps)
    assert info["opening_detection"] == again["opening_detection"]
    assert info["faces"] == again["faces"]


def test_multiple_apertures_on_same_plane_remain_separate_candidates(tmp_path):
    outside = Box(Pnt(0, 0, 0), Pnt(12, 12, 12))
    void = Box(Pnt(2, 2, 2), Pnt(10, 10, 10))
    # Two disjoint bores at the upper face and one at the right-hand face.
    void = void + Cylinder(Pnt(4, 6, 9), Vec(0, 0, 1), 1, 4)
    void = void + Cylinder(Pnt(8, 6, 9), Vec(0, 0, 1), 1, 4)
    void = void + Cylinder(Pnt(9, 6, 6), Vec(1, 0, 0), 1, 4)
    info = inspect_step(_write(tmp_path, outside - void))
    caps = _caps(info)
    assert len(info["solids"]) == 1
    assert len(caps) == 3
    assert sorted(f["cap_planes"][0]["axis"] for f in caps) == ["x", "z", "z"]
    assert all(f["area_mm2"] == pytest.approx(math.pi, rel=1e-8) for f in caps)
    assert set(info["opening_detection"]["candidate_face_ids"]) <= set(info["preview"]["face_ids"])
    assert info["opening_detection"]["status"] == "candidates_found"


def test_round_tube_exterior_is_not_misrepresented_as_selected_cavity(tmp_path):
    metal = (Cylinder(Pnt(0, 0, 0), Vec(0, 0, 1), 5, 10)
             - Cylinder(Pnt(0, 0, -1), Vec(0, 0, 1), 1, 12))
    path = _write(tmp_path, metal)
    _, default = load_domain(path)
    # The four corners outside the round metal body are separate voids and
    # each is larger than the bore. Selection remains explicit, as before.
    assert len(default["solids"]) == 5
    assert default["volume_mm3"] > math.pi * 10
    assert _caps(default)
    assert any("体積最大" in warning for warning in default["warnings"])
    assert any("外部空間" in note for note in default["opening_detection"]["notes"])
    bore_index = next(s["index"] for s in default["solids"]
                      if abs(s["volume_mm3"] - math.pi * 10) < 1e-5)
    _, bore = load_domain(path, solid_index=bore_index)
    caps = _caps(bore)
    assert len(caps) == 2
    assert all(f["cap_planes"][0]["axis"] == "z" for f in caps)
    assert all(f["area_mm2"] == pytest.approx(math.pi, rel=1e-8) for f in caps)
    assert bore["volume_mm3"] == pytest.approx(math.pi * 10, rel=1e-8)


def test_vacuum_solid_does_not_infer_every_flat_face_is_open(tmp_path):
    shape = Box(Pnt(0, 0, 0), Pnt(12, 10, 8))
    _, info = load_domain(_write(tmp_path, shape, "generated-vacuum.step"), "vacuum_solid")
    assert len(info["faces"]) == 6
    assert _caps(info) == []
    assert info["opening_detection"]["method"] == "not_applicable"
    assert info["opening_detection"]["status"] == "not_applicable"
