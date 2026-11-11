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


def _void_regions(original, solids):
    """Keep the established subtraction, region ordering and cap coordinates."""
    from netgen.occ import Box, Pnt
    lo, hi = [xyz(p) for p in original.bounding_box]
    # OCC boxes include a 1e-7 mm tolerance; avoid artificial sliver solids.
    lo = [round(v, 6) for v in lo]
    hi = [round(v, 6) for v in hi]
    remainder = Box(Pnt(*lo), Pnt(*hi))
    for solid in solids:
        remainder = remainder - solid
    choices = sorted([s for s in remainder.solids if s.mass > 1e-6], key=lambda s: -s.mass)
    planes = [(axis, coord[axis]) for coord in (lo, hi) for axis in range(3)]
    return choices, planes, (lo, hi)


def _infer_domain(solids, voids, cap_planes, bounds, extraction_failed=False):
    """Conservative enclosure heuristic, without interpreting STEP materials.

    A bore cap wholly inside a box face is different from a corner of the
    exterior complement. Contact with any box edge makes a void ambiguous.
    Face bounding boxes can overestimate edge contact for curved patches;
    this intentionally favors an uncertain fallback over a confident claim.
    """
    evidence = []
    if bounds is not None:
        lower, upper = bounds
        for index, void in enumerate(voids):
            caps = [face for face in face_info(void, cap_planes) if face["artificial_cap"]]
            touching = []
            for cap in caps:
                lo, hi = cap["bbox_mm"]
                on_edge = any(
                    lo[axis] <= lower[axis] + 1e-5 or hi[axis] >= upper[axis] - 1e-5
                    for plane in cap["cap_planes"]
                    for axis in range(3) if axis != "xyz".index(plane["axis"]))
                if on_edge:
                    touching.append(cap["id"])
            evidence.append({"index": index, "volume_mm3": float(void.mass),
                             "cap_count": len(caps), "bbox_edge_cap_face_ids": touching,
                             "classification": "enclosed_void" if not caps else
                             "interior_aperture_void" if not touching else "edge_connected_void",
                             "plausible_internal_void": not touching})
    plausible = [row for row in evidence if row["plausible_internal_void"]]
    if plausible:
        # voids already use the manual mode's descending-volume order.
        selected = plausible[0]["index"]
        mode = "metal_void"
        if len(plausible) > 1:
            confidence = "low"
            reason = "内部空洞の候補が複数あるため、最も大きい候補を仮選択しました。"
        elif plausible[0]["classification"] == "enclosed_void":
            confidence = "high"
            reason = "入力立体に囲まれた空洞が1つ見つかったため、金属から空洞を抽出します。"
        else:
            confidence = "medium"
            reason = "外接直方体の辺に触れない内孔の候補が見つかったため、金属から空洞を抽出します。"
    else:
        mode = "vacuum_solid"
        selected = max(range(len(solids)), key=lambda index: solids[index].mass)
        confidence = "low" if evidence or len(solids) > 1 or extraction_failed else "medium"
        reason = ("空隙の抽出に失敗したため、最も大きい入力立体を仮選択しました。" if extraction_failed else
                  "内部空洞を外部空間から区別できないため、最も大きい入力立体を仮選択しました。" if evidence else
                  "内部空洞が見つからないため、最も大きい入力立体を解析領域として推定しました。")
    return {"requested_mode": "auto", "selected_mode": mode, "selected_index": selected,
            "confidence": confidence, "reason": reason, "method": "geometric_enclosure",
            "semantic_identity_proven": False,
            "evidence": {"source_solid_count": len(solids), "void_candidate_count": len(voids),
                         "plausible_void_indices": [row["index"] for row in plausible],
                         "void_candidates": evidence, "void_extraction_failed": extraction_failed},
            "notes": ["STEPの形状からの推定です。金属・真空の材質や設計者の意図を確定するものではありません。",
                      "同じ中空形状が金属壁・真空領域のどちらを表すかは、形状だけでは決められません。",
                      "選択領域のプレビューを確認してください。入力の扱いと領域番号は手動で変更できます。",
                      "外接直方体の辺に触れる開口や、複雑・傾斜した形状では内部空洞を識別できない場合があります。"]}


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
    from netgen.occ import OCCGeometry
    original = OCCGeometry(str(path)).shape
    solids = list(original.solids)
    if not solids:
        raise ValueError("STEPに閉じたソリッドがありません。解析領域を閉じた立体にしてください。")
    cap_planes = []
    warnings = []
    requested_mode = mode
    detection = None
    if mode == "auto":
        try:
            voids, void_planes, bounds = _void_regions(original, solids)
        except Exception:
            # Automatic inference must not make a valid input solid unusable.
            # Explicit metal_void still reports its extraction error normally.
            voids, void_planes, bounds = [], [], None
        detection = _infer_domain(solids, voids, void_planes, bounds,
                                  extraction_failed=bounds is None)
        mode = detection["selected_mode"]
        solid_index = detection["selected_index"]
        warnings.append("自動判定: " + detection["reason"])
        warnings.append(detection["notes"][0])
    if mode == "metal_void":
        if requested_mode == "auto":
            choices, cap_planes = voids, void_planes
        else:
            choices, cap_planes, _ = _void_regions(original, solids)
        warnings.append("金属の外接直方体から金属を差し引いた空隙です。外接面で開口を閉じています。面一覧の人工キャップと選択空隙を必ず確認してください。")
        warnings.append("金属外形の周囲の外部空間も抽出候補に含まれます。体積最大の領域が目的のキャビティとは限りません。")
    elif mode == "vacuum_solid":
        choices = solids
    else:
        raise ValueError("不明な形状モードです。")
    if not 0 <= solid_index < len(choices):
        raise ValueError("選択した領域番号は存在しません。")
    if detection is None:
        detection = {"requested_mode": requested_mode, "selected_mode": mode,
                     "selected_index": solid_index, "confidence": "high",
                     "reason": "入力の扱いと領域は手動指定に従っています。自動推定は行っていません。",
                     "method": "manual_selection", "semantic_identity_proven": False,
                     "notes": ["確かさは指定どおりに選択されたことを表し、金属・真空の材質の判定ではありません。"]}
    domain = choices[solid_index]
    domain.mat("bulk")
    for i, face in enumerate(domain.faces):
        face.name = "face_%d" % (i + 1)
    faces = face_info(domain, cap_planes)
    info = {**shape_info(domain),
            "source_solids": [{"index": i, **shape_info(s)} for i, s in enumerate(solids)],
            "solids": [{"index": i, **shape_info(s)} for i, s in enumerate(choices)],
            "selected": solid_index, "mode": mode, "faces": faces,
            "warnings": warnings, "opening_detection": _opening_detection(faces, mode, solid_index),
            "domain_detection": detection}
    return domain, info


def inspect_step(path, mode="metal_void", solid_index=0):
    domain, info = load_domain(path, mode, solid_index)
    info["preview"] = preview(domain)
    info["filename"] = Path(path).name
    return info
