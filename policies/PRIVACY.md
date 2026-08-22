# Privacy Policy (engineering policy — not a legal contract)

This is an engineering policy about handling captured social data. It makes
no regulatory-compliance claims (no GDPR/CCPA/FERPA assertions).

## CURRENTLY ENFORCED

- **Real scraped corpus stays outside Git.** `data/raw`, `data/curated`,
  `data/normalized`, `data/enriched`, `data/rejected`, `data/manifests` are
  all gitignored; only `.gitkeep` placeholders and synthetic fixtures
  (`data/samples/example_tiktok_comments.jsonl`) are tracked.
  (Established in commit f66e8ed after real corpus was accidentally tracked.)
- **Session credentials are not datasets.** Cookies/tokens live in gitignored
  state directories, never inside collected records.

## CURRENTLY PARTIALLY ENFORCED

- **Data minimization** — runs are capped (`--max`, `--scrolls`, `max_items`),
  so collection volume is bounded; but there is no field-level minimization:
  whatever the provider page exposes gets written to raw output.
- **Sensitive identifiers out of logs** — console output may include handles,
  URLs, or comment text today; no redaction layer exists.
- **Retention** — local outputs persist until manually deleted; no automatic
  expiry.

## PLANNED

- Log/event redaction of user identifiers before any export path.
- Explicit retention defaults (e.g., raw tier auto-expiry) with a documented
  deletion workflow.
- Minimization profile: configurable allow-list of fields persisted from raw
  captures.

## Standing rules for contributors

1. Never commit real scraped records, screenshots, or session dumps.
2. New fixtures must be synthetic or irreversibly anonymized.
3. New output fields must be justified against minimization in review.
