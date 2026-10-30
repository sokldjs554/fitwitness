"""The searchable state of one tenant's corpus, read once per change instead of once per request.

A search used to read every revision and fact of the tenant three times (the snapshot before it, the corpus inside it,
the snapshot after it) and build a BM25 index over all of them for every query. At 100,000 drawings that was 84 seconds
and 5.4 GB for one search. The state here is built in one paged pass, keeps only what search needs (ids, numbers, kinds
and the text of each active drawing), and is reused until the tenant's corpus changes.

"Changes" is decided by the database, not by this process: ``fw_corpus_version`` holds a counter per tenant that
triggers on the revision, fact and vector tables bump in the same transaction as any write. A state is reused only when
the counter (and the random epoch the row was created with) is the one it was built under. The snapshot id is the same
SHA-256 over the same rows as before, computed from the pages as they arrive, so ids already recorded keep their value.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
import threading

from fitwitness.contracts import Fact, SearchSnapshot

PAGE = 5000
MAX_CACHED_DRAWINGS = 200_000  # across tenants; the least recently used state goes first
MAX_CACHED_STATES = 16


def active_ids(entries, now: datetime | None = None) -> tuple[list[str], list[str]]:
    """The revisions in force and the documents with competing revisions.

    ``entries`` are ``(document_id, id, supersedes, approval, effective_from)`` tuples. A revision counts once it is
    not a draft and its date has come; per document the one that no other revision supersedes is in force when it is
    approved, and two such leaves are a conflict."""
    now = now or datetime.now(timezone.utc)
    groups: dict[str, list[tuple]] = {}
    for entry in entries:
        document_id, _, _, approval, effective_from = entry
        if approval != "draft" and datetime.fromisoformat(effective_from) <= now:
            groups.setdefault(document_id, []).append(entry)
    active, conflicts = [], []
    for document_id, rs in groups.items():
        superseded = {r[2] for r in rs}
        leaves = [r for r in rs if r[1] not in superseded]
        if len(leaves) == 1 and leaves[0][3] == "approved":
            active.append(leaves[0][1])
        elif len(leaves) > 1:
            conflicts.append(document_id)
    return sorted(active), sorted(conflicts)


def _dumps(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _fact_text(data: dict) -> str:
    """``"{field} {value}"`` as the search text has always spelled a fact. A value that is not a string is an
    interval; it is read through the model so it prints the way the model prints it."""
    value = data["value"]
    if not isinstance(value, str):
        value = Fact.model_validate(data).value
    return f"{data['field']} {value}"


@dataclass
class CorpusState:
    label: tuple[str, int]  # (epoch, version) of the tenant's counter when this was built
    snapshot: SearchSnapshot
    ids: list[str]  # active revisions, in id order
    numbers: list[str]
    kinds: list[str]
    texts: list[str]  # title, number, kind and "field value" of each fact: what BM25 indexes
    memo: dict = field(default_factory=dict)  # indexes built from the above on first use
    lock: threading.Lock = field(default_factory=threading.Lock)

    def kind_by_id(self) -> dict[str, str]:
        return self.memo.setdefault("kinds", dict(zip(self.ids, self.kinds)))

    def positions_by_number(self) -> dict[str, list[int]]:
        with self.lock:
            if "by_number" not in self.memo:
                by_number: dict[str, list[int]] = {}
                for i, number in enumerate(self.numbers):
                    by_number.setdefault(number, []).append(i)
                self.memo["by_number"] = by_number
            return self.memo["by_number"]

    def size(self) -> int:
        return len(self.ids)


def read_label(c, tenant_id: str) -> tuple[str, int]:
    row = c.execute("SELECT epoch,version FROM fw_corpus_version WHERE tenant_id=%s", (tenant_id,)).fetchone()
    return (row["epoch"], int(row["version"])) if row else ("", 0)


def build_state(c, tenant_id: str, label: tuple[str, int]) -> CorpusState:
    """One paged pass over the tenant's revisions, facts and vector digests on an open connection."""
    revisions: list[dict] = []
    last = None
    while True:
        rows = (
            c.execute("SELECT id,data FROM fw_revisions ORDER BY id LIMIT %s", (PAGE,))
            if last is None
            else c.execute("SELECT id,data FROM fw_revisions WHERE id>%s ORDER BY id LIMIT %s", (last, PAGE))
        ).fetchall()
        revisions.extend(r["data"] for r in rows)
        if len(rows) < PAGE:
            break
        last = rows[-1]["id"]
    ids_active, _ = active_ids((d["document_id"], d["id"], d.get("supersedes"), d["approval"], d["effective_from"]) for d in revisions)
    wanted = set(ids_active)
    # sha256 over json.dumps({"revisions": [...], "facts": [...], "vectors": [...], "active_ids": [...]}, sort_keys=True):
    # keys in sorted order, each list written element by element so no page has to wait for the next.
    digest = sha256()
    digest.update(('{"active_ids": ' + _dumps(ids_active) + ', "facts": [').encode())
    parts: dict[str, list[str]] = {}
    first, last = True, None
    while True:
        rows = (
            c.execute("SELECT id,revision_id,data FROM fw_facts ORDER BY id LIMIT %s", (PAGE,))
            if last is None
            else c.execute("SELECT id,revision_id,data FROM fw_facts WHERE id>%s ORDER BY id LIMIT %s", (last, PAGE))
        ).fetchall()
        for row in rows:
            digest.update((("" if first else ", ") + _dumps({"data": row["data"]})).encode())
            first = False
            if row["revision_id"] in wanted:
                parts.setdefault(row["revision_id"], []).append(_fact_text(row["data"]))
        if len(rows) < PAGE:
            break
        last = rows[-1]["id"]
    digest.update(b'], "revisions": [')
    digest.update(", ".join(_dumps({"data": d}) for d in revisions).encode())
    vectors = c.execute(
        "SELECT revision_id,channel,md5(embedding::text) AS hash,metadata FROM fw_vectors ORDER BY revision_id,channel"
    ).fetchall()
    digest.update(('], "vectors": ' + _dumps(vectors) + "}").encode())
    snapshot_id = digest.hexdigest()
    ids, numbers, kinds, texts = [], [], [], []
    for d in revisions:
        if d["id"] in wanted:
            ids.append(d["id"])
            numbers.append(d["drawing_number"])
            kinds.append(d["kind"])
            texts.append(d["title"] + " " + d["drawing_number"] + " " + d["kind"] + " " + " ".join(parts.get(d["id"], [])))
    return CorpusState(
        label=label,
        snapshot=SearchSnapshot(id=snapshot_id, revision_ids=ids_active, index_hash=snapshot_id),
        ids=ids,
        numbers=numbers,
        kinds=kinds,
        texts=texts,
    )


_STATES: "OrderedDict[tuple[str, str], CorpusState]" = OrderedDict()
_STATES_LOCK = threading.Lock()
_BUILD_LOCKS: dict[tuple[str, str], threading.Lock] = {}


def _remember(key, state: CorpusState) -> None:
    with _STATES_LOCK:
        _STATES[key] = state
        _STATES.move_to_end(key)
        while len(_STATES) > MAX_CACHED_STATES or (len(_STATES) > 1 and sum(s.size() for s in _STATES.values()) > MAX_CACHED_DRAWINGS):
            _STATES.popitem(last=False)


def state_in(c, dsn: str, tenant_id: str) -> CorpusState:
    """The tenant's current state on an open connection: the remembered one when the counter has not moved."""
    key = (dsn, tenant_id)
    label = read_label(c, tenant_id)
    with _STATES_LOCK:
        hit = _STATES.get(key)
        if hit is not None and hit.label == label:
            _STATES.move_to_end(key)
            return hit
        build_lock = _BUILD_LOCKS.setdefault(key, threading.Lock())
    with build_lock:
        with _STATES_LOCK:
            hit = _STATES.get(key)
            if hit is not None and hit.label == label:
                return hit
        state = None
        for _ in range(3):
            label = read_label(c, tenant_id)
            state = build_state(c, tenant_id, label)
            if read_label(c, tenant_id) == label:
                break
        else:
            return state  # the corpus kept changing under the read: use this once, remember nothing
        # A transaction that has written has bumped the counter itself, uncommitted: its state must not outlive it.
        if c.execute("SELECT txid_current_if_assigned() AS x").fetchone()["x"] is None:
            _remember(key, state)
        return state


def forget(dsn: str | None = None) -> None:
    """Drop remembered states (tests that rebuild a database under the same name)."""
    with _STATES_LOCK:
        for key in [k for k in _STATES if dsn is None or k[0] == dsn]:
            del _STATES[key]
