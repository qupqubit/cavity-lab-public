"""Vector H(curl) Maxwell FEM; sparse shift-invert eigensolve; SI postprocessing.

The CAD mesh is in millimetres. Eigenvalues k are in mm^-1.
Fields use peak phasors and exp(+i omega t). Positive Im(k) is decay.
"""
import json
import math
import os
import platform
import time
from pathlib import Path

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import eigsh, eigs, LinearOperator

from .config import SolveConfig
from .geometry import load_domain

C0 = 299792458.0
MU0 = 1.25663706212e-6
EPS0 = 1.0 / (MU0 * C0 * C0)
Z0 = MU0 * C0
L0 = 1e-3


def sparse_matrix(matrix, free):
    rows, cols, values = matrix.COO()
    result = sparse.csr_matrix((np.asarray(values), (np.asarray(rows), np.asarray(cols))),
                               shape=(matrix.height, matrix.width))
    return result[free][:, free].tocsc()


def loss_budget(frequency_hz, geometry_factor, p_oxide, cfg):
    if cfg.loss_model == "none":
        return {"qi": None, "q_surface": None, "q_dielectric": None,
                "inverse_q_tls": 0.0, "inverse_q_residual": 0.0}
    thermal = math.tanh(6.62607015e-34 * frequency_hz / (2 * 1.380649e-23 * cfg.temperature_k))
    saturation = math.sqrt(1 + (cfg.photon_number / cfg.tls_nc) ** cfg.tls_beta)
    tls = p_oxide * cfg.oxide_tand * thermal / saturation
    surface = (cfg.rs_nohm or 0.0) * 1e-9 / geometry_factor if cfg.loss_model == "surface_rs" else 0.0
    residual = 1.0 / cfg.q_res if cfg.loss_model == "tls_qres" and cfg.q_res else 0.0
    dielectric = cfg.bulk_tand + tls
    inverse = surface + residual + dielectric
    return {"qi": 1 / inverse if inverse > 0 else None,
            "q_surface": 1 / surface if surface > 0 else None,
            "q_dielectric": 1 / dielectric if dielectric > 0 else None,
            "inverse_q_tls": tls, "inverse_q_residual": residual}


def solve(path, config, output_dir=None, progress=print, domain_override=None, geometry_override=None):
    import ngsolve as ng
    from netgen.occ import OCCGeometry
    cfg = config if isinstance(config, SolveConfig) else SolveConfig(**config)
    start = time.perf_counter()
    ng.SetNumThreads(cfg.threads)
    if domain_override is None:
        domain, geometry = load_domain(path, cfg.geometry_mode, cfg.solid_index)
    else:
        from .geometry import face_info, shape_info
        domain = domain_override
        if geometry_override is None:
            domain.mat("bulk")
            for i, face in enumerate(domain.faces):
                face.name = "face_%d" % (i + 1)
            geometry = {**shape_info(domain), "faces": face_info(domain), "warnings": []}
        else:
            geometry = geometry_override
    if cfg.antennas:
        from .antennas import build_antennas
        domain, geometry = build_antennas(domain, geometry, cfg.antennas, bulk_epsr=cfg.bulk_epsr, bulk_tand=cfg.bulk_tand)
    from .boundaries import assign_external_boundaries
    geometry = assign_external_boundaries(geometry, cfg)
    if cfg.analysis_mode == "all_openings_qc" or cfg.all_artificial_caps or cfg.antennas or cfg.spectrum_enabled or cfg.mode_search == "lowest" or geometry.get("antenna_ports"):
        from .spectrum import solve_advanced
        return solve_advanced(domain, geometry, cfg, output_dir, progress, time.perf_counter()-start)
    nfaces = len(domain.faces)
    port_faces = {f for p in cfg.ports for f in p.faces}
    if any(f > nfaces for f in port_faces):
        raise ValueError("ポート面IDが解析領域の面数を超えています。")
    wall_names = ["face_%d" % f for f in range(1, nfaces + 1) if f not in port_faces]
    if not wall_names:
        raise ValueError("少なくとも1つのPEC壁面が必要です。")
    geometry_s = time.perf_counter() - start
    progress("曲面四面体メッシュを生成中")
    stamp = time.perf_counter()
    mesh = ng.Mesh(OCCGeometry(domain).GenerateMesh(maxh=cfg.maxh_mm))
    mesh.Curve(cfg.order + 1)
    mesh_s = time.perf_counter() - stamp
    progress("Maxwell行列を組み立て中")
    stamp = time.perf_counter()
    fes = ng.HCurl(mesh, order=cfg.order, dirichlet="|".join(wall_names), complex=bool(cfg.ports))
    from .resources import check_mesh_memory
    resource_policy = check_mesh_memory(fes.ndof, cfg, progress)
    u, v = fes.TnT()
    with ng.TaskManager():
        a = ng.BilinearForm(ng.curl(u) * ng.curl(v) * ng.dx).Assemble()
        m = ng.BilinearForm(cfg.bulk_epsr * u * v * ng.dx).Assemble()
    free = np.flatnonzero(np.array(list(fes.FreeDofs()), dtype=bool))
    K, M = sparse_matrix(a.mat, free), sparse_matrix(m.mat, free)
    assembly_s = time.perf_counter() - stamp
    progress("固有モードを計算中（%d自由度）" % len(free))
    stamp = time.perf_counter()
    target = 2 * math.pi * cfg.target_ghz * 1e9 / C0 * L0
    rng = np.random.default_rng(42)
    count = min(max(cfg.n_modes + 3, 6), len(free) - 2)
    projector = None
    projected = None
    if cfg.n_modes > 1:
        # curl(grad(phi))=0 gives many zero modes that can be closer to the
        # frequency shift than the second physical resonance. Remove only
        # gradients of scalar potentials vanishing on ALL exterior boundaries.
        # In particular this keeps port-associated potentials in the QEP.
        gradient, scalar_space = fes.CreateGradient()
        gradient_t = gradient.CreateTranspose()
        scalar_free = scalar_space.FreeDofs() & ~scalar_space.GetDofs(mesh.Boundaries(".*"))
        if any(scalar_free):
            with ng.TaskManager():
                projection_mass = m.mat
                if cfg.ports:
                    # This lossless bulk mass is real even for complex modes.
                    # NGSolve 6.2.2506 cannot materialize mixed real/complex
                    # sparse triple products. The identical real basis avoids
                    # that backend limitation. An explicit real-to-complex
                    # wrapper below applies it to both phasor components.
                    projection_space = ng.HCurl(mesh, order=cfg.order, dirichlet="|".join(wall_names))
                    pu, pv = projection_space.TnT()
                    real_mass = ng.BilinearForm(cfg.bulk_epsr*pu*pv*ng.dx).Assemble()
                    projection_mass = real_mass.mat
                scalar_mass = gradient_t @ projection_mass @ gradient
                scalar_inverse = scalar_mass.Inverse(freedofs=scalar_free, inverse="sparsecholesky")
            projector = ng.IdentityMatrix() - gradient @ scalar_inverse @ gradient_t @ projection_mass
            if cfg.ports:
                projector = ng.la.Real2ComplexMatrix(projector)
            projected = ng.GridFunction(fes).vec.CreateVector()

    def free_solution(solution):
        if projector is not None:
            projected.data = projector * solution
            return projected.FV().NumPy()[free].copy()
        return solution.FV().NumPy()[free].copy()

    projection_input = ng.GridFunction(fes).vec.CreateVector() if projector is not None else None
    def project_free(x):
        if projector is None:
            return x
        projection_input.FV().NumPy()[:] = 0
        projection_input.FV().NumPy()[free] = x
        return free_solution(projection_input)

    port_matrices = []
    resource_policy = check_mesh_memory(fes.ndof, cfg)
    if not cfg.ports:
        shifted = ng.BilinearForm(ng.curl(u)*ng.curl(v)*ng.dx - target**2*cfg.bulk_epsr*u*v*ng.dx,
                                 symmetric=True).Assemble()
        with ng.TaskManager():
            inverse = shifted.mat.Inverse(freedofs=fes.FreeDofs(), inverse="sparsecholesky")
        rhs, solution = [ng.GridFunction(fes).vec.CreateVector() for _ in range(2)]
        def inverse_apply(x):
            rhs.FV().NumPy()[:] = 0
            rhs.FV().NumPy()[free] = x
            solution.data = inverse * rhs
            return free_solution(solution)
        opinv = LinearOperator(K.shape, matvec=inverse_apply, dtype=float)
        values, vectors = eigsh(K, k=count, M=M, sigma=target ** 2, which="LM", OPinv=opinv,
                                tol=1e-9, maxiter=2000, v0=rng.normal(size=len(free)))
        candidates = [(complex(math.sqrt(ev)), vectors[:, i]) for i, ev in enumerate(values)
                      if ev > 1e-8 * target ** 2]
    else:
        # K + i*k*D - k^2*M = 0. D contains surface wave admittance Z0/Zs.
        # This is an impedance boundary, not a numeric wave-port mode solver.
        D = sparse.csc_matrix(K.shape, dtype=complex)
        for port in cfg.ports:
            names = "|".join("face_%d" % f for f in port.faces)
            # This matrix intentionally has only boundary support.
            b = ng.BilinearForm(u.Trace() * v.Trace() * ng.ds(definedon=mesh.Boundaries(names)),
                               check_unused=False).Assemble()
            mat = sparse_matrix(b.mat, free)
            port_matrices.append(mat)
            D = D + (Z0 / port.impedance_ohm) * mat
        n = len(free)
        # Matrix-free companion shift-inverse avoids factoring a dense 2N system.
        shifted = ng.BilinearForm(ng.curl(u)*ng.curl(v)*ng.dx - target**2*cfg.bulk_epsr*u*v*ng.dx,
                                 symmetric=True)
        for port in cfg.ports:
            names = "|".join("face_%d" % f for f in port.faces)
            shifted += 1j*target*(Z0/port.impedance_ohm)*u.Trace()*v.Trace()*ng.ds(definedon=mesh.Boundaries(names))
        shifted.Assemble()
        with ng.TaskManager():
            inverse = shifted.mat.Inverse(freedofs=fes.FreeDofs(), inverse="sparsecholesky")
        rhs, solution = [ng.GridFunction(fes).vec.CreateVector() for _ in range(2)]
        def inverse_apply(x):
            rhs.FV().NumPy()[:] = 0
            rhs.FV().NumPy()[free] = x
            solution.data = inverse * rhs
            return free_solution(solution)
        def shift_inverse(vector):
            x, y = vector[:n], vector[n:]
            z = inverse_apply(M @ y - (1j * D - target * M) @ x)
            return np.concatenate([z, project_free(x) + target * z])
        op = LinearOperator((2*n, 2*n), matvec=shift_inverse, dtype=complex)
        transformed, companion = eigs(op, k=count, which="LM", tol=1e-9, maxiter=2000,
                                      v0=rng.normal(size=2*n).astype(complex))
        values = target + 1 / transformed
        candidates = [(complex(k), companion[:n, i]) for i, k in enumerate(values)
                      if k.real > target * 1e-4 and k.imag > -target * 1e-7]
    if len(candidates) < cfg.n_modes:
        raise RuntimeError("指定帯域で十分な非零固有モードを求められませんでした。探索周波数またはモード数を調整してください。")
    candidates.sort(key=lambda item: abs(item[0].real - target))
    candidates = sorted(candidates[:cfg.n_modes], key=lambda item: item[0].real)
    solve_s = time.perf_counter() - stamp
    progress("壁面損失・ポート損失・participationを積分中（NGSolve最大%dスレッド）" % cfg.threads)
    stamp = time.perf_counter()
    modes = []
    # Full volume traces are needed: HCurl's default boundary value keeps only E_t.
    from .surface import surface_integrals
    # Keep native integration/output tasks active throughout postprocessing.
    with ng.TaskManager():
        for mode_index, (k, vector) in enumerate(candidates):
            frequency = k.real * C0 / (2 * math.pi * L0)
            omega = 2 * math.pi * frequency
            vector = vector / math.sqrt(float(np.vdot(vector, M @ vector).real))
            g = ng.GridFunction(fes)
            g.vec.FV().NumPy()[:] = 0
            g.vec.FV().NumPy()[free] = vector if cfg.ports else vector.real
            E2 = float(np.vdot(vector, M @ vector).real) / cfg.bulk_epsr
            curlE = ng.curl(g)
            curl2 = float(np.vdot(vector, K @ vector).real)
            # H peak amplitude from Faraday's law; magnitude uses complex eigenfrequency.
            h_scale = 1 / (abs(k) * C0 * MU0)
            Ue = 0.25 * EPS0 * cfg.bulk_epsr * E2 * L0 ** 3
            Um = 0.25 * MU0 * curl2 * h_scale ** 2 * L0 ** 3
            U = Ue + Um
            faces = surface_integrals(mesh, g, order=2*cfg.order+4)
            wall_H2 = sum(f["curl_t2_mm2"] for f in faces if f["id"] not in port_faces) * h_scale ** 2 * L0 ** 2
            G = omega * U / (0.5 * wall_H2) if wall_H2 > 0 else float("inf")
            Se = sum(f["e_normal2_mm2"] for f in faces if f["id"] not in port_faces) / E2 / L0
            # D_n continuity: oxide E_n = eps_bulk/eps_oxide * E_n,bulk.
            p_oxide = cfg.oxide_thickness_nm * 1e-9 * cfg.bulk_epsr / cfg.oxide_epsr * Se
            ports = []
            for port, mat in zip(cfg.ports, port_matrices):
                power = 0.5 / port.impedance_ohm * float(np.vdot(vector, mat @ vector).real) * L0 ** 2
                ports.append({"name": port.name, "qc": omega * U / power if power > 1e-30 else None,
                              "power_w_normalized": power, "faces": port.faces,
                              "impedance_ohm": port.impedance_ohm, "kind": port.kind, "source_faces":port.faces,
                              "qc_cavity":omega*U/power if power>1e-30 else None,
                              "qc_total_energy":omega*U/power if power>1e-30 else None})
            outgoing_power=sum(port["power_w_normalized"] for port in ports)
            q_external=omega*U/outgoing_power if outgoing_power>1e-30 else None
            budget = loss_budget(frequency, G, p_oxide, cfg)
            inverse_loaded = (1 / budget["qi"] if budget["qi"] else 0) + sum(1 / p["qc"] for p in ports if p["qc"])
            residual_vec = K @ vector - k*k*(M @ vector)
            if cfg.ports:
                residual_vec += 1j * k * (D @ vector)
            residual = np.linalg.norm(residual_vec) / (np.linalg.norm(K @ vector) + abs(k)**2*np.linalg.norm(M @ vector))
            if not np.isfinite(residual) or residual > 1e-6:
                raise RuntimeError("固有対の残差が許容値を超えました。探索周波数またはメッシュを調整してください。")
            modes.append({"mode": mode_index + 1, "frequency_ghz": frequency / 1e9,
                          "frequency_imag_ghz": k.imag * C0 / (2*math.pi*L0*1e9),
                          **budget, "q_loaded": 1/inverse_loaded if inverse_loaded > 0 else None,
                          "q_ports_eigenvalue": abs(k.real / (2*k.imag)) if abs(k.imag) > 1e-14 else None,
                          "q_external_cavity":q_external,"q_external_total_energy":q_external,
                          "inverse_q_external_cavity":sum(1/p["qc"] for p in ports if p["qc"]),
                          "inverse_q_external_total_energy":sum(1/p["qc"] for p in ports if p["qc"]),
                          "cavity_energy_j_normalized":U,"total_energy_j_normalized":U,
                          "cavity_electric_energy_j_normalized":Ue,"cavity_magnetic_energy_j_normalized":Um,
                          "outgoing_power_w_normalized":outgoing_power,
                          "qc_energy_scope":"total modeled domain; equal to cavity because no feed volume is present",
                          "energy_normalization":"Arbitrary peak-phasor eigenmode amplitude with x^H M x=1 in native mm coordinates; energies and powers share this scale; Q is amplitude-independent.",
                          "geometry_factor_ohm": G, "surface_electric_per_m": Se,
                          "participation": {"bulk": 1.0, "oxide": p_oxide},
                          "surface_participation": [{"face": f["id"], "p_oxide": 0.0 if f["id"] in port_faces else cfg.oxide_thickness_nm*1e-6 * cfg.bulk_epsr/cfg.oxide_epsr * f["e_normal2_mm2"] / E2,
                                                       "is_port": f["id"] in port_faces} for f in faces],
                          "ports": ports, "residual": float(residual),
                          "energy_balance_relative": abs(Ue-Um)/U,
                          "electric_energy_j_normalized": Ue, "magnetic_energy_j_normalized": Um})
            if output_dir:
                out = Path(output_dir)
                out.mkdir(parents=True, exist_ok=True)
                ng.VTKOutput(ma=mesh, coefs=[g.real, g.imag, ng.Norm(g), ng.Norm(curlE)*h_scale],
                             names=["E_real", "E_imag", "E_magnitude", "H_magnitude"],
                             filename=str(out / ("mode_%02d" % (mode_index+1))), subdivision=0).Do()
                from .fields import export_field_slices
                modes[-1]["field_file"] = export_field_slices(mesh, g, geometry, out, mode_index+1, cfg.field_resolution)
    post_s = time.perf_counter() - stamp
    warnings = list(geometry.get("warnings", []))
    warnings += ["有限要素残差は離散化誤差ではありません。周波数・G・表面participationを別々にメッシュ収束確認してください。",
                 "酸化膜はPEC壁の法線電場から求める薄膜摂動近似です。鋭い角の値は丸みとメッシュに依存します。"]
    if not cfg.ports:
        warnings.append("ポート未定義：全境界PECの閉じた空洞です。実機Qcは算出していません。")
    else:
        warnings.append("ポートは指定面の局所表面波インピーダンス境界です。SMAの回路50Ωをそのまま入力するモデルではありません。結合プローブ・誘電体を含むCADと適切な終端面が必要です。")
    if cfg.loss_model == "tls_qres":
        warnings.append("Qiは入力したTLS係数・残留Qに依存します。初期値は材料の実測値ではありません。")
        if cfg.temperature_k >= 1.2:
            warnings.append("TLSモデルの高温での適用性を確認してください。準粒子損失の温度依存性は計算していません。")
    result = {"solver": "NGSolve", "solver_version": ng.__version__, "schema_version": 1,
              "config": cfg.model_dump(), "geometry": geometry, "modes": modes, "memory_policy": resource_policy,
              "analysis_mode":cfg.analysis_mode,"external_boundary_assignments":geometry.get("external_ports",[]),
              "mesh": {"elements": mesh.ne, "dofs": len(free), "total_dofs": fes.ndof,
                       "maxh_mm": cfg.maxh_mm, "order": cfg.order, "geometry_order": cfg.order+1},
              "timings": {"geometry_s": geometry_s, "mesh_s": mesh_s, "assembly_s": assembly_s,
                          "solve_s": solve_s, "postprocess_s": post_s, "total_s": time.perf_counter()-start},
              "machine": {"platform": platform.platform(), "processor": platform.machine(), "threads": cfg.threads,
                          "postprocess_parallelism": {
                              "task_manager_enabled": True, "requested_threads": cfg.threads,
                              "scope": "native_ngsolve_postprocessing", "python_loop_parallel": False,
                              "field_sampling": "native_batch_with_scalar_fallback",
                              "note": "TaskManager enables eligible native integration and field evaluation. Python bookkeeping, scalar fallback and VTK/JSON file output remain serial; requested threads do not guarantee speedup."}},
              "warnings": warnings}
    if output_dir:
        Path(output_dir, "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return result
