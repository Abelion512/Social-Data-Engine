# Acceptable Use Policy (engineering policy — not a legal contract)

What this platform may be used for, and what its architecture must never
optimize for. No legal advice; users remain responsible for obeying the rules
of platforms they access and applicable law.

## Legitimate use

- Research and engineering on publicly accessible social data the operator is
  authorized to access, with bounded collection volumes.
- Reproducible dataset construction for analysis/ML with provenance.

## CURRENTLY PARTIALLY ENFORCED

- **Bounded collection** — caps exist at the runtime level
  (`max_items` hard cap, page/retry budgets). CLI-level caps depend on flags.
- **Respect provider/platform rules** — enforced only by process: auth-block
  termination stops runs when a platform challenges the session. There is no
  rate-adaptation layer.

## Honest disclosure of current behavior

The existing TikTok acquisition stack uses an anti-detect browser
(Camoufox), fingerprint-spoofing stealth scripts, and automated captcha
handling (`src/harness/human.py`) to keep a logged-in session working.
**This exists in the codebase today and this policy does not pretend
otherwise.** It is tolerated only as session-persistence plumbing for reading
content an authenticated user can already see — it must not be extended into
new access-control evasion.

## Architectural position going forward

- Evasion of access controls is **NOT an architectural objective**. Future
  capability declarations treat evasion-class behaviors as restricted,
  `REQUIRE_APPROVAL` capabilities — not default grants.
- No abuse-oriented automation: no scraping-for-harassment tooling, no bulk
  account creation, no spam or engagement manipulation features will be built.
- New providers/integrations must declare their access mode honestly
  (public / authenticated / API) in their actor registration.

## PLANNED

- Capability gate makes the above mechanically checkable: undeclared or
  denied capability classes fail closed before any action executes.
