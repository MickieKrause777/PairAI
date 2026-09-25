import subprocess

import pytest


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path):
    """A real git repo with one tracked, committed file ready to be modified."""
    _git(["init", "-q"], tmp_path)
    _git(["config", "user.email", "driver@example.com"], tmp_path)
    _git(["config", "user.name", "Driver"], tmp_path)
    (tmp_path / "foo.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    _git(["add", "foo.py"], tmp_path)
    _git(["commit", "-q", "-m", "initial"], tmp_path)
    return tmp_path
