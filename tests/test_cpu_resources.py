"""CPU recommendations are synthetic, portable, and never change saved values."""
import json
from types import SimpleNamespace

import pytest

from cavity_fem import resources
from cavity_fem.config import SolveConfig


@pytest.fixture
def cpu_os(monkeypatch):
    fake = SimpleNamespace(cpu_count=lambda: 8, getpid=lambda: 123)
    monkeypatch.setattr(resources, 'os', fake)
    monkeypatch.setattr(resources, 'psutil', None)
    return fake


@pytest.mark.parametrize('cpus,threads', [(1, 1), (2, 1), (4, 3), (8, 6), (10, 8),
                                         (12, 9), (16, 12), (24, 19), (64, 51),
                                         (128, 102), (2048, 1024)])
def test_floor_eighty_percent_with_minimum_and_supported_maximum(cpu_os, cpus, threads):
    cpu_os.cpu_count = lambda: cpus
    policy = resources.cpu_policy()
    assert policy['detected'] is True
    assert policy['logical_cpus'] == policy['available_cpus'] == cpus
    assert policy['recommended_threads'] == threads
    assert policy['target_fraction'] == .8 and policy['max_threads'] == 1024
    assert policy['physical_cores'] is None
    assert resources.recommended_threads() == threads
    json.dumps(policy, allow_nan=False)


def test_process_cpu_count_and_posix_affinity_both_tighten_system_count(cpu_os):
    cpu_os.cpu_count = lambda: 32
    cpu_os.process_cpu_count = lambda: 12
    calls = []
    def affinity(pid):
        calls.append(pid)
        return {0, 2, 4, 6, 8, 10}
    cpu_os.sched_getaffinity = affinity
    policy = resources.cpu_policy()
    assert calls == [0]
    assert policy['logical_cpus'] == 32 and policy['available_cpus'] == 6
    assert policy['recommended_threads'] == 4
    assert policy['observations'] == {'os.cpu_count': 32, 'os.process_cpu_count': 12,
                                      'os.sched_getaffinity': 6}


def test_windows_style_psutil_affinity_and_physical_count(cpu_os, monkeypatch):
    cpu_os.cpu_count = lambda: 32
    queried = []
    def process(pid):
        queried.append(pid)
        # No setter parameter is accepted: policy must only read affinity.
        return SimpleNamespace(cpu_affinity=lambda: [0, 2, 4, 6])
    monkeypatch.setattr(resources, 'psutil', SimpleNamespace(
        cpu_count=lambda logical=True: 32 if logical else 16, Process=process))
    policy = resources.cpu_policy()
    assert queried == [123]
    assert policy['logical_cpus'] == 32 and policy['physical_cores'] == 16
    assert policy['available_cpus'] == 4 and policy['recommended_threads'] == 3


def test_psutil_logical_fallback_does_not_use_physical_cores_as_a_thread_cap(cpu_os, monkeypatch):
    cpu_os.cpu_count = lambda: None
    monkeypatch.setattr(resources, 'psutil', SimpleNamespace(
        cpu_count=lambda logical=True: 16 if logical else 8))
    policy = resources.cpu_policy()
    assert policy['logical_cpus'] == policy['available_cpus'] == 16
    assert policy['physical_cores'] == 8 and policy['recommended_threads'] == 12
    assert policy['observations'] == {'psutil.cpu_count': 16}


def test_affinity_can_establish_available_count_when_machine_count_is_unknown(cpu_os):
    cpu_os.cpu_count = lambda: None
    cpu_os.sched_getaffinity = lambda pid: {1, 3, 5, 7, 9}
    policy = resources.cpu_policy()
    assert policy['detected'] is True and policy['logical_cpus'] is None
    assert policy['available_cpus'] == 5 and policy['recommended_threads'] == 4


def test_affinity_ids_are_counted_once_and_all_available_limits_are_respected(cpu_os, monkeypatch):
    cpu_os.process_cpu_count = lambda: 4
    cpu_os.sched_getaffinity = lambda pid: {0, 1, 2, 3, 4, 5}
    monkeypatch.setattr(resources, 'psutil', SimpleNamespace(
        cpu_count=lambda logical=True: None,
        Process=lambda pid: SimpleNamespace(cpu_affinity=lambda: [0, 2, 2, 4])))
    policy = resources.cpu_policy()
    assert policy['available_cpus'] == 3 and policy['recommended_threads'] == 2


@pytest.mark.parametrize('invalid', [None, 0, -1, True, 1.5, '8'])
def test_unknown_or_invalid_counts_fall_back_to_one_without_fabricated_detection(cpu_os, invalid):
    cpu_os.cpu_count = lambda: invalid
    policy = resources.cpu_policy()
    assert policy['detected'] is False
    assert policy['logical_cpus'] is None and policy['available_cpus'] is None
    assert policy['recommended_threads'] == 1 and policy['method'] == 'fallback_one_thread'
    assert '取得できない' in policy['reason']


def test_optional_probe_failures_do_not_discard_a_valid_count(cpu_os, monkeypatch):
    def unavailable(*args, **kwargs):
        raise PermissionError('not permitted')
    cpu_os.process_cpu_count = unavailable
    cpu_os.sched_getaffinity = unavailable
    monkeypatch.setattr(resources, 'psutil', SimpleNamespace(cpu_count=unavailable, Process=unavailable))
    policy = resources.cpu_policy()
    assert policy['detected'] is True and policy['available_cpus'] == 8
    assert policy['recommended_threads'] == 6 and policy['physical_cores'] is None
    cpu_os.cpu_count = unavailable
    policy = resources.cpu_policy()
    assert policy['detected'] is False and policy['recommended_threads'] == 1


def test_empty_or_invalid_affinity_is_ignored_instead_of_claiming_no_cpus(cpu_os, monkeypatch):
    cpu_os.sched_getaffinity = lambda pid: set()
    monkeypatch.setattr(resources, 'psutil', SimpleNamespace(
        cpu_count=lambda logical=True: None,
        Process=lambda pid: SimpleNamespace(cpu_affinity=lambda: [0, -1])))
    policy = resources.cpu_policy()
    assert policy['observations'] == {'os.cpu_count': 8}
    assert policy['available_cpus'] == 8


def test_default_factory_rechecks_each_configuration_and_keeps_explicit_history(cpu_os):
    first = SolveConfig()
    assert first.threads == 6
    cpu_os.cpu_count = lambda: 12
    second = SolveConfig()
    assert second.threads == 9
    assert SolveConfig(**first.model_dump()).threads == 6
    assert SolveConfig(threads=2).threads == 2
    assert SolveConfig(threads=64).threads == 64
    assert SolveConfig(threads=1024).threads == 1024
    for invalid in (0, 1025):
        with pytest.raises(ValueError):
            SolveConfig(threads=invalid)


def test_explicit_thread_count_never_calls_automatic_detector(monkeypatch):
    def forbidden():
        raise AssertionError('explicit threads must skip CPU detection')
    monkeypatch.setattr(resources, 'cpu_policy', forbidden)
    assert SolveConfig(threads=19).threads == 19
