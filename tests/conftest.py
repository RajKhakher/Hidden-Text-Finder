import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_sample_builder():
    spec = importlib.util.spec_from_file_location("make_samples", ROOT / "samples" / "make_samples.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def sample_builder():
    return _load_sample_builder()


@pytest.fixture(scope="session")
def samples(tmp_path_factory, sample_builder):
    """Freshly generated copies of every sample file, keyed by file name."""
    out = tmp_path_factory.mktemp("samples")
    return {p.name: p for p in sample_builder.make_all(out)}


def techniques(result):
    return [f.technique for f in result.findings]
