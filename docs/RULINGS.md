# 구현 중 판단과 비용

Ruling: new isolated directory + feature branch is the workspace; no linked worktree needed because no existing code/branch is shared — avoids redundant checkout — no existing source is at risk.

Ruling: runtime cache writes use /tmp/fitwitness-* because root cache is read-only — reversible environment adaptation, no product behavior change.

Task 2: Ruling: psycopg transaction repository with explicit SQL schema is used instead of ORM wrappers — RLS/row locks are directly testable, unchanged public contract — cost: future ORM integration is separate work.

Task 2: Ruling: packaged PostgreSQL 16 binaries + bundled pgvector run under existing nobody account in /tmp; socket tests require approved sandbox escalation — real PostgreSQL, not SQLite — cost: reproducibility setup must be documented.

Task 8: Ruling: event endpoint currently uses cursor-based JSON polling; SSE is not implemented yet — reliable stored events first — cost: polling latency and no SSE completion claim.

Task 7: Ruling: current revision invalidation is conservative at catalog snapshot granularity; minimal impacted-node revalidation remains unimplemented — safety before optimization — cost: extra revalidation calls.

Final: Ruling: hosted anonymous demo never invokes paid providers even if server keys exist; use an explicitly configured operator CLI for live research — aggregate spending cannot be authorized by anonymous cookies — cost: live-model UI is unavailable until authenticated access/billing is built.

Final: Ruling: read-time snapshot validation is the safety fallback for interrupted ingestion, in addition to eager invalidation — no completed old snapshot can be returned after change — cost: extra snapshot query per result read and possible temporary unknown on incomplete ingestion.
