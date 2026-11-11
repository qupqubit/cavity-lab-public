"""Independent TEM transmission-line and low-mode references for driven FEM."""
import json
import math

import numpy as np
import pytest
from netgen.occ import Box, Cylinder, Pnt, Vec

from cavity_fem.config import SolveConfig
from cavity_fem.geometry import face_info, shape_info
from cavity_fem.solver import C0, solve
from cavity_fem.spectrum import MaxwellSystem, coax_impedance


def coax_line(length=5, epsr=2.1):
    shape=Cylinder(Pnt(0,0,0),Vec(0,0,1),3,length)-Cylinder(Pnt(0,0,0),Vec(0,0,1),1,length)
    shape.mat('bulk')
    for index,face in enumerate(shape.faces,1):face.name='face_%d'%index
    faces=face_info(shape);ports=[]
    for z,axis in [(0,[0,0,-1]),(length,[0,0,1])]:
        ids=[face['id'] for face in faces if abs(face['bbox_mm'][0][2]-z)<1e-5 and abs(face['bbox_mm'][1][2]-z)<1e-5]
        ports.append({'kind':'coax_tem','name':'left' if z==0 else 'right','faces':ids,
                      'center_mm':[0,0,z],'axis':axis,'inner_radius_mm':1,'outer_radius_mm':3,'dielectric_epsr':epsr})
    pf={face for port in ports for face in port['faces']}
    geometry={**shape_info(shape),'faces':faces,'antenna_ports':ports,
              'wall_faces':[face['id'] for face in faces if face['id'] not in pf],
              'materials':{'bulk':{'epsr':epsr,'tand':0}}}
    return shape,geometry


@pytest.fixture(scope='module')
def line():
    shape,geometry=coax_line()
    cfg=SolveConfig(maxh_mm=1,order=2,threads=1,loss_model='none')
    return MaxwellSystem(shape,geometry,cfg)


@pytest.mark.parametrize('frequency',[1,5,10])
def test_coax_tem_matched_transmission_phase_and_power(line,frequency):
    result=line.driven(frequency)
    s11,s21=result['scattering']
    expected=np.exp(-1j*2*math.pi*frequency*1e9/C0*math.sqrt(2.1)*.005)
    assert abs(s11)<.001
    assert s21==pytest.approx(expected,abs=.001)
    assert result['power_balance_error']<1e-8
    assert line.port_data[0]['modal_norm']==pytest.approx(1,abs=.001)
    assert line.port_data[0]['characteristic_impedance_ohm']==pytest.approx(coax_impedance(1,3,2.1))


def test_driven_dielectric_absorption_is_positive_and_matches_power_balance(line):
    before=line.cfg
    try:
        line.cfg=line.cfg.model_copy(update={'loss_model':'tls_qres','q_res':100,'oxide_tand':0})
        result=line.driven(5)
        # Independent TEM attenuation for a weakly lossy homogeneous line.
        expected=1-math.exp(-2*math.pi*5e9/C0*math.sqrt(2.1)*.005/100)
        assert result['absorbed_power_w']==pytest.approx(expected,rel=.01)
        assert 0<result['output_power_w']<1
        assert result['power_balance_error']<1e-8
    finally:
        line.cfg=before


def test_physical_feed_length_changes_the_computed_transmission_phase(line):
    shape,geometry=coax_line(length=8)
    longer=MaxwellSystem(shape,geometry,line.cfg)
    short_s=line.driven(5)['scattering'][1]
    long_s=longer.driven(5)['scattering'][1]
    phase_difference=np.angle(long_s/short_s)
    expected=-2*math.pi*5e9/C0*math.sqrt(2.1)*.003
    assert phase_difference==pytest.approx(expected,abs=.001)
    assert abs(longer.driven(5)['scattering'][0])<.001


def test_wide_lowest_search_finds_analytic_box_modes_and_exports_fields(tmp_path):
    result=solve(None,SolveConfig(maxh_mm=4,order=2,n_modes=3,mode_search='lowest',threads=1,
                                 spectrum_start_ghz=.1,spectrum_stop_ghz=30,loss_model='none',field_resolution=11),
                 domain_override=Box(Pnt(0,0,0),Pnt(10,8,12)),output_dir=tmp_path,progress=lambda _:None)
    sizes=(.01,.008,.012)
    exact=sorted(C0/2*math.sqrt(sum((i/l)**2 for i,l in zip(indices,sizes)))/1e9
                 for indices in [(1,0,1),(0,1,1),(1,1,0)])
    for mode,frequency in zip(result['modes'],exact):
        assert mode['frequency_ghz']==pytest.approx(frequency,rel=.012)
        assert mode['residual']<1e-8
        fields=json.loads((tmp_path/mode['field_file']).read_text())
        assert len(fields['planes'])==3
        assert all(len(plane['magnitude'])==121 for plane in fields['planes'])
        valid=[value for plane in fields['planes'] for value in plane['magnitude'] if value is not None]
        assert max(valid)==pytest.approx(1)
        assert all(value>=0 for value in valid)
        assert (tmp_path/('mode_%02d.vtu'%mode['mode'])).exists()
    assert result['mode_search']['claim']=='lowest found in searched band'
    assert result['mode_search']['completeness_proven'] is False


def test_tem_overlap_is_orthogonal_to_scalar_gradients_on_terminal(line):
    # This exact TEM identity is required before deflating gradients whose
    # scalar potential is free on the port. Low-order quadrature breaks it.
    from scipy import sparse
    gradient,scalar=line.fes.CreateGradient()
    rows,cols,values=gradient.COO()
    matrix=sparse.csr_matrix((np.asarray(values),(np.asarray(rows),np.asarray(cols))),shape=(gradient.height,gradient.width))
    scalar_free=np.asarray(list(scalar.FreeDofs() & ~scalar.GetDofs(line.mesh.Boundaries(line.wall_names))),dtype=bool)
    full=np.zeros((line.fes.ndof,line.B.shape[1]));full[line.free]=line.B
    overlap=(matrix.T@full)[scalar_free]
    assert np.max(np.abs(overlap))<1e-11


def test_frequency_sweep_releases_fields_between_points():
    import weakref
    from cavity_fem.spectrum import spectrum_samples

    class System:
        port_data=[{'name':'feed','characteristic_impedance_ohm':50}]
        previous=None

        def driven(self,frequency,port):
            assert self.previous is None or self.previous() is None
            vector=np.ones(16,dtype=complex)
            self.previous=weakref.ref(vector)
            return {'vector':vector,'scattering':np.array([.5+0j]),
                    'absorbed_power_w':.75,'power_balance_error':0}

    system=System()
    result=spectrum_samples(system,SolveConfig(spectrum_points=5),[],lambda _:None)
    assert system.previous() is None
    assert result['s11_real']==[.5]*5
    assert result['absorbed_power_w']==[.75]*5


def test_inserted_pin_changes_resonance_and_external_coupling():
    from cavity_fem.config import Antenna
    records=[]
    for length in (0,2):
        shape=Cylinder(Pnt(0,0,0),Vec(0,0,1),8,5)
        face=next(index+1 for index,face in enumerate(shape.faces) if abs(face.center[2]-5)<1e-5)
        config=SolveConfig(maxh_mm=4,order=1,n_modes=1,target_ghz=14,threads=1,loss_model='none',
             antennas=[Antenna(name='feed',face=face,length_mm=length,pin_radius_mm=.5,
                               coax_outer_radius_mm=1.5,feed_length_mm=2)])
        result=solve(None,config,domain_override=shape,progress=lambda _:None)
        mode=result['modes'][0]
        assert 12<mode['frequency_ghz']<16
        assert mode['residual']<1e-8
        assert mode['ports'][0]['qc']==pytest.approx(mode['q_ports_eigenvalue'],rel=1e-6)
        records.append(mode)
    assert records[1]['frequency_ghz']<records[0]['frequency_ghz']
    assert records[1]['ports'][0]['qc']<records[0]['ports'][0]['qc']/10
