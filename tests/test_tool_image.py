"""Build contract checks; Docker functional acceptance is an explicit script."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'doc' / 'phase-d' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_toolbox_base_is_immutable_and_mutable_override_is_rejected(tmp_path):
    builder = load_script('build_tool_image')
    assert '@sha256:' in builder.BASE
    with pytest.raises(ValueError, match='pinned'):
        builder.build(tmp_path / 'build', 'ctfbot-tools:candidate', base='python:latest')
    assert not (tmp_path / 'build').exists()


def test_functional_probe_compiles_and_uses_only_authored_runtime_paths():
    probe = load_script('verify_tool_image').PROBE
    compile(probe, '<toolbox-probe>', 'exec')
    assert '/work/probe.c' in probe and '/challenge/probe.txt' in probe
    assert 'set disable-randomization off' in probe


def test_image_has_hashed_requirements_inventory_and_unprivileged_default():
    context = ROOT / 'docker' / 'tooling'
    recipe = (context / 'Dockerfile').read_text()
    assert '--require-hashes' in recipe
    assert 'USER 65532:65532' in recipe
    assert 'VOLUME ' not in recipe
    assert 'inventory.json' in recipe
    locked = (context / 'requirements.lock').read_text()
    for dependency in ('pwntools', 'pycryptodome', 'sympy', 'z3-solver', 'capstone', 'unicorn', 'pillow', 'scapy'):
        assert dependency + '==' in locked
    assert '--hash=sha256:' in locked
