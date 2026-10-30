"""The synthetic claim corpus is a function of its seed, not of the process that built it."""

import hashlib
import os
import subprocess
import sys
from pathlib import Path


def _build(target: Path, hash_seed: str) -> dict[str, str]:
    env = {**os.environ, "PYTHONHASHSEED": hash_seed}
    subprocess.run([sys.executable, "-m", "fitwitness.claims.synth", str(target), "4", "7"], check=True, env=env, capture_output=True)
    return {str(p.relative_to(target)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(target.rglob("*")) if p.is_file()}


def test_two_processes_with_different_hash_salts_build_the_same_bytes(tmp_path):
    first, second = _build(tmp_path / "a", "1"), _build(tmp_path / "b", "2")
    assert first and first == second  # document and licence numbers no longer come from the salted hash()
