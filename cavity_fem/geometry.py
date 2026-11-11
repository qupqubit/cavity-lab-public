"""STEP import, explicit bounding-box caps, and native OCC geometry."""
from pathlib import Path
import math


def xyz(p):
    return [float(p[i]) for i in range(3)]


def shape_info(shape):
    return {"volume_mm3": float(shape.mass), "bbox_mm": [xyz(p) for p in shape.bounding_box],
            "faces": len(shape.faces)}


def face_info(shape, cap_planes=None):
    result = []
    for i, face in enumerate(shape.faces):
        lo, hi = [xyz(p) for p in face.bounding_box]
        cap = any(abs(lo[axis] - value) < 1e-5 and abs(hi[axis] - value) < 1e-5
                  for axis, value in (cap_planes or []))
        result.append({"id": i + 1, "area_mm2": float(face.mass), "center_mm": xyz(face.center),
                       "bbox_mm": [lo, hi], "artificial_cap": cap})
    return result


def preview(shape):
    # OCC.Triangulation's STL return binding is unavailable in some macOS wheels.
    # Netgen surface meshing provides portable, indexed triangles and CAD face IDs.
    from netgen.occ import OCCGeometry
    from netgen.meshing import MeshingStep
    lo, hi = [xyz(p) for p in shape.bounding_box]
    size = max(hi[i] - lo[i] for i in range(3)) / 12
    surface = OCCGeometry(shape).GenerateMesh(maxh=max(size, 0.1), perfstepsend=MeshingStep.MESHSURFACE)
    vertices = [xyz(point.p) for point in surface.Points()]
    triangles, face_ids = [], []
    for element in surface.Elements2D():
        points = [int(point.nr)-1 for point in element.vertices]
        if len(points) == 3:
            triangles.append(points)
            face_ids.append(surface.FaceDescriptor(element.index).bc)
    return {"vertices": vertices, "triangles": triangles, "face_ids": face_ids}


def load_domain(path, mode="metal_void", solid_index=0):
    from netgen.occ import OCCGeometry, Box, Pnt
    original = OCCGeometry(str(path)).shape
    solids = list(original.solids)
    if not solids:
        raise ValueError("STEPに閉じたソリッドがありません。解析領域を閉じた立体にしてください。")
    cap_planes = []
    warnings = []
    if mode == "metal_void":
        lo, hi = [xyz(p) for p in original.bounding_box]
        # OCC bounding boxes include a 1e-7 mm tolerance. Remove it to avoid sliver solids.
        lo = [round(v, 6) for v in lo]
        hi = [round(v, 6) for v in hi]
        remainder = Box(Pnt(*lo), Pnt(*hi))
        for solid in solids:
            remainder = remainder - solid
        choices = sorted([s for s in remainder.solids if s.mass > 1e-6], key=lambda s: -s.mass)
        cap_planes = [(axis, coord[axis]) for coord in (lo, hi) for axis in range(3)]
        warnings.append("金属の外接直方体から金属を差し引いた空隙です。外接面で開口を閉じています。面一覧の人工キャップと選択空隙を必ず確認してください。")
    elif mode == "vacuum_solid":
        choices = solids
    else:
        raise ValueError("不明な形状モードです。")
    if not 0 <= solid_index < len(choices):
        raise ValueError("選択した領域番号は存在しません。")
    domain = choices[solid_index]
    domain.mat("bulk")
    for i, face in enumerate(domain.faces):
        face.name = "face_%d" % (i + 1)
    info = {"source_solids": [{"index": i, **shape_info(s)} for i, s in enumerate(solids)],
            "solids": [{"index": i, **shape_info(s)} for i, s in enumerate(choices)],
            "selected": solid_index, "mode": mode, "faces": face_info(domain, cap_planes),
            "warnings": warnings, **shape_info(domain)}
    info["faces"] = face_info(domain, cap_planes)
    return domain, info


def inspect_step(path, mode="metal_void", solid_index=0):
    domain, info = load_domain(path, mode, solid_index)
    info["preview"] = preview(domain)
    info["filename"] = Path(path).name
    return info
