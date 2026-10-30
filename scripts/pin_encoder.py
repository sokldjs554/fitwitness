"""Resolve a Hugging Face model revision and print the hash-locked entry for retrieval/models.json.

Run where huggingface.co is reachable (the pin workflow). It downloads only the named files, prints their
SHA-256 beside the resolved commit, and verifies nothing: the printed entry is what a person commits, and
scripts/download_encoders.py verifies against it from then on."""

import argparse, json, sys, urllib.request
from hashlib import sha256
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("model_id")
p.add_argument("--revision", default="main")
p.add_argument("--files", nargs="+", required=True)
p.add_argument("--folder", required=True)
p.add_argument("--license", default="")
a = p.parse_args()
with urllib.request.urlopen(f"https://huggingface.co/api/models/{a.model_id}/revision/{a.revision}") as r:
    info = json.load(r)
commit = info["sha"]
work = Path("artifacts/pin") / a.folder
work.mkdir(parents=True, exist_ok=True)
files = {}
for name in a.files:
    target = work / name
    target.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(f"https://huggingface.co/{a.model_id}/resolve/{commit}/{name}", target)
    files[name] = sha256(target.read_bytes()).hexdigest()
    print(f"{name}: {target.stat().st_size} bytes", file=sys.stderr)
print(
    "== pin", json.dumps({"model_id": a.model_id, "revision": commit, "license": a.license, "folder": a.folder, "files": files}, indent=2)
)
