# ADR-003: Bind Personal Memory to Users, Not Models

- Status: Accepted
- Date: 2026-07-25

## Context

Model providers and versions change. Treating model identity as ownership would
strand or transfer user memory when a binding changes.

## Decision

Every personal object is addressed by tenant, user, and personal-memory-space
ID. Model bindings are replaceable references and cannot alter ownership,
visibility, or trust.

## Consequences

The same memory can be used by two authorized model bindings and can survive a
base-model change. Cross-user and cross-tenant operations fail before storage
access. Export and deletion remain user-scoped.
