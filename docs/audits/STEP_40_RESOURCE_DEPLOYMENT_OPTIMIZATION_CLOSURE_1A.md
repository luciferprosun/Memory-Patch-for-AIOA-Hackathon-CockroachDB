# Step 40 resource and deployment optimization closure 1A

## Record status

`PRE-COMMIT CLOSURE RECORD`. Step 40 implementation, controlled resource
validation, focused validation, checkpoint validation, and final full
regression are complete in the closure worktree. The Step 40 Git closure SHA
is `NOT CREATED`, push is `NOT PERFORMED`, and Step 41 is `NOT STARTED`.

## Starting state

| Field | Value |
|---|---|
| Repository branch | `main` |
| Exact Step 39 base | `90c2563556fea96ee120b264166640f277677acd` |
| Local HEAD at start | exact Step 39 base |
| `origin/main` at start | exact Step 39 base |
| Start divergence | `0 0` |
| Start worktree | clean |
| Active Git operation | none |
| Step 39 | COMPLETE AND PUSHED |
| Step 40 | NOT STARTED at guard; implemented only after gate |
| Step 41 | NOT STARTED |

The Step 39 architecture, ADR-046, operations doc, evidence, closure record,
roadmap, and AGENTS checkpoint were all reachable from the exact base.

## Runtime inventory and baseline

The runtime inventory is
`docs/performance/STEP40_RUNTIME_COMPONENT_INVENTORY_1A.md`. Before Step 40
changes, the host exposed 3,751 MiB physical memory, 1,016 MiB available, 249
processes, and 818 threads. Unchanged scenario peaks were:

| Scenario | Pre-change peak RSS |
|---|---:|
| idle core | 65,640 KiB |
| retrieval-only | 76,836 KiB |
| Step 38 provider-free core | 78,596 KiB |
| Personal Memory approval/activation and active retrieval | 40,220 KiB |
| Step 39 Critic bridge lane | 83,148 KiB |
| owner UI | 72,072 KiB |
| real local E5 | 694,432 KiB |

The pre-change local CockroachDB probe retained its existing 640 MiB store, 64
MiB cache, and 128 MiB SQL memory but did not become ready within the existing
bound under host pressure. It left no process or temporary store. No RSS was
invented from configured memory.

## Budget decision

ADR-047 freezes a constrained demo profile, not production HA. The canonical
configuration is `config/runtime/4gb-demo-1a.json`, profile digest
`eb205843f34039418bd49663ab47390777cbdc861f671172c8f025d588a55ad8`.

| Budget | Value |
|---|---:|
| Nominal host | 4,096 MiB |
| Minimum detected host | 3,700 MiB |
| OS/filesystem headroom | 700 MiB minimum |
| Idle target | 512 MiB |
| Steady target | 2,200 MiB |
| Peak target | 3,000 MiB |
| Hard observed usage | 3,400 MiB |
| Minimum available memory | 384 MiB |

## Optimizations implemented

1. One web/backend worker serves Kernel, server-rendered owner UI, static HTMX,
   request-driven review, audit adapters, and Personal Memory orchestration.
2. CockroachDB is a required remote service for the constrained host. There is
   no automatic local fallback and no production HA claim.
3. Application, Commit Helper, audit, and review pools are bounded at 4/1/1/1
   and retain separate credential purposes.
4. Hosted generation remains remote; no local generation model is introduced.
5. Exact Step 19 E5 is loaded lazily, once, with batch 8, one intra-op/OMP/MKL
   thread, tokenizer parallelism off, and verified external cache reuse.
6. Critic is optional and disabled by default. Ingestion is disabled after
   prepared-corpus readiness. Review is request-driven; audit remains required.
7. Provider, embedding, Critic, ingestion, review, audit, and export queues are
   bounded at 2/4/1/1/4/16/1; blocking executor threads are capped at four.
8. Derived external cache is capped at 2,048 MiB and in-memory cache at 64 MiB.
   Cache remains non-authoritative and rebuildable.
9. A typed resource guard suppresses optional work and backpressures heavy work
   before failing a required request closed, with every authority flag false.
10. Typed readiness/liveness exposes Critic disabled, E5 unloaded, ingestion
    disabled, and request-driven review as intentional states without running
    expensive semantic probes.
11. A fixed-scenario process-tree measurement helper and controlled validation
    runner record bounded numeric metrics and hashes only.

## Controlled resource proof

Evidence:
`docs/evidence/performance/step40-4gb-resource-validation.json`

| Evidence field | Observation |
|---|---|
| Status | `PASS_4GB_CONTROLLED` |
| Closure eligible | `true` |
| Canonical validation digest | `22d49082321b80ceb91eb94aaaf305fb0c20566b863f4803a69e2ff1b7ce5017` |
| File SHA-256 | `17b58c819190f4d4e096ac446989ed9b9f6753d1ee4dbfc3dcf5e00682c6142f` |
| Detected host | 3,751 MiB |
| Largest non-embedding core scenario | 78 MiB |
| E5 peak | 706 MiB |
| Conservative core peak | 784 MiB |
| Configured peak | 3,000 MiB |
| Safety margin | 2,216 MiB |
| E5 instances | 1 |
| E5 batch/dimension | 8 / 384 |
| Critic-disabled core | PASS |
| Critic-enabled conformance | PASS; optional |
| German Law core | PASS |
| Personal Memory | PASS |
| Audit chain | verified through Step 38 authority anchor |
| Pressure backpressure | PASS; zero factory calls/partial/duplicate effects |
| Owner/tenant isolation | true / true |
| Secret leakage | 0 |
| Production resources touched | 0 |
| Step 41 started | false |

The conservative formula adds the complete E5 peak to the largest separate
core scenario and therefore deliberately double-counts some process overhead.

## Correctness, security, and reliability boundaries

- The exact Step 38 live evidence remains the source for approved provider,
  same-database Personal Memory, audit-chain, active-patch, and owner/tenant
  authority proof. Step 40 did not rewrite it.
- Provider-free Step 38 tests preserve route, evidence, temporal, correction,
  verifier, and Verified Answer semantics under the new profile.
- Step 39 disabled/enabled and provider-failure paths preserve Critic
  optionality and zero authority.
- The pressure test performs no model factory call, persistence write, partial
  state, duplicate side effect, approval, commit, or activation.
- Step 36 capabilities and separate Commit Helper purpose remain unchanged.
- Step 35 UI/auth/browser-secret boundaries and Step 33 audit are not disabled
  or replaced.
- No production database, provider, AWS, S3, secret rotation, or external
  action was used by the controlled resource run.

## Validation ledger

| Gate | Result |
|---|---|
| Profile/resource/optional focused tests | 37/37 PASS in 0.266 s |
| Measurement helper fixed scenarios | PASS |
| Controlled Step 40 resource validation | PASS |
| Evidence canonical digest and secret/path scan | PASS |
| Step 9/15-34 checkpoint suite after Step 40 frontier update | 437/437 PASS in 28.494 s |
| Steps 5/6/30/31/33-39 focused regression bundle | 535/535 PASS in 66.719 s |
| Step 38 focused regression | PASS within the 535-test bundle |
| Step 39 focused regression | PASS within the 535-test bundle |
| Step 37 reliability spot check | PASS within the 535-test bundle |
| Step 36 credential spot check | PASS within the 535-test bundle |
| Step 35 UI tests | PASS within the 535-test bundle |
| Step 35 asset build/security check | PASS, asset SHA-256 `22283ef68cb7545914f0a88a1bdedc7256a703d1d580c1d255217d0a50d31313` |
| Step 33 audit | PASS within the 535-test bundle |
| Step 31 active retrieval | PASS within the 535-test bundle |
| Step 30 approval/commit/activation | PASS within the 535-test bundle |
| Step 5 RLS and Step 6 persistence | PASS within the 535-test bundle |
| Contract validator | PASS: 5 schemas, 4 fixture files, 2 unrelated HAT manifests and public authority/state invariants |
| Compileall | PASS for `src`, `scripts`, and `tests` |
| Final full unittest discovery | 2,149/2,149 PASS in 141.345 s; wall 146.21 s; max runner RSS 121,912 KiB |
| `git diff --check` | PASS |

## File inventory

The intended Step 40 write set consists of:

- versioned runtime profile;
- resource profile, guard, lazy embedding, and health modules;
- measurement and controlled validation scripts;
- three focused Step 40 test modules;
- canonical performance evidence;
- runtime inventory and resource budget;
- architecture, ADR-047, operations, and this closure record;
- only the mechanically necessary roadmap, AGENTS, and historical checkpoint
  assertion updates after all validation passes.

No Step 38 or Step 39 historical evidence is edited. No Step 41 source, test,
evidence, or deployment file is permitted.

## Known limitations and retained boundaries

- Remote database process memory and live connection count were not observed;
  the local configured pool ceiling is seven and production DB was untouched.
- Local CockroachDB plus E5 is not a validated 4 GB topology.
- E5 first load is slow on the measured two-CPU host; no SLA is claimed.
- Real Critic provider validation remains `UNAVAILABLE_NOT_REQUIRED`.
- The profile is not production HA, AWS deployment, RC freeze, or
  backup/restore proof.
- Step 41 owns the full security and regression campaign.

## Git closure

| Field | Pre-commit value |
|---|---|
| Final Step 40 commit SHA | `NOT CREATED` |
| Commit subject | `perf(runtime): optimize Memory Patch for 4 GB profile 1a` |
| Push | `NOT PERFORMED` |
| Final divergence | `NOT CHECKED` |
| Final worktree | `NOT CHECKED` |

The final report may supply the actual reachable commit after push. This
record does not preclaim it.

`Step 41: NOT STARTED`. Step 40 completion does not authorize Step 41.
