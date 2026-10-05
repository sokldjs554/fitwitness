# Demo audit — 2026-10-05

User report: only `0004` appears. On the previously deployed commit `e2a1dbd9`, a fresh browser always initially selected FW-000-4 (UUID ordering). Clicking all five cards did load five distinct PNG sources in the audit session; inability to select other cards was not universally reproducible. Existing tests had only clicked the first result. The audit found additional actual failures that could leave the user stranded.

## Corrections

- Drawing-number order starts at FW-000-0. A persistent direct selector and previous/next controls expose every active source, independently of search results. Switching retains the chosen 2D/3D mode.
- Exact search text is no longer silently suffixed with `브래킷`. Korean part names with common particles are recognized by lexical retrieval. Unrelated tokens and exact drawing IDs are preserved.
- The catalog uses current revisions after invalidation instead of reusing superseded IDs from the previous run. Unsearched drawings remain accessible and are labeled `검색 제외`.
- Source-image failures have a retry control. Model requests validate HTTP/mesh responses, abort on selection changes, dispose graphics resources, and reset loading/error state.
- When WebGL is unavailable, actual downloaded mesh triangles produce a labeled static 2D projection. This is not an interactive 3D rendering or generated illustration.
- Lab selectors remain usable on failed requests. Empty query/case sets cannot dereference missing selections. Error-only filtering cannot display an excluded success case. Agent errors no longer silently show an older successful report. Missing repetitions and broken source images are explicit.

## Verification scope

Regression tests cover all five drawing selections, distinct decoded PNGs, matching PDF links, exact submitted text, revision B selection, source retries, mesh request races/retries, and desktop/mobile layouts. Lab tests traverse every published case/query/repeat and simulate failed/empty responses. Simulated network boundaries are isolated UI regressions; the existing suite and CI also exercise real API/PostgreSQL workflows.

Local managed runtime cannot launch PostgreSQL under a non-root user. Local UI tests use a clearly separate temporary fixture server with source assets downloaded from the public demo. The final integration gate runs actual PostgreSQL 16/pgvector in GitHub Actions. No local fixture server is part of the product.
