"""Honest runtime ranges, calibrated from local completed jobs, and live progress.

No progress percentage is inferred from wall time. Native factorization can
stall; a passed deadline expands the estimate instead of claiming completion.
"""
import hashlib
import json
import math
import re
import time
from pathlib import Path


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
    temp.replace(path)


def shifts(config):
    if config.get('mode_search') != 'lowest':
        return 1
    if not (config.get('ports') or config.get('antennas') or config.get('all_artificial_caps')):
        return 1
    lo, hi = config.get('spectrum_start_ghz', .1), config.get('spectrum_stop_ghz', 18)
    return len(set([lo + (hi-lo)*i/6 for i in range(7)] + [config.get('target_ghz', 8)]))


def spectrum_count(config):
    if not config.get('spectrum_enabled') or not config.get('antennas') or config.get('analysis_mode') == 'all_openings_qc':
        return 0
    return config.get('spectrum_points', 41) + 11*config.get('n_modes', 3)


def raw_costs(config):
    h = max(.1, config.get('maxh_mm', 2))
    order = config.get('order', 2)
    antennas = len(config.get('antennas', []))
    ports = len(config.get('ports', [])) + bool(config.get('all_artificial_caps'))
    factor = (3/h)**3.4
    basis = [0, 1.8, 45, 180][order] if antennas else [0, .5, 3, 15][order]
    if ports and not antennas:
        basis *= 6
    mode_factor = max(1, (2*config.get('n_modes', 3)+4)/10)**.7
    return {'geometry': .5, 'mesh': max(.2, factor*.8*order),
            'eigen': max(.5, basis*factor*mode_factor*(1+.25*max(0, antennas-1))),
            'post': max(.3, .15*config.get('n_modes', 3)*(config.get('field_resolution', 41)/21)**2),
            'frequency': max(.1, (2.8 if order == 2 else .2 if order == 1 else 12)*factor)}


def fingerprint(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


_history_cache = {}


def histories(data, source):
    if data is None or source is None:
        return []
    key = (str(data), str(source))
    now = time.monotonic()
    if key in _history_cache and now-_history_cache[key][0] < 5:
        return _history_cache[key][1]
    signature = fingerprint(source)
    if signature is None:
        return []
    rows = []
    for path in sorted((Path(data)/'jobs').glob('*/result.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:120]:
        result = read_json(path, {})
        config = result.get('config', {})
        gid = config.get('geometry_id')
        if not gid or fingerprint(Path(data)/'geometries'/gid/'input.step') != signature:
            continue
        if result.get('timings', {}).get('solve_s') is not None:
            rows.append(result)
    _history_cache[key] = (now, rows)
    return rows


def distance(a, b):
    result = abs(math.log(a.get('maxh_mm', 2)/b.get('maxh_mm', 2)))*2
    result += 3*abs(a.get('order', 2)-b.get('order', 2))
    result += 4*abs(len(a.get('antennas', []))-len(b.get('antennas', [])))
    result += 2*abs(len(a.get('ports', []))-len(b.get('ports', [])))
    result += abs(a.get('n_modes', 3)-b.get('n_modes', 3))*.15
    if a.get('antennas') and b.get('antennas'):
        result += abs(a['antennas'][0]['length_mm']-b['antennas'][0]['length_mm'])*.1
    return result


def calibrated_costs(config, history):
    costs = raw_costs(config)
    closest = sorted(history, key=lambda r: distance(config, r['config']))[:4]
    closest = [r for r in closest if distance(config, r['config']) < 5]
    for phase in costs:
        samples = []
        for result in closest:
            old = result['config']; timing = result['timings']
            observed = {'geometry': timing.get('geometry_s'),
                        'mesh': (timing.get('mesh_s') or 0)+(timing.get('assembly_s') or 0),
                        'eigen': (timing.get('solve_s') or 0)/shifts(old),
                        'post': timing.get('postprocess_s'),
                        'frequency': (timing.get('spectrum_s') or 0)/max(1, len((result.get('spectrum') or {}).get('frequencies_ghz', [])))}[phase]
            if observed is not None and observed > .001:
                weight = 1/(.3+distance(config, old))**2
                samples.append((observed*costs[phase]/raw_costs(old)[phase], weight))
        if samples:
            costs[phase] = sum(value*weight for value, weight in samples)/sum(weight for _, weight in samples)
    return costs, len(closest)


def make_plan(config, data=None, source=None):
    config = config.model_dump() if hasattr(config, 'model_dump') else dict(config)
    history = histories(data, source)
    blocks = []
    configs = [('基本設定', config)]
    sweep = config.get('antenna_sweep')
    if sweep:
        base = next(a['length_mm'] for a in config['antennas'] if a['name'] == sweep['antenna'])
        for length in sorted(sweep['lengths_mm']):
            if length == base:
                continue
            sub = json.loads(json.dumps(config))
            sub.update(antenna_sweep=None, spectrum_enabled=False, mode_search='target')
            for antenna in sub['antennas']:
                if antenna['name'] == sweep['antenna']:
                    antenna['length_mm'] = length
            configs.append(('挿入長 %g mm'%length, sub))
    count = 0
    for label, cfg in configs:
        costs, examples = calibrated_costs(cfg, history); count = max(count, examples)
        phases = [{'key': 'geometry', 'label': '形状処理', 'seconds': costs['geometry']},
                  {'key': 'mesh', 'label': 'メッシュ・行列', 'seconds': costs['mesh']}]
        phases += [{'key': 'eigen_%d'%i, 'label': '固有モード探索 %d/%d'%(i+1, shifts(cfg)), 'seconds': costs['eigen']} for i in range(shifts(cfg))]
        phases.append({'key': 'post', 'label': 'Qi・Qc・電場の積分と出力', 'seconds': costs['post']})
        if spectrum_count(cfg):
            phases.append({'key': 'spectrum', 'label': 'S11 周波数応答', 'seconds': costs['frequency']*spectrum_count(cfg), 'units': spectrum_count(cfg)})
        blocks.append({'label': label, 'phases': phases})
    total = sum(p['seconds'] for b in blocks for p in b['phases'])
    return {'total_s': total, 'blocks': blocks, 'confidence': 'medium' if count >= 2 else 'low',
            'basis': ('同じSTEPの実行履歴%d件と現在の設定から推定'%count) if count else '初期の概算。形状・メッシュ・計算機によって大きく変わります',
            'note': '終了時刻は目安です。探索の難しさや同時実行負荷に応じて更新します。'}


def estimate(plan, state=None, progress=None, now=None, queue_wait=0):
    now = time.time() if now is None else now
    state = state or {}; progress = progress or {}
    status = state.get('status', 'preview')
    total = plan['total_s']
    remaining = total
    stage = '実行前の見積もり'
    overdue = False
    if status in ('completed', 'failed', 'cancelled'):
        remaining = 0; stage = state.get('stage', status)
    elif status == 'running':
        elapsed = max(0, now-state.get('started_at', now))
        if progress:
            planned_done = progress.get('completed_planned_s', 0)
            pace = min(6, max(.25, progress.get('completed_actual_s', 0)/planned_done)) if planned_done > 1 else 1
            stage_elapsed = max(0, now-progress.get('stage_started_at', now))
            stage_total = progress.get('stage_planned_s', total)
            done, units = progress.get('completed_units') or 0, progress.get('total_units') or 0
            if done > 0 and units > done:
                stage_left = stage_elapsed/done*(units-done)
            else:
                target = stage_total*pace
                overdue = stage_elapsed > target
                stage_left = max(2, target-stage_elapsed) if not overdue else max(5, stage_elapsed*.35)
            after = max(0, total-planned_done-stage_total)
            remaining = max(1, stage_left+after*pace)
            stage = progress.get('label', state.get('stage', '計算中'))
        else:
            overdue = elapsed > total
            remaining = max(5, total-elapsed) if not overdue else max(10, elapsed*.35)
            stage = state.get('stage', '計算を開始しています')
    elif status == 'queued':
        stage = '先行する計算を待機中'
    remaining += queue_wait if remaining else 0
    factor = .5 if plan['confidence'] == 'medium' else .3
    lower, upper = remaining*factor, remaining*(2 if factor == .5 else 3)
    return {'total_s': total, 'remaining_s': remaining, 'lower_s': lower, 'upper_s': upper,
            'estimated_finish_at': now+remaining if remaining else state.get('finished_at', now),
            'confidence': plan['confidence'], 'basis': plan['basis'], 'note': plan['note'],
            'queued_wait_s': queue_wait, 'stage': stage, 'overdue': overdue,
            'completed_units': progress.get('completed_units'), 'total_units': progress.get('total_units'),
            'breakdown': [{'label': b['label'], 'seconds': sum(p['seconds'] for p in b['phases'])} for b in plan['blocks']]}


class ProgressTracker:
    def __init__(self, output, config, callback=print):
        self.output = Path(output); self.callback = callback
        self.plan = read_json(self.output/'estimate.json') or make_plan(config)
        self.block = -1; self.phase = -1
        self.completed_planned = 0; self.completed_actual = 0
        self.stage_started = time.time(); self.started = self.stage_started

    def start_block(self):
        self.block += 1; self.phase = -1
        self.enter(0)

    def enter(self, index):
        phases = self.plan['blocks'][self.block]['phases']
        now = time.time()
        if self.phase >= 0:
            self.completed_planned += sum(p['seconds'] for p in phases[self.phase:index])
            self.completed_actual += now-self.stage_started
        self.phase = min(index, len(phases)-1); self.stage_started = now
        self.emit()

    def finish_block(self):
        phases = self.plan['blocks'][self.block]['phases']
        self.completed_planned += sum(p['seconds'] for p in phases[self.phase:])
        self.completed_actual += time.time()-self.stage_started
        self.phase = -1

    def emit(self, done=None, units=None):
        block = self.plan['blocks'][self.block]; phase = block['phases'][self.phase]
        write_json(self.output/'progress.json', {'updated_at': time.time(), 'stage_started_at': self.stage_started,
            'stage_planned_s': phase['seconds'], 'completed_planned_s': self.completed_planned,
            'completed_actual_s': self.completed_actual, 'completed_units': done, 'total_units': units,
            'label': block['label']+' · '+phase['label'], 'block_index': self.block, 'phase': phase['key']})

    def __call__(self, message):
        self.callback(message)
        if self.block < 0 or self.phase < 0:
            return
        phases = self.plan['blocks'][self.block]['phases']
        key = None
        if 'メッシュ' in message or '行列を組み立て' in message: key = 'mesh'
        elif message.startswith('固有モード'):
            eigen = [i for i,p in enumerate(phases) if p['key'].startswith('eigen_')]
            next_index = next((i for i in eigen if i > self.phase), eigen[-1])
            if next_index != self.phase: self.enter(next_index)
            return
        elif 'participationを積分' in message: key = 'post'
        elif '周波数応答を直接計算中' in message: key = 'spectrum'
        if key:
            index = next((i for i,p in enumerate(phases) if p['key'] == key), None)
            if index is not None and index > self.phase: self.enter(index)
            if key == 'spectrum':
                match = re.search(r'(\d+)/(\d+)', message)
                if match: self.emit(int(match[1])-1, int(match[2]))
