"""Matplotlib font cache seeding for restricted macOS sandboxes."""
import sys

import pytest

sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]/'scripts'))
import _sandbox


@pytest.mark.skipif(sys.platform != 'darwin', reason='system_profiler font scan is macOS only')
def test_seed_writes_the_font_list_once(tmp_path):
    assert _sandbox.seed_matplotlib_font_cache(tmp_path) is True
    assert list(tmp_path.glob('fontlist-v*.json'))
    assert _sandbox.seed_matplotlib_font_cache(tmp_path) is False, 'an existing cache is left alone'


def test_seed_is_a_no_op_off_macos(tmp_path, monkeypatch):
    monkeypatch.setattr(_sandbox.sys, 'platform', 'linux')
    assert _sandbox.seed_matplotlib_font_cache(tmp_path) is False and not list(tmp_path.iterdir())
