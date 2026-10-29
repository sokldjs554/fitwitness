"""Continuing a claim a reviewer held for documents.

A reviewer who answers ``REQUEST_INFO`` ends that run with nothing paid and nothing refused. The claimant then sends
the documents asked for, and the claim is reviewed again. This module plans that second run: the same claim
(so the ledger still pays it at most once), the held run's documents with any document of the same kind replaced,
and a record of which run it continues. The first run stays as it was; the second one links back to it.

Every document in this system is synthetic and is seeded with its case, so "sending" a document means choosing one
of the case's waiting documents. There is no upload.
"""

from __future__ import annotations

from fitwitness.claims.models import ClaimRequest, Resubmission

HELD_CODE = "info_requested"


class ResubmitError(ValueError):
    """The documents cannot be attached to this run. ``conflict`` is a state problem, not a bad request."""

    def __init__(self, message: str, *, conflict: bool = False):
        super().__init__(message)
        self.conflict = conflict


def held_reason(result: dict | None) -> dict | None:
    """The reviewer's request for documents on a finished claim run, if that is how it ended."""
    decision = (result or {}).get("decision") or {}
    return next((r for r in decision.get("reasons", []) if r.get("code") == HELD_CODE), None)


def plan(case: dict, run: dict, document_ids: list[str]) -> ClaimRequest:
    """The claim request for the run that continues ``run`` (a stored claim run) with ``document_ids``."""
    if (run["request"] or {}).get("kind") != "claim":
        raise ResubmitError("서류를 접수할 수 있는 것은 청구 심사 실행뿐입니다", conflict=True)
    if run["state"] != "completed" or held_reason(run["result"]) is None:
        raise ResubmitError("보완 요청으로 보류된 청구에만 서류를 접수할 수 있습니다", conflict=True)
    previous = ClaimRequest.model_validate(run["request"]["claim"])
    waiting = {d["id"]: d for d in case.get("supplementary_documents") or []}
    kinds = {d["id"]: d["kind"] for d in [*case["documents"], *waiting.values()]}
    if len(set(document_ids)) != len(document_ids):
        raise ResubmitError("같은 서류를 두 번 접수할 수 없습니다")
    for doc_id in document_ids:
        if doc_id not in waiting:
            raise ResubmitError(f"이 청구에 접수할 수 있는 서류가 아닙니다: {doc_id}")
        if doc_id in previous.document_ids:
            raise ResubmitError(f"이미 접수된 서류입니다: {doc_id}", conflict=True)
    added_kinds = [kinds[i] for i in document_ids]
    if len(set(added_kinds)) != len(added_kinds):
        raise ResubmitError("같은 종류의 서류는 한 번에 하나만 접수할 수 있습니다")
    replaced = [i for i in previous.document_ids if kinds.get(i) in added_kinds]
    kept = [i for i in previous.document_ids if i not in replaced]
    human = (run["result"] or {}).get("human") or {}
    return previous.model_copy(update={
        "document_ids": [*kept, *document_ids],
        "resubmission": Resubmission(
            of_run=run["id"], attempt=(previous.resubmission.attempt + 1) if previous.resubmission else 2,
            added=list(document_ids), replaced=replaced, held_by=human.get("reviewer", ""), hold_note=human.get("note", ""),
        ),
    })
