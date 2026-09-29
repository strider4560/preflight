import subprocess
from pathlib import Path

import pytest


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path.resolve()


from preflight.gate import unload_consumer  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_consumer():
    unload_consumer()
    yield
    unload_consumer()
