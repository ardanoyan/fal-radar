"""The pre-commit secret check, run against throwaway files. Fake keys are built at runtime."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "precommit-secrets.sh"

FAKE_GHP = "ghp_" + "A1b2C3d4" * 4 + "Zz9Y"
FAKE_PAT = "github_pat_" + "11ABCDEFG" + "0" * 20 + "_xyz"
FAKE_FAL = "0a1b2c3d-4e5f-6a7b-8c9d-0e1f2a3b4c5d" + ":" + "ab" * 16


def check(tmp_path: Path, text: str) -> subprocess.CompletedProcess:
    f = tmp_path / "sample.txt"
    f.write_text(text)
    return subprocess.run(["bash", str(SCRIPT), str(f)], capture_output=True, text=True)


@pytest.mark.parametrize(
    "text",
    [
        f"token = '{FAKE_GHP}'",
        f"GH_SEARCH_TOKEN={FAKE_PAT}",
        "FAL" + "_KEY=sk-something",
        "FAL" + "_KEY = 'abc'",
        f"key: {FAKE_FAL}",
    ],
)
def test_flags_secrets_without_printing_them(tmp_path, text):
    result = check(tmp_path, text)
    assert result.returncode == 1
    secret = text.split("=", 1)[-1].split(":", 1)[-1].strip(" '")
    assert secret not in result.stderr


@pytest.mark.parametrize(
    "text",
    [
        "import fal_client\nfrom fal_client import submit",
        "FAL" + "_KEY=\nGH_SEARCH_TOKEN=",
        "FAL" + "_KEY: ${{ secrets.FAL_KEY }}",
        "FAL" + "_KEY = os.environ['FAL_KEY']",
        "const key = process.env.FAL" + "_KEY",
        "uuid only: 0a1b2c3d-4e5f-6a7b-8c9d-0e1f2a3b4c5d",
    ],
)
def test_leaves_normal_code_alone(tmp_path, text):
    assert check(tmp_path, text).returncode == 0


def test_staged_mode_blocks_a_secret_and_a_dotenv(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    run = lambda *a: subprocess.run(a, cwd=repo, capture_output=True, text=True)  # noqa: E731
    run("git", "init", "-q")
    (repo / "ok.py").write_text("import fal_client\n")
    run("git", "add", "ok.py")
    assert run("bash", str(SCRIPT)).returncode == 0
    (repo / "bad.py").write_text(f"TOKEN = '{FAKE_GHP}'\n")
    run("git", "add", "bad.py")
    result = run("bash", str(SCRIPT))
    assert result.returncode == 1 and "bad.py" in result.stderr and FAKE_GHP not in result.stderr
    run("git", "rm", "-q", "--cached", "bad.py")
    (repo / ".env").write_text("GH_SEARCH_TOKEN=\n")
    run("git", "add", "-f", ".env")
    assert run("bash", str(SCRIPT)).returncode == 1
