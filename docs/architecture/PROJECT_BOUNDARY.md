# Project Boundary

This repository contains the standalone AIOA Knowledge Kernel and Memory Patch. It is not the complete AIOA product.

The kernel defines reusable contracts for persistent memory, knowledge ingestion, retrieval, provenance, and Knowledge HAT integration. The AOIA Critic Prompt Loop remains a separate AIOA layer. This repository contains only its optional Step 39 production bridge: validated Critic output may enter Step 28 as untrusted candidate data, with no route, source, canonical-evidence, reviewer, approval, commit, activation, or execution authority.

German Federal Law will be the first Knowledge HAT. Future Linux, Python, and other domain HATs are expected to use the same kernel contracts without becoming part of the kernel itself.

CockroachDB is planned to provide:

- persistent memory;
- vector retrieval;
- transactional state;
- audit records.

AWS is planned to provide runtime infrastructure in a later phase.

This bootstrap creates no AWS or CockroachDB Cloud resources, performs no cloud authentication, configures no Managed MCP server, and starts no local database server. Planned architecture is not a claim that the future implementation already exists.
