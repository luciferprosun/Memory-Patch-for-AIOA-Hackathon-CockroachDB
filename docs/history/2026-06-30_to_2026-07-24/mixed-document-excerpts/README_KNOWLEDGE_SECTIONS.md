# Partial excerpt: README.md — Knowledge and Memory Layer sections

> Provenance header
>
> - Original repository: `/home/l/AOIA_PRODUCTION/repos/AOIA-Core`
> - Original path: `README.md`
> - Source commit: `33dfeb52263a50e23aa7edabdaab1fc47e60c9b9`
> - Source branch or ref: `feature/knowledge-provider-bridge-1a`
> - Source document date: `2026-07-20T10:12:25+02:00`
> - Extraction reason: The source README is a whole-project document, but these sections provide substantive Knowledge HAT, retrieval, provenance and Knowledge Chat context.
> - Scope: This is a partial excerpt. Only the listed original sections were copied; their substantive text is unmodified.

## What AOIA-Core does

- keeps provider output untrusted and critic output metadata-only;
- builds inert `ActionProposal` and `ArtifactPreview` review objects;
- protects controlled writes with the separate canonical human barrier;
- records append-only Durable Audit Ledger evidence;
- provides Knowledge Foundation provenance and validation schemas;
- exposes Linux, Bash, Python, and UNIX knowledge Hats;
- performs deterministic local ingestion, lexical retrieval, and no-dispatch routing;
- renders and verifies an offline static UNIX review prototype;
- retains deterministic validation, freeze, sponsor, and architect-handoff evidence.

---

## Implemented components

- Provider Runtime, Selector, and Critic
- Artifact Preview, controlled write, and canonical human-gate bindings
- ActionProposal and Durable Audit Ledger
- static capability-boundary and adversarial suites
- Knowledge Foundation schemas and provenance
- Linux/RHCSA retrieval compatibility
- Bash safety inspection
- Python knowledge Hat data and read-only loader
- UNIX corpus, retrieval adapter, inert Hat, deterministic routing, and visible prototype
- deterministic UNIX validation/freeze and architect-handoff manifests

---

## Knowledge Hats

- **Linux Hat:** local Linux/RHCSA library and deterministic retrieval compatibility.
- **Bash Hat:** command parsing and pre-execution safety classification; it does not execute shell text.
- **Python Hat:** curated Python knowledge, validation records, and a read-only loader.
- **UNIX Hat:** capability-empty descriptor, deterministic no-dispatch routing, explicit read-only retrieval, and offline review rendering.

All Hat results are non-authoritative metadata.

---

## Knowledge Module control plane

The provider-independent control plane lists logical modules and concrete
read-only instances without activating either:

```bash
aoia-knowledge-hub list-modules --repository-root . --format json
aoia-knowledge-hub list-instances --repository-root . --module de-law-federal-1a --format json
aoia-knowledge-hub query --repository-root . --question "Explain evidence and authority." --format json
```

The zero-module query is valid and returns `NO_KNOWLEDGE_MODULE_SELECTED`.
Selections are explicit and request-only; provider selection remains separate.

The provider-independent context bridge can bind the same deterministic context
package to an explicitly selected provider target. Dry-run is the default and
does not perform a live provider call:

```bash
aoia-knowledge-chat --repository-root . --provider openrouter_chat --model explicit-model-id --question "Explain the purpose of the Nachweisgesetz." --dry-run --format json
```

Module selection remains a separate set of explicit `--enable-module`,
`--instance`, and module-specific retrieval options. Evidence is serialized as
untrusted data and cannot grant approval, write, execution, Git, or gate authority.

---

## Current limitations

- This is not production 1.0 and is not a complete terminal security product.
- The UNIX corpus contains 13 canonical normalized records from one approved extracted source; it is not all UNIX knowledge.
- UNIX retrieval is local and lexical, without remote embeddings or provider reasoning.
- Scores and deterministic routes may be incomplete or wrong and remain metadata.
- The prototype does not administer a real machine and executes no command.
- Human review remains required before consequential use.
- The current freeze is a local dirty-worktree evidence record, not a Git release.
