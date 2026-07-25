# ADR-002: Separate Knowledge HATs from Personal Memory HATs

- Status: Accepted
- Date: 2026-07-25

## Context

A domain module and a user's private memory namespace have different code,
ownership, trust, and visibility risks despite sharing the product word “HAT”.

## Decision

A Knowledge HAT is a trusted, system-installed protocol implementation with a
versioned manifest and no action, canonical-write, approval, or commit
authority. A Personal Memory HAT is a `PersonalMemorySpace`: private data with
no executable code or plugin authority.

## Consequences

Shared retrieval cannot see private personal data. Personal spaces cannot load
code, grant capabilities, or become canonical evidence. The UI name may remain
Personal Memory HAT while the internal type makes its data-only nature explicit.
