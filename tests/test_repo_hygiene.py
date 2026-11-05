"""Guards against the mistake that exposed the database and keys on GitHub."""
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORBIDDEN = re.compile(r"(\.db$|\.db-wal$|\.key$|(^|/)\.env$|\.secret_key$|(^|/)data/)")


def test_gitignore_covers_secrets_and_data():
    text = open(os.path.join(ROOT, ".gitignore")).read()
    for entry in (".env", "data/", "*.db", "*.key", ".secret_key"):
        assert entry in text.split(), f".gitignore must list {entry}"


@pytest.mark.skipif(not shutil.which("git") or not os.path.isdir(os.path.join(ROOT, ".git")), reason="no git repository")
def test_no_secret_or_database_file_is_tracked():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
    bad = [f for f in files if FORBIDDEN.search(f)]
    assert not bad, f"These files must never be committed: {bad}"


def test_example_env_has_no_real_secrets():
    text = open(os.path.join(ROOT, ".env.example")).read()
    for line in text.splitlines():
        if line.startswith(("HRMS_SECRET_KEY=", "HRMS_FIELD_KEY=", "POSTGRES_PASSWORD=")):
            assert line.split("=", 1)[1].strip() == "", f"{line.split('=')[0]} must be empty in .env.example"
