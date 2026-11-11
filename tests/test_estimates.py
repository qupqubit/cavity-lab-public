"""Runtime estimates account for the actual workload and never invent completion."""
import json
import pytest

from cavity_fem.estimates import make_plan, estimate, ProgressTracker, write_json
from cavity_fem.config import SolveConfig, Antenna, AntennaSweep


@pytest.fixture
def isolated_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from cavity_fem import api
    monkeypatch.setattr(api, 'DATA', tmp_path)
    with TestClient(api.app, base_url="http://127.0.0.1:8768") as client:
        yield client, tmp_path


def test_preflight_includes_wide_shifts_frequency_points_and_sweep():
    base = SolveConfig(antennas=[Antenna(name='feed', face=1, length_mm=5)], maxh_mm=3, n_modes=1)
    p = make_plan(base)
    wide = make_plan(base.model_copy(update={'mode_search':'lowest'}))
    s11 = make_plan(base.model_copy(update={'spectrum_enabled':True}))
    sweep = make_plan(base.model_copy(update={'antenna_sweep':AntennaSweep(antenna='feed', lengths_mm=[0,5,8])}))
    assert wide['total_s'] > 4*p['total_s']
    assert s11['total_s'] > p['total_s']
    assert len(sweep['blocks']) == 3
    assert sweep['total_s'] == pytest.approx(3*p['total_s'])
    assert estimate(p, now=100, queue_wait=30)['estimated_finish_at'] == pytest.approx(130+p['total_s'])


def test_deadline_overrun_expands_range_never_claims_completion():
    p=make_plan(SolveConfig())
    e=estimate(p, {'status':'running','started_at':100}, now=100+p['total_s']*5)
    assert e['overdue'] and e['remaining_s'] > 0
    assert e['upper_s'] > e['lower_s'] > 0
    done=estimate(p, {'status':'completed','finished_at':400}, now=500)
    assert done['remaining_s'] == 0 and done['estimated_finish_at'] == 400


def test_progress_tracks_completed_samples_and_separate_sweep_blocks(tmp_path, monkeypatch):
    cfg=SolveConfig(antennas=[Antenna(name='a',face=1,length_mm=5)],spectrum_enabled=True,
                    antenna_sweep=AntennaSweep(antenna='a',lengths_mm=[0,5]))
    clock=[100.0];monkeypatch.setattr('cavity_fem.estimates.time.time',lambda:clock[0])
    tracker=ProgressTracker(tmp_path,cfg,lambda _:None);tracker.start_block()
    tracker('アンテナと誘電体を含むメッシュ・Maxwell行列を生成中')
    clock[0]+=2;tracker('固有モード探索 8 GHz')
    clock[0]+=30;tracker('壁面損失・ポート損失・participationを積分中')
    clock[0]+=1;tracker('周波数応答を直接計算中 1/20')
    clock[0]+=10;tracker('周波数応答を直接計算中 11/20')
    progress=json.loads((tmp_path/'progress.json').read_text())
    assert progress['completed_units'] == 10 and progress['total_units'] == 20
    e=estimate(tracker.plan,{'status':'running','started_at':100},progress,now=clock[0])
    assert e['remaining_s'] > 10  # includes the still-unstarted sweep case
    tracker.finish_block();tracker.start_block()
    p=json.loads((tmp_path/'progress.json').read_text())
    assert p['block_index']==1 and p['phase']=='geometry'


def test_only_same_step_history_calibrates(tmp_path):
    source=tmp_path/'source.step';source.write_text('geometry A')
    cfg=SolveConfig(maxh_mm=3,n_modes=1).model_dump()
    baseline=make_plan(cfg)['total_s']
    for index,content in enumerate(['geometry A','geometry A','geometry B']):
        gid='g%d'%index
        geometry=tmp_path/'geometries'/gid/'input.step';geometry.parent.mkdir(parents=True);geometry.write_text(content)
        old=dict(cfg,geometry_id=gid)
        write_json(tmp_path/'jobs'/str(index)/'result.json',{'config':old,'timings':{'solve_s':100 if index<2 else 100000,'geometry_s':1,'mesh_s':1,'assembly_s':1,'postprocess_s':1}})
    result=make_plan(cfg,tmp_path,source)
    assert result['confidence']=='medium'
    assert baseline < result['total_s'] < 200


