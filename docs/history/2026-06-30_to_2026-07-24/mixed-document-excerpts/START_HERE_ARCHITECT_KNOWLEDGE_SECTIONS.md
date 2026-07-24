# Partial excerpt: START_HERE_ARCHITECT.md — Knowledge architecture sections

> Provenance header
>
> - Original repository: `/home/l/AOIA_PRODUCTION/repos/AOIA-Core`
> - Original path: `START_HERE_ARCHITECT.md`
> - Source commit: `b7a3a1481ce382e516ed0d39e5ac334f3240c727`
> - Source branch or ref: `feature/m2-b0-provider-critic-inert-core`
> - Source document date: `2026-07-15T03:59:22+02:00`
> - Extraction reason: The full architect handoff spans the whole runtime; these sections substantively map Knowledge Hats, retrieval, corpus/index bindings and their authority boundary.
> - Scope: This is a partial excerpt. Only the listed original sections were copied; their substantive text is unmodified.

## Critical invariants

Provider output is never authority. Critic output, `ActionProposal`,
`ArtifactPreview`, knowledge, Hats, routes, retrieval results, scores, audit
records, manifests, and freeze evidence are metadata only. Only the existing
separate canonical human barrier may authorize its exact controlled path.

---

## Architecture map

- Core runtime and boundaries: `runtime/`
- Provider surfaces: `runtime/providers/`, `runtime/provider_critic/`
- Gate, preview, controlled write, and audit: `runtime/safety/`, `runtime/control_write.py`, `runtime/audit/`
- Linux retrieval: `runtime/retrieval/linux/`
- Bash inspection: `runtime/safety/bash_parser.py`
- Python Hat: `knowledge/hats/hat_003_python/`, `runtime/knowledge/`
- UNIX Hat/routing/prototype: `runtime/memory_hats/unix_hat.py`, `runtime/orchestrator/knowledge_router.py`, `runtime/visible_unix_prototype.py`
- Current retained data: `data/`
- Current freeze: `data/unix_full_validation_freeze_1a_r1/`
- Complete file inventory: `data/architect_handoff_manifest_1a.json`
- Tests: `tests/`

---

## Limitations and change control

The corpus is bounded, local lexical retrieval can be incomplete, and no score
or evidence record proves correctness. The repository retains compatibility
surfaces that may expose controlled capabilities; do not broaden them, bypass
the human barrier, reinterpret metadata as permission, or alter corpus/index/
freeze bindings without focused review and full regression validation.

Next development step: isolated clean-clone and complete prototype validation.
Commit, push, release archive, and deployment remain separate approvals.
