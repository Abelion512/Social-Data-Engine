# Security Policy (engineering policy — not a legal contract)

No compliance or "enterprise-grade" claims are made here. This document
states only what the code actually does today.

## CURRENTLY ENFORCED

- **No plaintext credentials in code.** Pre-merge gate:
  `grep -rn "LINKEDIN_PASSWORD\|LINKEDIN_USERNAME" src/` must return 0 hits.
  Authentication is cookie/session-based only.
- **Credential state stays out of Git.** `.tiktok-linkedin/`, `*.tokens.json`,
  and `.env*` are gitignored; Claude local credential state is ignored too.
- **Real scraped corpus stays out of Git.** All runtime acquisition output
  under `data/` is gitignored; only synthetic fixtures are tracked.

## CURRENTLY PARTIALLY ENFORCED

- **Secrets never in logs** — no mechanism scrubs tokens/handles from print
  output today; the rule is convention, not enforcement.
- **Explicit filesystem boundaries** — writes are confined in practice to
  `data/` and checkpoint paths by convention; nothing mechanically prevents a
  script from writing elsewhere.
- **Least privilege** — see Constitution §1: documented, not enforced.

## PLANNED

- Capability-gated external network access (`network.fetch` requires an
  explicit grant per actor/run).
- No arbitrary process execution by default — `process.execute` becomes a
  distinct, approvable capability rather than something any harness tool can do.
- Secrets redaction at log/event boundaries.
- Plugin sandboxing / isolation (explicitly out of scope until designed).

## Non-goals

This document does not claim penetration testing, threat-model sign-off,
or regulated-industry compliance.
