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
    verifier = load_script('verify_tool_image')
    probe = verifier.PROBE
    compile(probe, '<toolbox-probe>', 'exec')
    compile(verifier.EXTRA_TOOL_PROBE, '<extra-toolbox-probe>', 'exec')
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


def test_historical_missing_tools_are_declared_and_accepted():
    import json
    inventory = ROOT / 'docker' / 'tooling' / 'inventory.py'
    spec = importlib.util.spec_from_file_location('tool_inventory', inventory)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert {'objdump', 'factor', 'upx', 'go', 'veracrypt', 'cryptsetup'} <= set(module.BINARIES)
    recipe = (inventory.parent / 'Dockerfile').read_text()
    assert 'sha256sum -c -' in recipe and 'cryptsetup-bin' in recipe and 'golang-go' in recipe
    from ctfbot.packs.catalog import catalog, playbook
    packs = catalog()
    assert {'factor', 'veracrypt', 'cryptsetup'} <= set(packs['crypto']['optional_binaries'])
    assert {'objdump', 'upx', 'go'} <= set(packs['reverse']['optional_binaries'])
    assert {'veracrypt', 'cryptsetup'} <= set(packs['forensics']['optional_binaries'])
    assert 'GOCACHE' in playbook('reverse') and 'device-mapper' in playbook('forensics')
    assert {'bkcrack', 'fcrackzip', 'john', 'zip2john', 'hashcat', 'zsteg', 'sox', 'sage'} <= set(module.BINARIES)
    for pack in ('crypto', 'forensics'):
        assert {'bkcrack', 'fcrackzip', 'john', 'zip2john', 'hashcat'} <= set(packs[pack]['optional_binaries'])
    assert {'zsteg', 'sox'} <= set(packs['stego']['optional_binaries'])
