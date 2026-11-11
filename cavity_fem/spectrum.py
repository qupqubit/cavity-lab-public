"""Full driven Maxwell FEM with analytic TEM ports and wide-band eigenmode search.

Coordinates are mm, fields are peak phasors with exp(+i omega t). TEM ports
project onto the normalized coax radial field: they are modal ports, not a
local plane-wave impedance. Higher transverse port modes are not retained.
"""
import json
import math
import platform
import time
from pathlib import Path

import numpy as np
from scipy.sparse.linalg import LinearOperator, eigs

from .solver import C0, MU0, EPS0, Z0, L0, sparse_matrix, loss_budget


def coax_impedance(inner_radius, outer_radius, epsr):
    return Z0 * math.log(outer_radius / inner_radius) / (2 * math.pi * math.sqrt(epsr))


def coax_te11_cutoff_ghz(inner_radius_mm, outer_radius_mm, epsr):
    """First higher coax mode from the annular TE Bessel eigen-equation."""
    from scipy.optimize import brentq
    from scipy.special import jvp, yvp
    ratio=inner_radius_mm/outer_radius_mm
    def equation(x):
        return jvp(1,ratio*x)*yvp(1,x)-yvp(1,ratio*x)*jvp(1,x)
    previous=.001; value=equation(previous)
    for current in np.linspace(.01,30,3000):
        next_value=equation(current)
        if value*next_value<0:
            root=brentq(equation,previous,current)
            return C0*root/(2*math.pi*outer_radius_mm*L0*math.sqrt(epsr)*1e9)
        previous,value=current,next_value
    raise ValueError('同軸高次モードの遮断周波数を計算できませんでした。')


class MaxwellSystem:
    """One curved mesh and FE basis reused by all real-frequency solves."""
    def __init__(self, domain, geometry, cfg, progress=lambda _: None):
        import ngsolve as ng
        from netgen.occ import OCCGeometry
        self.ng, self.cfg, self.geometry = ng, cfg, geometry
        ng.SetNumThreads(cfg.threads)
        start = time.perf_counter()
        self.mesh = mesh = ng.Mesh(OCCGeometry(domain).GenerateMesh(maxh=cfg.maxh_mm))
        mesh.Curve(cfg.order + 1)
        self.mesh_s = time.perf_counter() - start
        start = time.perf_counter()
        from .config import Port
        self.ports = geometry.get('antenna_ports', [])
        self.external_port_data = geometry.get('external_ports', [port.model_dump() for port in cfg.ports])
        self.local_configs = [Port(**{key:value for key,value in port.items() if key in Port.model_fields})
                              for port in self.external_port_data]
        self.local_faces = {face for port in self.local_configs for face in port.faces}
        self.port_faces = {face for port in self.ports for face in port['faces']} | self.local_faces
        self.wall_faces = set(geometry.get('wall_faces', [f['id'] for f in geometry['faces']
                              if not f.get('internal', False) and f['id'] not in self.port_faces]))
        self.wall_faces -= self.port_faces
        self.oxide_faces = set(geometry.get('oxide_wall_faces', self.wall_faces)) - self.port_faces
        self.wall_names = '|'.join('face_%d' % face for face in sorted(self.wall_faces))
        if not self.wall_names:
            raise ValueError('少なくとも1つの金属壁面が必要です。')
        self.wall_region = mesh.Boundaries(self.wall_names)
        self.oxide_region = mesh.Boundaries('|'.join('face_%d' % f for f in sorted(self.oxide_faces)))
        self.materials = geometry.get('materials', {'bulk': {'epsr': cfg.bulk_epsr, 'tand': cfg.bulk_tand}})
        self.eps = mesh.MaterialCF({name: value['epsr'] for name, value in self.materials.items()}, default=cfg.bulk_epsr)
        self.eps_tand = mesh.MaterialCF({name: value['epsr'] * value.get('tand', 0) for name, value in self.materials.items()}, default=cfg.bulk_epsr * cfg.bulk_tand)
        self.fes = fes = ng.HCurl(mesh, order=cfg.order, dirichlet=self.wall_names, complex=True)
        from .resources import check_mesh_memory
        self.memory_policy = check_mesh_memory(fes.ndof, cfg, progress)
        self.u, self.v = u, v = fes.TnT()
        with ng.TaskManager():
            self.a = ng.BilinearForm(ng.curl(u)*ng.curl(v)*ng.dx).Assemble()
            self.m = ng.BilinearForm(self.eps*u*v*ng.dx).Assemble()
        self.free = np.flatnonzero(np.array(list(fes.FreeDofs()), dtype=bool))
        self.K, self.M = sparse_matrix(self.a.mat, self.free), sparse_matrix(self.m.mat, self.free)
        self.n = len(self.free)
        self.local = []
        for port in self.local_configs:
            region = mesh.Boundaries('|'.join('face_%d' % face for face in port.faces))
            form = ng.BilinearForm(u.Trace()*v.Trace()*ng.ds(definedon=region), check_unused=False).Assemble()
            self.local.append((port, region, sparse_matrix(form.mat, self.free)))
        vectors, self.port_data = [], []
        for port in self.ports:
            axis = np.asarray(port['axis'], dtype=float); axis /= np.linalg.norm(axis)
            center = np.asarray(port['center_mm'], dtype=float)
            a, b, epsr = port['inner_radius_mm'], port['outer_radius_mm'], port['dielectric_epsr']
            direction = ng.CF(tuple(axis)); position = ng.CF((ng.x-center[0], ng.y-center[1], ng.z-center[2]))
            radial = position - (position*direction)*direction
            r2 = radial*radial
            mode = radial / (r2 * math.sqrt(2*math.pi*math.log(b/a)))
            region = mesh.Boundaries('|'.join('face_%d' % face for face in port['faces']))
            overlap = ng.LinearForm(mode*v.Trace()*ng.ds(definedon=region, bonus_intorder=12)).Assemble()
            vectors.append(overlap.vec.FV().NumPy()[self.free].real.copy())
            norm = float(ng.Integrate(mode*mode, mesh, ng.BND, definedon=region, order=2*cfg.order+6))
            if abs(norm-1) > .03:
                raise ValueError('同軸終端のTEM正規化が3%以内で一致しません。終端面と中心・半径を確認してください。')
            self.port_data.append({**port, 'modal_norm': norm, 'characteristic_impedance_ohm': coax_impedance(a,b,epsr),
                                   'wave_admittance_s': math.sqrt(epsr)/Z0, 'gamma': math.sqrt(epsr),
                                   'te11_cutoff_ghz': coax_te11_cutoff_ghz(a,b,epsr)})
        self.B = np.column_stack(vectors) if vectors else np.zeros((self.n,0))
        self.gamma = np.asarray([p['gamma'] for p in self.port_data])
        self.projector = None
        gradient, scalar = fes.CreateGradient()
        # The annular TEM profile is surface-divergence-free. For scalar
        # potentials zero on the conductor rims, its overlap with grad_t(phi)
        # vanishes by integration by parts. Port scalar DOFs therefore belong
        # to the zero-curl/zero-TEM-loss kernel and can be deflated too. Local
        # Robin ports damp those gradients, so preserve their boundary DOFs.
        scalar_zero_faces = self.wall_faces | self.local_faces
        scalar_zero_names = '|'.join('face_%d' % face for face in sorted(scalar_zero_faces))
        scalar_free = scalar.FreeDofs() & ~scalar.GetDofs(mesh.Boundaries(scalar_zero_names))
        if any(scalar_free):
            real_space = ng.HCurl(mesh, order=cfg.order, dirichlet=self.wall_names)
            ru, rv = real_space.TnT()
            real_mass = ng.BilinearForm(self.eps*ru*rv*ng.dx).Assemble()
            gt = gradient.CreateTranspose()
            mass_grad = gt @ real_mass.mat @ gradient
            inv_grad = mass_grad.Inverse(freedofs=scalar_free, inverse='sparsecholesky')
            self.projector = ng.la.Real2ComplexMatrix(ng.IdentityMatrix() - gradient @ inv_grad @ gt @ real_mass.mat)
        self.proj_in = ng.GridFunction(fes).vec.CreateVector()
        self.proj_out = ng.GridFunction(fes).vec.CreateVector()
        self.loss_matrices = None
        self.assembly_s = time.perf_counter() - start

    def project(self, x):
        if self.projector is None:
            return x
        self.proj_in.FV().NumPy()[:] = 0
        self.proj_in.FV().NumPy()[self.free] = x
        self.proj_out.data = self.projector * self.proj_in
        return self.proj_out.FV().NumPy()[self.free].copy()

    def damping(self, x):
        result = self.B @ (self.gamma * (self.B.T @ x))
        for port, _, mat in self.local:
            result = result + Z0/port.impedance_ohm * (mat @ x)
        return result

    def _loss_terms(self, k):
        ng, cfg, u, v = self.ng, self.cfg, self.u, self.v
        if cfg.loss_model == 'none':
            return []
        frequency = k*C0/(2*math.pi*L0)
        thermal = math.tanh(6.62607015e-34*frequency/(2*1.380649e-23*cfg.temperature_k))
        tls = cfg.oxide_tand*thermal / math.sqrt(1+(cfg.photon_number/cfg.tls_nc)**cfg.tls_beta)
        n = ng.specialcf.normal(3)
        terms = [1j*k*k*self.eps_tand*u*v*ng.dx]
        if cfg.loss_model == 'tls_qres' and cfg.q_res:
            terms.append(1j*k*k/cfg.q_res*self.eps*u*v*ng.dx)
        if tls and cfg.oxide_thickness_nm and self.oxide_faces:
            terms.append(1j*k*k*tls*cfg.oxide_thickness_nm*1e-6/cfg.oxide_epsr * self.eps**2 *
                         (u*n)*(v*n)*ng.ds(skeleton=True, definedon=self.oxide_region))
        if cfg.loss_model == 'surface_rs' and cfg.rs_nohm:
            cu, cv = ng.curl(u), ng.curl(v)
            terms.append(1j*cfg.rs_nohm*1e-9/(k*Z0) * (cu-(cu*n)*n)*(cv-(cv*n)*n) *
                         ng.ds(skeleton=True, definedon=self.oxide_region if self.port_data else self.wall_region))
        return terms

    def inverse(self, k, losses=False):
        """Native sparse factor plus an exact small Woodbury TEM correction."""
        from .resources import check_mesh_memory
        self.memory_policy = check_mesh_memory(self.fes.ndof, self.cfg)
        ng, u, v = self.ng, self.u, self.v
        form = ng.BilinearForm(ng.curl(u)*ng.curl(v)*ng.dx-k*k*self.eps*u*v*ng.dx, symmetric=True)
        for port, region, _ in self.local:
            form += 1j*k*(Z0/port.impedance_ohm)*u.Trace()*v.Trace()*ng.ds(definedon=region)
        if losses:
            for term in self._loss_terms(k):
                form += term
        form.Assemble()
        with ng.TaskManager():
            native_inv = form.mat.Inverse(freedofs=self.fes.FreeDofs(), inverse='sparsecholesky')
        rhs, sol = [ng.GridFunction(self.fes).vec.CreateVector() for _ in range(2)]
        def base(x):
            rhs.FV().NumPy()[:] = 0; rhs.FV().NumPy()[self.free] = x
            sol.data = native_inv*rhs
            return sol.FV().NumPy()[self.free].copy()
        if self.B.shape[1]:
            responses = np.column_stack([base(column) for column in self.B.T])
            small = np.diag(1/(1j*k*self.gamma)) + self.B.T @ responses
            def apply(x):
                y = base(x)
                return y - responses @ np.linalg.solve(small, self.B.T @ y)
        else:
            apply = base
        # Keep the native assembled form alive for the lifetime of its inverse.
        apply._native_form = form
        return apply

    def eigenpairs(self, target_ghz, count):
        k0 = 2*math.pi*target_ghz*1e9/C0*L0
        inverse = self.inverse(k0)
        n = self.n
        def shift_inverse(z):
            x, y = z[:n], z[n:]
            solution = self.project(inverse(self.M@y - 1j*self.damping(x) + k0*(self.M@x)))
            return np.concatenate((solution, self.project(x)+k0*solution))
        operator = LinearOperator((2*n,2*n), matvec=shift_inverse, dtype=complex)
        vals, vectors = eigs(operator, k=min(count,2*n-2), which='LM', tol=1e-9, maxiter=2500,
                             v0=np.random.default_rng(42).normal(size=2*n).astype(complex))
        pairs = []
        for index, k in enumerate(k0 + 1/vals):
            if k.real <= 1e-7 or k.imag < -1e-8*max(k0,k.real):
                continue
            vector = vectors[:n,index]
            weight = float(np.vdot(vector,self.M@vector).real)
            if weight <= 0:
                continue
            vector = vector/math.sqrt(weight)
            residual = self.residual(k,vector)
            if residual < 1e-6:
                pairs.append((complex(k),vector))
        return pairs

    def residual(self,k,vector):
        kval,mval = self.K@vector,self.M@vector
        return float(np.linalg.norm(kval-k*k*mval+1j*k*self.damping(vector)) /
                     (np.linalg.norm(kval)+abs(k)**2*np.linalg.norm(mval)))

    def grid_function(self, vector):
        field = self.ng.GridFunction(self.fes)
        field.vec.FV().NumPy()[:] = 0
        field.vec.FV().NumPy()[self.free] = vector
        return field

    def driven(self, frequency_ghz, driven_port=0):
        """One watt incident TEM wave, solved directly on the full FE system."""
        if not self.port_data:
            raise ValueError('S11には同軸アンテナポートが必要です。')
        k = 2*math.pi*frequency_ghz*1e9/C0*L0
        admittance = self.port_data[driven_port]['wave_admittance_s']
        incident = math.sqrt(2/admittance)/L0
        rhs = 2j*k*self.gamma[driven_port]*self.B[:,driven_port]*incident
        vector = self.inverse(k,losses=True)(rhs)
        total = self.B.T@vector
        outgoing = total.copy(); outgoing[driven_port] -= incident
        scattering = np.asarray([math.sqrt(p['wave_admittance_s']/admittance)*alpha/incident
                                 for p,alpha in zip(self.port_data,outgoing)])
        # P_abs follows the FE weak-form power identity; all loss operators are positive.
        kva,mva = self.K@vector,self.M@vector
        operator_without_port = kva-k*k*mva
        # Reuse the same physical loss forms to check absorbed power independently of S.
        absorbed = 0.0
        if self.cfg.loss_model != 'none':
            ng=self.ng
            loss_form=ng.BilinearForm(self.fes,symmetric=True,check_unused=False)
            for term in self._loss_terms(k): loss_form += term
            loss_form.Assemble()
            g=self.grid_function(vector)
            loss_vector=g.vec.CreateVector()
            loss_vector.data=loss_form.mat*g.vec
            quadratic=complex(np.vdot(g.vec.FV().NumPy(), loss_vector.FV().NumPy()))
            absorbed=max(0.0,quadratic.imag*L0**2/(2*k*Z0))
        output=float(np.sum(np.abs(scattering)**2))
        return {'scattering':scattering,'vector':vector,'input_power_w':1.0,
                'output_power_w':output,'absorbed_power_w':absorbed,
                'power_balance_error':abs(1-output-absorbed)}


def search_modes(system, cfg, progress):
    count = max(10,2*cfg.n_modes+4)
    if cfg.mode_search == 'lowest':
        lo,hi = cfg.spectrum_start_ghz,cfg.spectrum_stop_ghz
        # Closed cavity gradients are exactly deflated, so one low shift returns
        # the first positive eigenvalues. Open ports retain physical boundary
        # potentials; multiple shifts protect against purely imaginary poles.
        shifts = [lo] if not system.port_data and not system.local else sorted(set(np.linspace(lo,hi,7).tolist()+[cfg.target_ghz]))
    else:
        lo,hi = 0,float('inf'); shifts=[cfg.target_ghz]
    found=[]; shift_results=[]
    for shift in shifts:
        progress('固有モード探索 %.4g GHz' % shift)
        at_shift=system.eigenpairs(shift,count)
        shift_results.append({'shift_ghz':shift,'positive_frequencies_ghz':sorted(k.real*C0/(2*math.pi*L0*1e9) for k,_ in at_shift)})
        for k,vector in at_shift:
            frequency=k.real*C0/(2*math.pi*L0*1e9)
            if frequency < lo*(1-1e-8) or frequency > hi*(1+1e-8): continue
            # Preserve degenerate orthogonal modes while removing rediscovered ones.
            if any(abs(k-other[0])<1e-5*max(abs(k),abs(other[0])) and
                   abs(np.vdot(vector,system.M@other[1]))>.9 for other in found): continue
            found.append((k,vector))
    if not found:
        raise RuntimeError('指定帯域で検証済みの非零固有モードを発見できませんでした。帯域・メッシュを変更してください。')
    if cfg.mode_search=='target': found.sort(key=lambda item:abs(item[0].real*C0/(2*math.pi*L0*1e9)-cfg.target_ghz))
    else: found.sort(key=lambda item:item[0].real)
    selected=sorted(found[:cfg.n_modes],key=lambda item:item[0].real)
    return selected, {'method':'gradient-deflated shift-invert Maxwell eigenproblem', 'selection':cfg.mode_search,
                      'shifts_ghz':shifts,'shift_results':shift_results,'band_ghz':[cfg.spectrum_start_ghz,cfg.spectrum_stop_ghz] if cfg.mode_search=='lowest' else None,
                      'distinct_modes_found':len(found),'requested_modes':cfg.n_modes,'returned_modes':len(selected),
                      'claim':'lowest found in searched band' if cfg.mode_search=='lowest' else 'nearest computed modes to target',
                      'completeness_proven':False,'note':'S11 invisibility does not exclude a dark eigenmode; mesh and search convergence remain necessary.'}


def spectrum_samples(system,cfg,modes,progress):
    if not system.port_data:
        return {'available':False,'reason':'S11には同軸アンテナの定義が必要です。','frequencies_ghz':[]}
    names=[p['name'] for p in system.port_data]
    driven=names.index(cfg.driven_port) if cfg.driven_port else 0
    frequencies=set(np.linspace(cfg.spectrum_start_ghz,cfg.spectrum_stop_ghz,cfg.spectrum_points).tolist())
    # High-Q linewidths can be many orders below a uniform sweep increment.
    for mode in modes:
        center=mode['frequency_ghz']; q=mode['q_loaded']
        if q and cfg.spectrum_start_ghz <= center <= cfg.spectrum_stop_ghz:
            halfwidth=center/(2*q)
            for offset in [-20,-8,-3,-1,-.3,0,.3,1,3,8,20]:
                f=center+offset*halfwidth
                if cfg.spectrum_start_ghz <= f <= cfg.spectrum_stop_ghz: frequencies.add(float(f))
    frequencies=sorted(frequencies)
    rows=[]
    for i,frequency in enumerate(frequencies):
        progress('周波数応答を直接計算中 %d/%d'%(i+1,len(frequencies)))
        row=system.driven(frequency,driven)
        # The plot needs scalar responses only; retaining every FEM field
        # would grow memory with the number of sweep frequencies.
        row.pop('vector',None)
        rows.append(row)
    scattering=np.asarray([row['scattering'] for row in rows])
    reflection=scattering[:,driven]
    return {'available':True,'frequencies_ghz':frequencies,'s11_real':reflection.real.tolist(),
            's11_imag':reflection.imag.tolist(),'s11_db':(20*np.log10(np.maximum(np.abs(reflection),1e-15))).tolist(),
            'driven_port':names[driven],'reference_impedances_ohm':{p['name']:p['characteristic_impedance_ohm'] for p in system.port_data},
            's_parameters':{name:{'real':scattering[:,i].real.tolist(),'imag':scattering[:,i].imag.tolist()} for i,name in enumerate(names)},
            'absorbed_power_w':[row['absorbed_power_w'] for row in rows],
            'power_balance_error':[row['power_balance_error'] for row in rows],
            'method':'direct frequency-domain H(curl) Maxwell FEM with normalized coax TEM projection',
            'adaptive_sampling':'11 extra frequencies around each computed eigenmode, using its loaded linewidth',
            'warnings':['TEM-only termination: higher-order coax modes are excluded; extend the uniform feed and verify convergence.',
                        'A resonance with negligible coupling can be absent from S11. Eigenmode search is independent of this plot.',
                        'Losses use configured linearized TLS saturation and perturbative metal/oxide operators; no nonlinear power iteration.']}


def solve_advanced(domain,geometry,cfg,output_dir=None,progress=print,geometry_s=0):
    import ngsolve as ng
    from .surface import surface_integrals
    from .fields import export_field_slices
    start=time.perf_counter()
    progress('選択した解析領域のメッシュ・Maxwell行列を生成中')
    system=MaxwellSystem(domain,geometry,cfg,progress)
    stamp=time.perf_counter()
    pairs,search=search_modes(system,cfg,progress)
    solve_s=time.perf_counter()-stamp
    modes=[];stamp=time.perf_counter()
    progress('壁面損失・ポート損失・participationを積分中（NGSolve最大%dスレッド）'%cfg.threads)
    # Python bookkeeping remains serial; native NGSolve work can share threads.
    with ng.TaskManager():
        for index,(k,vector) in enumerate(pairs,1):
            frequency=k.real*C0/(2*math.pi*L0); omega=2*math.pi*frequency
            field=system.grid_function(vector)
            h_scale=1/(abs(k)*C0*MU0)
            weighted_e=float(np.vdot(vector,system.M@vector).real)
            curl2=float(np.vdot(vector,system.K@vector).real)
            ue=.25*EPS0*weighted_e*L0**3
            um=.25*MU0*curl2*h_scale**2*L0**3; energy=ue+um
            e2cf=(field*ng.Conj(field)).real
            e2=float(ng.Integrate(e2cf,system.mesh,order=2*cfg.order+4))
            material_p={name:float(ng.Integrate(value['epsr']*e2cf,system.mesh,
                        definedon=system.mesh.Materials(name),order=2*cfg.order+4))/weighted_e
                        for name,value in system.materials.items()}
            material_sum=sum(material_p.values())
            material_p={name:value/material_sum for name,value in material_p.items()}
            curl_field=ng.curl(field)
            curl_density=(curl_field*ng.Conj(curl_field)).real
            magnetic_weights={name:float(ng.Integrate(curl_density,system.mesh,
                               definedon=system.mesh.Materials(name),order=2*cfg.order+4))
                              for name in system.materials}
            magnetic_sum=sum(magnetic_weights.values())
            magnetic_p={name:value/magnetic_sum for name,value in magnetic_weights.items()}
            cavity_ue=ue*material_p.get('bulk',0.0)
            cavity_um=um*magnetic_p.get('bulk',0.0)
            cavity_energy=cavity_ue+cavity_um
            faces=surface_integrals(system.mesh,field,order=2*cfg.order+4)
            # All generated antenna metals are ideal PEC. The assigned Rs and oxide
            # model apply only to original cavity metal surfaces.
            lossy_faces=system.oxide_faces if system.port_data else system.wall_faces
            wall_h2=sum(f['curl_t2_mm2'] for f in faces if f['id'] in lossy_faces)*h_scale**2*L0**2
            geometry_factor=omega*energy/(.5*wall_h2) if wall_h2>0 else None
            se=sum(f['e_normal2_mm2'] for f in faces if f['id'] in system.oxide_faces)/e2/L0
            normal=ng.specialcf.normal(3)
            en=ng.BoundaryFromVolumeCF(field)*normal
            weighted_normal=ng.BoundaryFromVolumeCF(system.eps**2)*(en*ng.Conj(en)).real
            p_regions=ng.Integrate(weighted_normal,system.mesh,ng.BND,order=2*cfg.order+4,region_wise=True)
            p_faces={}
            for name,value in zip(system.mesh.GetBoundaries(),p_regions):
                face_id=int(name.split('_')[-1])
                if face_id in system.oxide_faces:
                    p_faces[face_id]=p_faces.get(face_id,0)+max(0,float(value))*cfg.oxide_thickness_nm*1e-6/cfg.oxide_epsr/weighted_e
            poxide=sum(p_faces.values())
            ports=[]
            for port,alpha in zip(system.port_data,system.B.T@vector):
                power=.5*port['wave_admittance_s']*abs(alpha)**2*L0**2
                ports.append({'name':port['name'],'faces':port['faces'],'source_faces':[port['source_face']] if port.get('source_face') else [],
                              'kind':'coax_tem','qc_total_energy':omega*energy/power if power>1e-30 else None,
                              'qc_cavity':omega*cavity_energy/power if power>1e-30 and cavity_energy>0 else None,
                              'power_w_normalized':float(power),'impedance_ohm':port['characteristic_impedance_ohm'],
                              'tem_normalization_integral':port['modal_norm'],'te11_cutoff_ghz':port['te11_cutoff_ghz']})
            for port,_,matrix in system.local:
                power=.5/port.impedance_ohm*float(np.vdot(vector,matrix@vector).real)*L0**2
                metadata=next(item for item in system.external_port_data if item['name']==port.name)
                ports.append({'name':port.name,'faces':port.faces,'source_faces':metadata.get('source_faces',port.faces),
                              'kind':port.kind,'qc_total_energy':omega*energy/power if power>1e-30 else None,
                              'qc_cavity':omega*cavity_energy/power if power>1e-30 and cavity_energy>0 else None,
                              'power_w_normalized':power,'impedance_ohm':port.impedance_ohm,
                              'radiation_epsr':port.radiation_epsr if port.kind=='radiation' else None,
                              'boundary_model':'first_order_local_outgoing_impedance','pml':False})
            for port in ports:
                port['qc']=port['qc_cavity'] if cfg.analysis_mode=='all_openings_qc' else port['qc_total_energy']
            outgoing_power=sum(port['power_w_normalized'] for port in ports)
            q_external_cavity=omega*cavity_energy/outgoing_power if outgoing_power>1e-30 and cavity_energy>0 else None
            q_external_total=omega*energy/outgoing_power if outgoing_power>1e-30 else None
            effective_tand=sum(material_p[name]*value.get('tand',0) for name,value in system.materials.items())
            budget=loss_budget(frequency,geometry_factor or float('inf'),poxide,cfg.model_copy(update={'bulk_tand':effective_tand}))
            inverse_loaded=(1/budget['qi'] if budget['qi'] else 0)+sum(1/p['qc_total_energy'] for p in ports if p['qc_total_energy'])
            external_q=abs(k.real/(2*k.imag)) if abs(k.imag)>1e-14 else None
            cavity_fraction=material_p.get('bulk',0.0)
            mode_class='feed_dominated' if cavity_fraction<.5 else 'strongly_damped' if external_q is not None and external_q<2 else 'cavity_like'
            mode={'mode':index,'mode_class':mode_class,'cavity_electric_energy_fraction':cavity_fraction,
                  'classification_rule':'feed_dominated: bulk electric-energy fraction <0.5; otherwise strongly_damped: external Q<2; otherwise cavity_like. This is a diagnostic, not an exact mode identity.','frequency_ghz':frequency/1e9,'frequency_imag_ghz':k.imag*C0/(2*math.pi*L0*1e9),
                  **budget,'q_loaded':1/inverse_loaded if inverse_loaded>0 else None,
                  'q_ports_eigenvalue':external_q,
                  'q_external_cavity':q_external_cavity,'q_external_total_energy':q_external_total,
                  'inverse_q_external_cavity':sum(1/p['qc_cavity'] for p in ports if p['qc_cavity']),
                  'inverse_q_external_total_energy':sum(1/p['qc_total_energy'] for p in ports if p['qc_total_energy']),
                  'q_loaded_cavity':cavity_energy/energy/inverse_loaded if inverse_loaded>0 else None,
                  'q_loaded_energy_scope':'total modeled domain including feed',
                  'qc_energy_scope':'cavity (bulk material) excluding feed' if cfg.analysis_mode=='all_openings_qc' else 'total modeled domain including feed',
                  'cavity_energy_j_normalized':cavity_energy,'total_energy_j_normalized':energy,
                  'cavity_electric_energy_j_normalized':cavity_ue,'cavity_magnetic_energy_j_normalized':cavity_um,
                  'outgoing_power_w_normalized':outgoing_power,
                  'energy_normalization':'Arbitrary peak-phasor eigenmode amplitude with x^H M x=1 in native mm coordinates; energies and powers share this scale, and their Q ratios are amplitude-independent.',
                  'material_magnetic_participation':magnetic_p,
                  'geometry_factor_ohm':geometry_factor,'surface_electric_per_m':se,
                  'participation':{**material_p,'oxide':poxide},'material_participation':material_p,
                  'surface_participation':[{'face':f['id'],'p_oxide':p_faces.get(f['id'],0),
                                            'is_port':f['id'] in system.port_faces,'is_internal':f['id'] not in system.wall_faces|system.port_faces} for f in faces],
                  'ports':ports,'residual':system.residual(k,vector),'energy_balance_relative':abs(ue-um)/energy,
                  'electric_energy_j_normalized':ue,'magnetic_energy_j_normalized':um}
            if output_dir:
                out=Path(output_dir);out.mkdir(parents=True,exist_ok=True)
                ng.VTKOutput(ma=system.mesh,coefs=[field.real,field.imag,ng.Norm(field),ng.Norm(ng.curl(field))*h_scale],
                             names=['E_real','E_imag','E_magnitude','H_magnitude'],filename=str(out/('mode_%02d'%index)),subdivision=0).Do()
                mode['field_file']=export_field_slices(system.mesh,field,geometry,out,index,cfg.field_resolution)
            modes.append(mode)
    post_s=time.perf_counter()-stamp
    spectrum=None;stamp=time.perf_counter()
    if cfg.spectrum_enabled and cfg.analysis_mode!='all_openings_qc':spectrum=spectrum_samples(system,cfg,modes,progress)
    spectrum_s=time.perf_counter()-stamp
    warnings=list(geometry.get('warnings',[]))+[
        '周波数・Qc・G・表面participationには別々のメッシュ収束確認が必要です。有限要素残差は離散化誤差を表しません。',
        'lowestは指定帯域と探索点で見つかった最低モードです。モード完全性の証明ではなく、S11に見えない暗いモードもあり得ます。',
        '同軸TEMポートは均一な環状終端の解析モード投影です。高次同軸モードは含まず、給電長と端面位置への収束が必要です。',
        'アンテナ金属はPECです。指定Rs・酸化膜は元の空洞壁にだけ適用します。実ピンの金属損失は別途必要です。']
    if system.local:
        warnings.append('開口のradiation/表面インピーダンスは局所一次の外向き波近似です。自由空間では約376.73Ωで、同軸の回路50Ωとは異なります。斜入射・エバネッセント波・高次モードは反射し得ます。PMLは未実装です。')
    if cfg.analysis_mode=='all_openings_qc':
        warnings.append('全開口QcモードではS11計算を無効化しています。Qcは空洞bulk領域の電気・磁気エネルギーを用い、給電部を除きます。複素固有周波数のQとの比較にはqc_total_energyを使用します。')
    if system.local or cfg.analysis_mode=='all_openings_qc':
        warnings.append('開いているのは指定した外部境界と選択した人工キャップ候補だけです。STEPから実機の全接続を自動同定したことにはなりません。未指定面はPECです。')
    highest=max([m['frequency_ghz'] for m in modes]+([cfg.spectrum_stop_ghz] if cfg.spectrum_enabled else []))
    for port in system.port_data:
        if highest>=port['te11_cutoff_ghz']:
            warnings.append('%s: 解析帯域が同軸TE11遮断周波数 %.3f GHz以上です。TEMのみの終端は不十分で、高次ポートモードが必要です。'%(port['name'],port['te11_cutoff_ghz']))
    if any(m['mode_class']!='cavity_like' for m in modes):
        warnings.append('一覧には給電部に集中した極・強く減衰する極も含まれます。最低モードの探索範囲は空洞と同軸全体であり、空洞内の鋭い共振だけではありません。分類は電気エネルギー分布とQの目安です。')
    if cfg.loss_model=='tls_qres':warnings.append('QresとTLS係数は入力した実測由来パラメータです。材質からの第一原理予測ではありません。')
    result={'solver':'NGSolve','solver_version':ng.__version__,'schema_version':2,'config':cfg.model_dump(),
            'geometry':geometry,'modes':modes,'mode_search':search,'memory_policy':system.memory_policy,
            'analysis_mode':cfg.analysis_mode,
            'spectrum_policy':'disabled by all_openings_qc; Qc uses stored energy and outgoing power only' if cfg.analysis_mode=='all_openings_qc' else 'optional driven S11',
            'external_boundary_assignments':system.external_port_data+system.port_data,'mesh':{'elements':system.mesh.ne,'dofs':system.n,
            'total_dofs':system.fes.ndof,'maxh_mm':cfg.maxh_mm,'order':cfg.order,'geometry_order':cfg.order+1},
            'timings':{'geometry_s':geometry_s,'mesh_s':system.mesh_s,'assembly_s':system.assembly_s,'solve_s':solve_s,
                       'postprocess_s':post_s,'spectrum_s':spectrum_s,'total_s':time.perf_counter()-start+geometry_s},
            'machine':{"platform": platform.platform(), "processor": platform.machine(), "threads": cfg.threads,
                          "postprocess_parallelism": {
                              "task_manager_enabled": True, "requested_threads": cfg.threads,
                              "scope": "native_ngsolve_postprocessing", "python_loop_parallel": False,
                              "field_sampling": "native_batch_with_scalar_fallback",
                              "note": "TaskManager enables eligible native integration and field evaluation. Python bookkeeping, scalar fallback and VTK/JSON file output remain serial; requested threads do not guarantee speedup."}},'warnings':warnings}
    if spectrum is not None:result['spectrum']=spectrum
    if output_dir:Path(output_dir,'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    return result
