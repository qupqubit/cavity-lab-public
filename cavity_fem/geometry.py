"""STEP import and geometric opening candidates at bounding-box caps.

The caps are truncation surfaces of the selected void, not a proof that every
physical opening has been identified or that the selected void is a cavity.
"""
from pathlib import Path


def xyz(p):
    return [float(p[i]) for i in range(3)]


def shape_info(shape):
    return {"volume_mm3": float(shape.mass), "bbox_mm": [xyz(p) for p in shape.bounding_box],
            "faces": len(shape.faces)}


def face_info(shape, cap_planes=None):
    result = []
    for i, face in enumerate(shape.faces):
        lo, hi = [xyz(p) for p in face.bounding_box]
        matches = [{"axis": "xyz"[axis], "coordinate_mm": value}
                   for axis, value in (cap_planes or [])
                   if abs(lo[axis] - value) < 1e-5 and abs(hi[axis] - value) < 1e-5]
        result.append({"id": i + 1, "area_mm2": float(face.mass), "center_mm": xyz(face.center),
                       "bbox_mm": [lo, hi], "artificial_cap": bool(matches),
                       "cap_planes": matches})
    return result


def _opening_detection(faces, mode, selected):
    """Describe the geometric evidence without inferring physical port types."""
    candidates = [face["id"] for face in faces if face["artificial_cap"]]
    metal_void = mode == "metal_void"
    notes = ([
        "金属の外接直方体の6平面上で、選択した空隙を閉じる人工面を開口候補とします。",
        "候補は外接面での切断面です。実際の穴の入口位置・種類・放射条件を確定するものではありません。",
        "金属外形の周囲の外部空間も抽出されます。体積最大の空隙が目的のキャビティとは限りません。",
        "傾いた開口や外接面より内側の入口など、すべての物理的な開口の識別は保証しません。",
        "候補が0件でも、実物に開口がないことを証明するものではありません。",
    ] if metal_void else [
        "真空ソリッド入力では外接面から開口を推定しません。外部接続にする面を指定してください。",
    ])
    notes.append("候補の面IDはアンテナ追加前の選択領域を参照します。")
    return {"method": "bounding_box_caps" if metal_void else "not_applicable",
            "status": ("candidates_found" if candidates else "no_bbox_caps") if metal_void else "not_applicable",
            "candidate_face_ids": candidates, "candidate_count": len(candidates),
            "face_id_space": "source_faces", "selected_region_index": selected,
            "physical_completeness_proven": False, "exterior_space_excluded": False,
            "notes": notes}


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
        warnings.append("金属外形の周囲の外部空間も抽出候補に含まれます。体積最大の領域が目的のキャビティとは限りません。")
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
    faces = face_info(domain, cap_planes)
    info = {**shape_info(domain),
            "source_solids": [{"index": i, **shape_info(s)} for i, s in enumerate(solids)],
            "solids": [{"index": i, **shape_info(s)} for i, s in enumerate(choices)],
            "selected": solid_index, "mode": mode, "faces": faces,
            "warnings": warnings, "opening_detection": _opening_detection(faces, mode, solid_index)}
    return domain, info


def inspect_step(path, mode="metal_void", solid_index=0):
    domain, info = load_domain(path, mode, solid_index)
    info["preview"] = preview(domain)
    info["filename"] = Path(path).name
    return info
