"""Evaluate the full, adjacent-volume Maxwell fields on exterior CAD faces.

The natural HCurl trace contains E_t only, while the boundary trace of curl(E)
contains its normal component only. Neither is suitable for oxide E_n or wall
H_t loss. BoundaryFromVolumeCF keeps the components required for both losses.
These integrals use the mesh's native length unit, millimetres in this app.
"""
import re


def surface_integrals(mesh, g, order=6):
    """Return per-CAD-face squared fields, before conversion to SI units.

    This function assumes each integration face borders one computational
    volume, as in a cavity vacuum mesh. An internal material interface requires
    explicit side selection, which this single-domain implementation omits.
    ``curl_t2_mm2`` integrates the square of the native-coordinate curl. Its
    field units still include inverse millimetres; the name denotes dS only.
    """
    import ngsolve as ng

    electric = ng.BoundaryFromVolumeCF(g)
    curl = ng.BoundaryFromVolumeCF(ng.curl(g))
    normal = ng.specialcf.normal(3)
    en = electric * normal
    cn = curl * normal
    et = electric - en * normal
    ct = curl - cn * normal

    def squared(value):
        return (value * ng.Conj(value)).real

    integrands = {
        "e_normal2_mm2": squared(en),
        "e_tangent2_mm2": squared(et),
        "curl_t2_mm2": squared(ct),
        "curl_normal2_mm2": squared(cn),
    }
    region_values = {
        key: ng.Integrate(cf, mesh, ng.BND, order=order, region_wise=True)
        for key, cf in integrands.items()
    }
    faces = {}
    for index, name in enumerate(mesh.GetBoundaries()):
        match = re.fullmatch(r"face_([1-9][0-9]*)", name)
        face_id = int(match.group(1)) if match else index + 1
        record = faces.setdefault(face_id, {"id": face_id, "name": name,
                                           **{key: 0.0 for key in integrands}})
        for key, values in region_values.items():
            record[key] += max(0.0, float(values[index]))
    for record in faces.values():
        record["e_full2_mm2"] = record["e_normal2_mm2"] + record["e_tangent2_mm2"]
        record["curl_full2_mm2"] = record["curl_normal2_mm2"] + record["curl_t2_mm2"]
    return [faces[key] for key in sorted(faces)]
