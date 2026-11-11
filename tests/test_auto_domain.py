"""Automatic domain inference on original, self-generated primitives only."""
import math

import pytest
from netgen.occ import Box, Compound, Cylinder, Pnt, Vec

from cavity_fem.geometry import inspect_step, load_domain


def _write(tmp_path, shape):
    path = tmp_path / "generated-domain.step"
    shape.WriteStep(str(path))
    return path


def _check_detection(info, expected_mode):
    detection = info["domain_detection"]
    assert info["mode"] == detection["selected_mode"] == expected_mode
    assert info["selected"] == detection["selected_index"]
    assert detection["requested_mode"] == "auto"
    assert detection["method"] == "geometric_enclosure"
    assert detection["semantic_identity_proven"] is False
    assert detection["reason"] and detection["notes"]
    assert any("推定" in warning for warning in info["warnings"])
    return detection


def test_solid_box_uses_input_solid_and_auto_ignores_old_region_index(tmp_path):
    path = _write(tmp_path, Box(Pnt(0, 0, 0), Pnt(8, 10, 12)))
    info = inspect_step(path, "auto", solid_index=999)
    detection = _check_detection(info, "vacuum_solid")
    assert info["selected"] == 0
    assert info["volume_mm3"] == pytest.approx(8 * 10 * 12)
    assert detection["evidence"]["void_candidate_count"] == 0
    assert info["preview"]["triangles"]
    assert info["opening_detection"]["method"] == "not_applicable"


def test_closed_hollow_box_prefers_enclosed_cavity(tmp_path):
    shape = Box(Pnt(0, 0, 0), Pnt(12, 12, 12)) - Box(Pnt(2, 2, 2), Pnt(10, 10, 10))
    _, info = load_domain(_write(tmp_path, shape), "auto")
    detection = _check_detection(info, "metal_void")
    assert info["volume_mm3"] == pytest.approx(8**3)
    assert detection["confidence"] == "high"
    assert detection["evidence"]["void_candidates"][0]["classification"] == "enclosed_void"
    assert info["opening_detection"]["candidate_count"] == 0


def test_round_tube_selects_bore_instead_of_larger_outer_corners(tmp_path):
    shape = (Cylinder(Pnt(0, 0, 0), Vec(0, 0, 1), 5, 10)
             - Cylinder(Pnt(0, 0, -1), Vec(0, 0, 1), 1, 12))
    path = _write(tmp_path, shape)
    _, info = load_domain(path, "auto")
    detection = _check_detection(info, "metal_void")
    assert info["selected"] != 0
    assert info["volume_mm3"] == pytest.approx(math.pi * 10, rel=1e-8)
    assert len(info["solids"]) == 5
    assert detection["confidence"] == "medium"
    assert detection["evidence"]["plausible_void_indices"] == [info["selected"]]
    assert info["opening_detection"]["candidate_count"] == 2
    # Exact manual indices/faces remain usable after the recommendation.
    _, manual = load_domain(path, "metal_void", info["selected"])
    assert manual["faces"] == info["faces"]
    assert manual["solids"] == info["solids"]
    _, outer_corner = load_domain(path, "metal_void", 0)
    assert outer_corner["volume_mm3"] > info["volume_mm3"]
    assert outer_corner["selected"] == 0
    assert outer_corner["domain_detection"]["method"] == "manual_selection"
    # An annular vacuum solid has exactly the same geometric shape. Its
    # intended meaning is not inferred: explicit vacuum input must be honored.
    _, annular_vacuum = load_domain(path, "vacuum_solid", 0)
    assert annular_vacuum["volume_mm3"] == pytest.approx(math.pi * (25 - 1) * 10, rel=1e-8)
    assert annular_vacuum["domain_detection"]["requested_mode"] == "vacuum_solid"


def test_solid_cylinder_does_not_choose_outside_corners(tmp_path):
    _, info = load_domain(_write(tmp_path, Cylinder(Pnt(0, 0, 0), Vec(0, 0, 1), 5, 10)), "auto")
    detection = _check_detection(info, "vacuum_solid")
    assert info["volume_mm3"] == pytest.approx(math.pi * 25 * 10, rel=1e-8)
    assert detection["evidence"]["plausible_void_indices"] == []
    assert all(row["bbox_edge_cap_face_ids"] for row in detection["evidence"]["void_candidates"])


def test_multiple_internal_cavities_choose_largest_with_low_confidence(tmp_path):
    shape = Box(Pnt(0, 0, 0), Pnt(20, 12, 12))
    shape = shape - Box(Pnt(2, 2, 2), Pnt(8, 10, 10))
    shape = shape - Box(Pnt(12, 3, 3), Pnt(18, 9, 9))
    _, info = load_domain(_write(tmp_path, shape), "auto")
    detection = _check_detection(info, "metal_void")
    assert info["volume_mm3"] == pytest.approx(6 * 8 * 8)
    assert detection["confidence"] == "low"
    assert len(detection["evidence"]["plausible_void_indices"]) == 2
    assert "複数" in detection["reason"]


def test_box_bore_uses_global_bbox_for_edge_contact(tmp_path):
    # Every cap reaches its own void's in-plane extrema. Only contact with
    # the SOURCE box's edges is evidence of exterior/ambiguous space.
    shape = Box(Pnt(0, 0, 0), Pnt(12, 12, 12)) - Box(Pnt(-1, 3, 3), Pnt(13, 9, 9))
    _, info = load_domain(_write(tmp_path, shape), "auto")
    detection = _check_detection(info, "metal_void")
    assert info["volume_mm3"] == pytest.approx(12 * 6 * 6)
    row = detection["evidence"]["void_candidates"][info["selected"]]
    assert row["classification"] == "interior_aperture_void"
    assert row["bbox_edge_cap_face_ids"] == []
    assert info["opening_detection"]["candidate_count"] == 2


def test_edge_open_void_is_ambiguous_and_falls_back_to_input_solid(tmp_path):
    shape = Box(Pnt(0, 0, 0), Pnt(12, 12, 12)) - Box(Pnt(3, 3, 3), Pnt(13, 13, 9))
    _, info = load_domain(_write(tmp_path, shape), "auto")
    detection = _check_detection(info, "vacuum_solid")
    assert detection["confidence"] == "low"
    assert detection["evidence"]["plausible_void_indices"] == []
    assert any(row["bbox_edge_cap_face_ids"] for row in detection["evidence"]["void_candidates"])
    assert "仮選択" in detection["reason"]


def test_side_open_curved_shell_can_merge_cavity_with_exterior(tmp_path):
    shape = Cylinder(Pnt(0, 0, 0), Vec(0, 0, 1), 5, 10)
    shape = shape - Cylinder(Pnt(0, 0, 1), Vec(0, 0, 1), 3, 8)
    shape = shape - Cylinder(Pnt(0, 0, 5), Vec(1, 0, 0), 1, 6)
    _, info = load_domain(_write(tmp_path, shape), "auto")
    detection = _check_detection(info, "vacuum_solid")
    assert detection["confidence"] == "low"
    assert detection["evidence"]["plausible_void_indices"] == []


def test_largest_input_selection_preserves_original_source_index(tmp_path):
    shape = Compound([Box(Pnt(0, 0, 0), Pnt(1, 1, 1)), Box(Pnt(3, 0, 0), Pnt(5, 2, 2))])
    path = _write(tmp_path, shape)
    _, manual = load_domain(path, "vacuum_solid", 0)
    _, info = load_domain(path, "auto")
    detection = _check_detection(info, "vacuum_solid")
    largest = max(manual["source_solids"], key=lambda row: row["volume_mm3"])
    assert info["selected"] == largest["index"] == 1
    assert info["source_solids"] == manual["source_solids"]
    assert info["volume_mm3"] == pytest.approx(8)
    assert manual["volume_mm3"] == pytest.approx(1)
    assert detection["confidence"] == "low"


def test_failed_auto_extraction_is_uncertain_but_manual_error_is_preserved(tmp_path, monkeypatch):
    import cavity_fem.geometry as geometry
    path = _write(tmp_path, Box(Pnt(0, 0, 0), Pnt(8, 10, 12)))
    def failure(*args):
        raise RuntimeError("synthetic boolean failure")
    monkeypatch.setattr(geometry, "_void_regions", failure)
    _, info = geometry.load_domain(path, "auto")
    detection = _check_detection(info, "vacuum_solid")
    assert detection["confidence"] == "low"
    assert detection["evidence"]["void_extraction_failed"] is True
    with pytest.raises(RuntimeError, match="synthetic boolean failure"):
        geometry.load_domain(path, "metal_void")
