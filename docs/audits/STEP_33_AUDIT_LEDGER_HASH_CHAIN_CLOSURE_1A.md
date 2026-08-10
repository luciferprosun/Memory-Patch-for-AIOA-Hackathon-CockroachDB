# Step 33 Audit Ledger and Hash Chain Closure 1A

## Starting point and scope

- Exact Step 32 base: `355a790b50a6412adcf64dd0a463219574a3f849`.
- Step 32 was complete and pushed; Steps 33 and 34 were not started.
- Scope is limited to typed audit normalization, append-only owner chains,
  deterministic verification and bounded redacted audit export.
- Step 34 remains not started. This record does not invent the final Step 33
  Git closure SHA before that commit exists.

## Event inventory and contracts

Step 33 inventories the implemented Kernel route/policy, evidence/temporal,
Draft/claim/correction/answer, Personal Memory slot/candidate/proposal,
approval/commit/activation, lifecycle/shared-review and security/integrity
facts. Closed event, actor, subject and reason registries reject arbitrary
model/user strings.

`AuditEventDraft`, `AuditEventEnvelope`, `AuditAppendReceipt`,
`AuditChainVerificationResult`, `AuditExportRequest` and `AuditExportBundle`
are immutable, reconstructed on verification and bound by existing canonical
JSON/SHA-256 helpers. Payloads contain bounded safe metadata and hashes, not
raw private text or credentials.

Step 26, Step 30 and Step 32 adapters preserve immutable result/receipt
hashes without reimplementing business decisions. Audit describes those
facts and has no approval, commit, activation, revocation, deletion,
publication or execution authority.

## Ledger, partition and persistence decision

The chain partition is exact `(tenant_id, owner_user_id)`, with a separate
nullable-owner tenant-system partition available for non-private facts. The
explicit genesis sentinel is the SHA-256 of the domain string
`MEMORY_PATCH_AUDIT_GENESIS_V1`. Event/payload hashing uses independent
domain strings and repository canonical serialization.

Migration `0016_step33_audit_ledger_hash_chain` extends the existing Step 4
`audit_events` table rather than creating a competing audit store and adds a
bounded `audit_chain_heads` accelerator. Legacy audit rows remain outside the
Step 33 chain and are not misrepresented as chained history.

The migration SHA-256 is
`5c803f8bc81407aa558bfbed0c7bb862f24f918af07ea732c37b909896075a7c`.

Audit-event rows are database append-only: runtime has SELECT/INSERT only and
an independent trigger rejects UPDATE/DELETE. Head UPDATE is guarded by an
exact one-step transition and a just-inserted matching event. Both tables use
RLS and FORCE RLS; runtime has no BYPASSRLS.

## Concurrency, replay and verification

A short Step 6 serializable transaction resolves replay, locks one chain
head, rechecks replay after waiting, inserts the next event and advances the
head. Unique chain sequence and idempotency keys prevent duplicate positions
and changed replay. The controlled concurrency proof uses its own sanitized
owner chain so thread scheduling cannot change the representative export
proof. Appending is O(1) against history.

The verifier checks scope, genesis/range anchor, contiguous sequence, event
identity, predecessor, reconstructed event/payload hashes, append receipt and
head consistency. It returns typed failure reasons and never repairs data.
The controlled matrix detects payload/type/subject/predecessor tampering,
middle deletion, reordering, forged event, duplicate sequence and head
change.

## Export, redaction and isolation

Owner export is exact-tenant/exact-owner, ordered by chain/sequence, bounded
by event and byte ceilings and carries a predecessor anchor plus verification
proof. Hash-only redaction omits payload representation while retaining the
original bound digest and explicit marker. No raw Personal Memory text,
secret, authorization value or machine path is exported.

Service authentication binds tenant as well as owner; database RLS separately
proves same-tenant cross-user and cross-tenant invisibility and insert denial.
There is no unrestricted all-tenant export or Step 34 reviewer capability.

## Validation and known limitations

The final evidence file is
`docs/evidence/audit/step33-audit-ledger-validation.json`. It records the
clean disposable CockroachDB run, migration replay, live catalog, real
Step 30/32 receipt-hash fixtures, concurrent append, tamper matrix, owner
export/redaction, isolation and exact cleanup.

The complete pre-commit gate passed:

- Step 33 ledger/export tests: 42 tests, failures 0, errors 0;
- full repository discovery: 1,814 tests, failures 0, errors 0;
- focused Step 17/20--32, CockroachDB RLS/persistence, authority,
  tenant-boundary and serialization regressions: 659 tests, failures 0,
  errors 0;
- Python compile, offline migration validation, contract validation and
  diff whitespace validation: PASS;
- controlled disposable CockroachDB validation: PASS, digest
  `ac215cc82e31a005b6387ab1f91a4d332f4d01cf1ec0b383e06d1ab5b358142a`;
- controlled cleanup removed the database, role and temporary store, observed
  the process exit and closed ports without a forced kill.

Known limitations are explicit:

- earlier non-chain Step 4 audit rows are retained as legacy history;
- V1 export handles one exact owner chain and rejects sparse event filters;
- audit append is not claimed to be atomically distributed with already
  completed historical business transactions;
- the normal database role is a trusted repository boundary rather than an
  end-user/model credential; production credential hardening remains Step 36;
- the SHA-256 chain is not a signature or external notarization, so Step 33
  does not claim detection after privileged replacement of all rows and local
  anchors; no S3 mutation/external mirror is performed in this closure;
- Step 34 review workspace/reviewer authorization and Step 35 Personal
  Memory UI are absent.

`Step 34: NOT STARTED`.
