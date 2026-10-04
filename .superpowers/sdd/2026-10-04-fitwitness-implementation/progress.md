# SDD ledger — plan: docs/superpowers/plans/2026-10-04-fitwitness-implementation.md

User approved spec, plan, native execution and browser repository creation.
New private remote: https://github.com/sokldjs554/fitwitness, initial remote SHA 369d774568ec34d7ae394b9d21600531cd1636b0.
Ruling: new isolated directory + feature branch is the workspace; no linked worktree needed because no existing code/branch is shared — avoids redundant checkout — no existing source is at risk.
Pre-flight: Tasks 1→2/3/4 share contracts; no incompatible names. Tasks 2/4/5→6 share repo/snapshot/tools; scope must be server-owned. Tasks 2/6→7/8 share snapshot/lease/finalize; finalization must be atomic. Tasks 8→9 share OpenAPI/events; schema generation prevents duplicate UI verdict logic. Tasks 1–9→10 consume actual results, not gold at inference. Tasks 10→11/12 require truthful blocked-gate propagation.
Ruling: runtime cache writes use /tmp/fitwitness-* because root cache is read-only — reversible environment adaptation, no product behavior change.
Task 1: complete (commits 9cc5033..e579bdc, tests: .venv/bin/python -m pytest tests/unit -q → 17 passed in 12.27s)
Task 1: CAD contact sheet visually inspected across four shapes. Adjusted drawing annotation contrast and shaft projection to match generated CAD. Full corpus generated at var/corpus (180 revisions).
Task 2: Ruling: psycopg transaction repository with explicit SQL schema is used instead of ORM wrappers — RLS/row locks are directly testable, unchanged public contract — cost: future ORM integration is separate work.
Task 2: Ruling: packaged PostgreSQL 16 binaries + bundled pgvector run under existing nobody account in /tmp; socket tests require approved sandbox escalation — real PostgreSQL, not SQLite — cost: reproducibility setup must be documented.
Task 2: storage/RLS tests 8 passed on real PostgreSQL; finalization-race contract is implemented and tested with Task 6, not yet complete.
Task 3: vector PDF region extraction and actual STEP bbox/volume/mesh tests 5 passed; rotated/scanned/OCR perturbation coverage and parser sandbox limits remain.
Task 4: small-corpus BM25 negative IDF caused empty retrieval (workflow test RED). Fix selects lexical overlap, not score>0; workflow suite GREEN 5/5. E5 384d and OpenCLIP 512d real model smoke passed.
Task 5: typed tool/schema/budget unit tests 5 passed; actual OpenAI/Anthropic credentials not present, no live provider execution claimed.
Task 6: actual worker exit 86 after retrieval checkpoint + new process resume passed; one retrieved event and one completion. Stale worker fencing test passed. Full PostgreSQL integration suite 16 passed.
Task 8: API tests 4 passed (real child workers), including tenant run isolation, cross-origin write rejection, revision invalidation and disconnected provider rejection.
Task 8: Ruling: event endpoint currently uses cursor-based JSON polling; SSE is not implemented yet — reliable stored events first — cost: polling latency and no SSE completion claim.
Task 7: Ruling: current revision invalidation is conservative at catalog snapshot granularity; minimal impacted-node revalidation remains unimplemented — safety before optimization — cost: extra revalidation calls.
Recovery 2026-10-04: workspace was restored to an earlier snapshot; later claimed measurements/artifacts were not present. Re-ran actual available unit suite 34/34; remote source preserved in GitHub, PR #1. CI at da6e4ef passed real PostgreSQL + browser flow in two independent environments and captured screenshots/video.
Final review: fresh gpt-6-astra reviewer identified seven Important issues. Regression RED verified 5/5 pure tests locally and all 10 review regressions in GitHub CI run37178482633. Fix pass underway: required numeric constraints, quotas and disabled anonymous paid APIs, per-run DB session fencing, durable dispatch, effective dates, read-time stale detection, durable budget reservation ledger.
Final: Ruling: hosted anonymous demo never invokes paid providers even if server keys exist; use an explicitly configured operator CLI for live research — aggregate spending cannot be authorized by anonymous cookies — cost: live-model UI is unavailable until authenticated access/billing is built.
Final: Ruling: read-time snapshot validation is the safety fallback for interrupted ingestion, in addition to eager invalidation — no completed old snapshot can be returned after change — cost: extra snapshot query per result read and possible temporary unknown on incomplete ingestion.

Final fix pass: GitHub CI run37178871171 at42851e8: both independent database test steps passed; both desktop/mobile E2E steps passed; both real demo captures succeeded. Full release remains incomplete because live-provider evaluation, public deployment and documented scope are not complete.

Deployment follow-up: User confirmed free deployment in My workspace. Render creation rejected HTTP 400: only one active free Postgres; no FitWitness resources created, no existing resource changed. Docker creation also requires Dashboard because connector lacks support.
CI final status investigation: run37178871171 passed functional steps but setup-uv post cleanup failed because background uv run held cache lock for >300s. Replace background launcher with .venv/bin/uvicorn; no runtime app code changed. Await rerun before claiming overall CI green.

CI launcher fix verified: run37180224738 at7058f797, both verify (1) and verify (2) completed success, including setup-uv cleanup. Public deployment remains blocked by free database quota.
