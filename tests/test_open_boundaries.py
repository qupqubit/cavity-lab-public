"""External opening Q from eigenmode energy and outward power; no driven solve."""
import math
from pathlib import Path

import pytest
from netgen.occ import Box, Pnt

from cavity_fem.antennas import build_antennas
from cavity_fem.boundaries import assign_external_boundaries
from cavity_fem.config import Antenna, Port, SolveConfig
from cavity_fem.geometry import face_info, load_domain, shape_info
from cavity_fem.solver import solve



def test_radiation_wave_impedance_and_no_s11_mode():
    port=Port(name='air',faces=[5],kind='radiation',radiation_epsr=4)
    assert port.impedance_ohm==pytest.approx(376.730313668/2)
    config=SolveConfig(analysis_mode='all_openings_qc',spectrum_enabled=True,
                      antennas=[Antenna(name='coax',face=1)],ports=[port])
    assert config.spectrum_enabled is False
    with pytest.raises(ValueError):
        SolveConfig(analysis_mode='unknown')
    with pytest.raises(ValueError):
        Port(name='bad',faces=[1],kind='pml')
    with pytest.raises(ValueError):
        SolveConfig(antennas=[Antenna(name='coax',face=1)],ports=[Port(name='open',faces=[1])])






def test_internal_or_missing_openings_are_rejected():
    shape=Box(Pnt(0,0,0),Pnt(10,8,12))
    info={**shape_info(shape),'faces':face_info(shape)}
    cfg=SolveConfig(analysis_mode='all_openings_qc')
    with pytest.raises(ValueError,match='1つ以上'):
        assign_external_boundaries(info,cfg)
    info['faces'][0]['internal']=True
    cfg=cfg.model_copy(update={'ports':[Port(name='internal',faces=[1])]})
    with pytest.raises(ValueError,match='内部界面'):
        assign_external_boundaries(info,cfg)


def test_two_openings_energy_power_conservation_without_s11(monkeypatch):
    import cavity_fem.spectrum as spectrum
    def forbidden(*args,**kwargs):
        raise AssertionError('all_openings_qc must never call driven S11')
    monkeypatch.setattr(spectrum,'spectrum_samples',forbidden)
    shape=Box(Pnt(0,0,0),Pnt(10,8,12))
    bottom=next(i+1 for i,f in enumerate(shape.faces) if abs(f.center[2])<1e-6)
    top=next(i+1 for i,f in enumerate(shape.faces) if abs(f.center[2]-12)<1e-6)
    cfg=SolveConfig(analysis_mode='all_openings_qc',spectrum_enabled=True,maxh_mm=4,order=2,
                    n_modes=1,target_ghz=15,threads=1,loss_model='none',
                    ports=[Port(name='lower',faces=[bottom],impedance_ohm=1e6),
                           Port(name='upper',faces=[top],impedance_ohm=2e6)])
    result=solve(None,cfg,domain_override=shape,progress=lambda _:None)
    mode=result['modes'][0]
    omega=2*math.pi*mode['frequency_ghz']*1e9
    assert 'spectrum' not in result
    assert result['config']['spectrum_enabled'] is False
    assert mode['cavity_energy_j_normalized']==pytest.approx(mode['total_energy_j_normalized'],rel=1e-12)
    assert all(port['power_w_normalized']>0 for port in mode['ports'])
    assert mode['outgoing_power_w_normalized']==pytest.approx(sum(p['power_w_normalized'] for p in mode['ports']))
    for port in mode['ports']:
        assert port['qc']==pytest.approx(omega*mode['cavity_energy_j_normalized']/port['power_w_normalized'])
        assert port['qc']==pytest.approx(port['qc_cavity'])
        assert port['qc_cavity']==pytest.approx(port['qc_total_energy'])
    assert mode['q_external_cavity']==pytest.approx(1/sum(1/p['qc_cavity'] for p in mode['ports']))
    assert mode['q_external_total_energy']==pytest.approx(mode['q_ports_eigenvalue'],rel=1e-6)
    assert mode['residual']<1e-8


