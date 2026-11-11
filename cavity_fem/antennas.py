"""Parameterized ideal coax feeds and finite PEC pins attached to CAD faces.

All dimensions and dielectric properties are editable model inputs. The generic
cross section does not describe a particular manufacturer's connector.
"""
from collections import Counter
import copy
import math

import numpy as np

from .geometry import xyz, shape_info


def _point(center, axis, distance):
    return np.asarray(center) + np.asarray(axis)*distance


def _cylinder(center, axis, radius, length, **kwargs):
    from netgen.occ import Cylinder, Pnt, Vec
    return Cylinder(Pnt(*center), Vec(*axis), radius, length, **kwargs)


def _attachment(domain, face, antenna):
    """Get a planar aperture's center and inward unit axis; reject blind feeds."""
    from netgen.occ import Vertex, Pnt
    center = np.asarray(antenna.center_mm if antenna.center_mm is not None else xyz(face.center))
    normal = np.asarray(xyz(face.surf.Normal(0, 0)))
    for uv in [(0.37, 0.83), (1.23, 2.17), (-0.61, 0.49)]:
        other = np.asarray(xyz(face.surf.Normal(*uv)))
        if abs(float(np.dot(normal, other))) < 1-1e-8:
            raise ValueError("アンテナの取付面には平面の開口・人工キャップを選択してください。")
    if face.Distance(Vertex(Pnt(*center))) > 1e-5:
        raise ValueError("アンテナ中心が選択面の上にありません。")
    if antenna.inward_axis is not None:
        axis = np.asarray(antenna.inward_axis, dtype=float)
        axis /= np.linalg.norm(axis)
        if abs(float(np.dot(axis, normal))) < 1-1e-6:
            raise ValueError("同軸の挿入方向は選択した平面に垂直にしてください。")
        candidates = [axis]
    else:
        candidates = [normal, -normal]
    for axis in candidates:
        probe = _cylinder(_point(center, axis, 1e-5), axis,
                          min(antenna.pin_radius_mm*.1, .02), .02)
        if (domain*probe).mass > .999*probe.mass:
            # The whole pin disk, not merely its center, must enter the aperture.
            full = _cylinder(center, axis, antenna.pin_radius_mm, .02)
            if (domain*full).mass < .99999*full.mass:
                raise ValueError("中心導体の断面が選択開口に収まりません。中心位置か半径を変更してください。")
            return center, axis
    raise ValueError("指定面から解析空間へ挿入できません。面・中心・向きを確認してください。")


def build_antennas(domain, geometry, antennas, bulk_epsr=1.0, bulk_tand=0.0):
    """Return a conforming glued multi-material OCC domain and explicit face sets.

    Length is measured from the original selected face inward, so zero is a
    flush pin tip. The feed extends outward; its outer-conductor bore may be
    slightly wider than the selected aperture, producing a physical PEC step.
    A boundary shared by two solids is an internal material interface, never a
    metal wall. Original face IDs remain in source_face, generated IDs are new.
    """
    from netgen.occ import Glue
    if not antennas:
        return domain, geometry
    original_faces = list(dict.fromkeys(domain.faces))
    original_caps = {f["id"]: f.get("artificial_cap", False) for f in geometry["faces"]}
    placements, pins, feeds = [], [], []
    for antenna in antennas:
        if antenna.face > len(original_faces):
            raise ValueError("アンテナの取付面IDが元の解析領域にありません。")
        center, axis = _attachment(domain, original_faces[antenna.face-1], antenna)
        prefix = "antenna_%d" % len(placements)
        local_h = min((antenna.coax_outer_radius_mm-antenna.pin_radius_mm)/2,
                      antenna.pin_radius_mm, .7)
        pin = None
        if antenna.length_mm > 0:
            pin = _cylinder(center, axis, antenna.pin_radius_mm, antenna.length_mm,
                            bottom=prefix+"_pin", top=prefix+"_pin", mantle=prefix+"_pin")
            retained = float((domain*pin).mass)
            if retained < float(pin.mass)*(1-1e-6):
                raise ValueError("%s: 中心導体が壁や別の金属に衝突します。挿入長を短くしてください。" % antenna.name)
            # A finite gap is needed; exactly touching creates a different topology.
            tip_probe = _cylinder(_point(center, axis, antenna.length_mm), axis,
                                  antenna.pin_radius_mm, 1e-4)
            if (domain*tip_probe).mass < .99*tip_probe.mass:
                raise ValueError("%s: 中心導体の先端が壁に接触しています。隙間を設けてください。" % antenna.name)
            for previous in pins:
                if previous is not None and (previous*pin).mass > 1e-7:
                    raise ValueError("中心導体どうしが交差しています。配置を変更してください。")
            pin.maxh = local_h
        feed_origin = _point(center, axis, -antenna.feed_length_mm)
        feed_outer = _cylinder(feed_origin, axis, antenna.coax_outer_radius_mm,
                               antenna.feed_length_mm, bottom=prefix+"_terminal",
                               top=prefix+"_rim", mantle=prefix+"_outer")
        feed_inner = _cylinder(feed_origin, axis, antenna.pin_radius_mm, antenna.feed_length_mm,
                               bottom=prefix+"_pin", top=prefix+"_pin", mantle=prefix+"_pin")
        feed = feed_outer-feed_inner
        if (domain*feed).mass > 1e-6:
            raise ValueError("同軸の外側延長が別の解析領域と重なります。面または基準面距離を変更してください。")
        if any((previous*feed).mass > 1e-6 for previous in feeds):
            raise ValueError("同軸フィードどうしが重なっています。配置を変更してください。")
        feed.mat(prefix)
        feed.maxh = local_h
        feeds.append(feed)
        pins.append(pin)
        placements.append((antenna, center, axis, prefix, feed_origin))
    # Name the originals only after placements have been validated.
    for i, face in enumerate(original_faces):
        face.name = "original_%d" % (i+1)
    cavity = domain
    for pin in pins:
        if pin is not None:
            cavity = cavity-pin
    cavity.mat("bulk")
    result = Glue([cavity]+feeds)
    occurrences = Counter(result.faces)
    faces = list(occurrences)
    info = copy.deepcopy(geometry)
    info.pop("preview", None)
    info["source_faces"] = copy.deepcopy(geometry["faces"])
    info.update(shape_info(result))
    info["internal_faces"], info["wall_faces"], info["oxide_wall_faces"] = [], [], []
    info["materials"] = {"bulk": {"epsr": bulk_epsr, "tand": bulk_tand}}
    info["antenna_ports"], info["faces"] = [], []
    terminal_ids = {}
    for index, face in enumerate(faces, 1):
        old_name = face.name or ""
        source_id = int(old_name.split("_")[-1]) if old_name.startswith("original_") else None
        internal = occurrences[face] > 1
        terminal = old_name.endswith("_terminal")
        category = "interface" if internal else "terminal" if terminal else "cavity_wall" if source_id else "antenna_metal"
        # At length=0 the pin end is a split piece of the original cap.
        if category == "cavity_wall":
            for antenna, center, axis, prefix, feed_origin in placements:
                if antenna.length_mm == 0 and source_id == antenna.face:
                    relative = np.asarray(xyz(face.center))-center
                    if abs(float(np.dot(relative, axis))) < 1e-6 and face.mass <= math.pi*antenna.pin_radius_mm**2*(1+1e-5):
                        category = "antenna_metal"
        if internal:
            info["internal_faces"].append(index)
        elif not terminal:
            info["wall_faces"].append(index)
            if category == "cavity_wall":
                info["oxide_wall_faces"].append(index)
        if terminal:
            terminal_ids[old_name] = index
        face.name = "face_%d" % index
        lo, hi = [xyz(p) for p in face.bounding_box]
        info["faces"].append({"id": index, "area_mm2": float(face.mass),
                              "center_mm": xyz(face.center), "bbox_mm": [lo, hi],
                              "category": category, "internal": internal,
                              "source_face": source_id,
                              "artificial_cap": bool(source_id and original_caps.get(source_id))})
    for antenna, center, axis, prefix, feed_origin in placements:
        terminal = terminal_ids.get(prefix+"_terminal")
        if terminal is None:
            raise ValueError("同軸終端面を生成できませんでした。形状条件を確認してください。")
        info["materials"][prefix] = {"epsr": antenna.dielectric_epsr, "tand": antenna.dielectric_tand}
        zc = 376.730313668 / (2*math.pi*math.sqrt(antenna.dielectric_epsr))*math.log(antenna.coax_outer_radius_mm/antenna.pin_radius_mm)
        info["antenna_ports"].append({"kind": "coax_tem", "name": antenna.name,
            "faces": [terminal], "center_mm": feed_origin.tolist(), "axis": (-axis).tolist(),
            "inner_radius_mm": antenna.pin_radius_mm, "outer_radius_mm": antenna.coax_outer_radius_mm,
            "dielectric_epsr": antenna.dielectric_epsr, "reference_impedance_ohm": zc,
            "source_face": antenna.face, "mount_center_mm": center.tolist(),
            "inward_axis": axis.tolist(), "length_mm": antenna.length_mm,
            "feed_length_mm": antenna.feed_length_mm})
    info["warnings"] = list(info.get("warnings", []))+[
        "同軸断面・誘電体・取付基準面は入力したモデル条件です。初期値は一般的な例で、特定製品の寸法ではありません。",
        "挿入長は選択した元の面から内向きに測ります。0mmは先端が面と同じ位置です。",
        "アンテナの金属はPECです。金めっき・接触抵抗・誘電体の低温損失は自動推定しません。",
        "終端は均一同軸のTEMモードです。終端面距離・局所メッシュを変えてQcとS11の収束を確認してください。"]
    return result, info
