#!/usr/bin/env python3
"""
AcquisitionRuntime — the provider-independent execution loop.

Owns, for ANY actor:
    job identity (RunContext) · pagination/checkpoint state (PaginationState)
    retry policy (budgets in RunOptions → PaginationState)
    incremental persistence (JsonlDataset, fsync per batch)
    durability ordering (dataset write BEFORE checkpoint commit)
    metrics (AcquisitionMetrics) · termination reason + outcome classification
    resume (checkpoint restore + dataset id replay)

The loop never imports provider code. Adding a second platform must not
require touching this file.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from src.runtime.context import RunContext, utc_now
from src.runtime.checkpoint import CheckpointStore, CheckpointCorrupt
from src.runtime.dataset import JsonlDataset
from src.runtime.actor import AcquisitionActor, PageResult
from src.runtime.state import PaginationState
from src.runtime.termination import classify_termination, lifecycle_for_outcome, RunLifecycle


@dataclass
class RunOptions:
    """User/configured run budget. Retry budgets are handed to PaginationState."""
    max_items: int = 2000
    max_pages: int = 10_000
    resume: bool = False
    max_retries: int = 3
    max_empty_retries: int = 5
    max_stalls: int = 3
    max_parse_retries: int = 3


@dataclass
class RunSummary:
    """Final provenance record for one run invocation.

    actor_id/actor_version/lifecycle_state are additive provenance fields
    (actor/harness contract): legacy actors without an identity record empty
    strings; lifecycle_state is the terminal RunLifecycle classification.
    """
    run_id: str = ""
    job_id: str = ""
    provider: str = ""
    resumed: bool = False
    termination_reason: Optional[str] = None
    outcome: str = ""
    items_written: int = 0          # unique items appended during THIS invocation
    items_seen: int = 0             # total unique items in the dataset
    metrics: dict = field(default_factory=dict)
    dataset_path: str = ""
    checkpoint_path: str = ""
    actor_id: str = ""
    actor_version: str = ""
    lifecycle_state: str = ""

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "job_id": self.job_id,
            "provider": self.provider,
            "resumed": self.resumed,
            "termination_reason": self.termination_reason,
            "outcome": self.outcome,
            "items_written": self.items_written,
            "items_seen": self.items_seen,
            "metrics": self.metrics,
            "dataset_path": self.dataset_path,
            "checkpoint_path": self.checkpoint_path,
            "actor_id": self.actor_id,
            "actor_version": self.actor_version,
            "lifecycle_state": self.lifecycle_state,
        }


class AcquisitionRuntime:
    """Drives one acquisition run for any AcquisitionActor."""

    def __init__(self, print_fn=print):
        self._print = print_fn

    # ── public entry point ────────────────────────────────────────────────
    async def run(
        self,
        actor: AcquisitionActor,
        ctx: RunContext,
        options: Optional[RunOptions] = None,
    ) -> RunSummary:
        options = options or RunOptions()
        ckpt = CheckpointStore(ctx.checkpoint_path)
        dataset = JsonlDataset(ctx.dataset_path, id_keys=(actor.id_key,))

        state: Optional[PaginationState] = None
        resumed = False

        if options.resume and ckpt.exists():
            try:
                prev = ckpt.load() or {}
            except CheckpointCorrupt as e:
                # Fail-closed (ENGINEERING_CONSTITUTION.md §3): a corrupt
                # checkpoint is NEVER silently treated as a fresh run —
                # resume must not guess. Report an explicit terminal
                # recovery failure; discarding/fixing the checkpoint file is
                # an explicit human recovery decision, not an automatic
                # fallback.
                self._print(
                    f"[runtime] RECOVERY FAILURE {ctx.job_id}: {e} — "
                    "not resuming and NOT restarting; fix or remove the "
                    "checkpoint explicitly"
                )
                dataset.load_seen()  # read-only replay: honest items_seen
                outcome = classify_termination("checkpoint_corrupt")
                return RunSummary(
                    run_id=ctx.run_id,
                    job_id=ctx.job_id,
                    provider=ctx.provider,
                    resumed=False,
                    termination_reason="checkpoint_corrupt",
                    outcome=outcome,
                    items_written=0,
                    items_seen=len(dataset.seen_ids),
                    metrics={},
                    dataset_path=str(ctx.dataset_path),
                    checkpoint_path=str(ctx.checkpoint_path),
                    actor_id=getattr(actor, "actor_id", ""),
                    actor_version=getattr(actor, "actor_version", ""),
                    lifecycle_state=lifecycle_for_outcome(outcome),
                )
            pag = prev.get("pagination")
            if pag:
                state = PaginationState.from_dict(pag)
                resumed = True
                # Reconcile the persisted dataset BEFORE any early-return path
                # below, so items_seen always reflects what is actually on disk
                # (read-only replay of ids — never mutates the dataset).
                dataset.load_seen()
                # A cap-terminated run may continue when the cap is raised
                # (same semantics as the TikTok collector resume path).
                if (
                    state.termination_reason == "max_comments_reached"
                    and state.items_seen < options.max_items
                ):
                    state.has_more = True
                    state.termination_reason = None
                elif (
                    state.termination_reason == "max_pages_reached"
                    and state.page_index < options.max_pages
                ):
                    state.has_more = True
                    state.termination_reason = None
                if not state.has_more:
                    # Already terminally stopped — nothing to re-run: no provider
                    # call, no dataset write, reason preserved, real item count.
                    self._print(f"[runtime] run {ctx.job_id} already terminated: "
                                f"{state.termination_reason}")
                    return self._summary(ctx, dataset, state, 0, resumed, actor)

        if state is None:
            state = PaginationState(
                cursor=actor.initial_cursor(),
                max_retries=options.max_retries,
                max_empty_retries=options.max_empty_retries,
                max_stalls=options.max_stalls,
                max_parse_retries=options.max_parse_retries,
            )

        if resumed:
            # Dataset ids were already replayed above; just sync state's counter.
            state.record_items(len(dataset.seen_ids))
            self._print(f"[runtime] resume {ctx.job_id}: cursor={state.cursor} "
                        f"seen={state.items_seen} page={state.page_index}")

        started = time.monotonic()
        items_written = 0

        while state.has_more:
            if state.check_cap(options.max_items):
                self._print(f"[runtime] cap {options.max_items} reached — stopping")
                break

            if state.page_index >= options.max_pages:
                state.has_more = False
                if state.termination_reason is None:
                    state.termination_reason = "max_pages_reached"
                break

            # 1. Fetch one page (actor exceptions → classified fetch failure)
            try:
                result = await actor.fetch_page(ctx, state.cursor, state.page_index)
            except Exception as e:  # noqa: BLE001 — provider errors are data
                result = PageResult(
                    error=f"{type(e).__name__}: {e}"[:200],
                    error_type="fetch_failure",
                )

            # 2. Classify failures through the shared retry/termination state
            if result.error:
                state.process_page(error=result.error, error_type=result.error_type)
                self._commit(ckpt, ctx, state, dataset, status="running")
                if not state.has_more:
                    self._print(f"[runtime] terminal failure: {state.termination_reason}")
                    break
                continue  # retry same cursor within budget

            # 3. Dedup items against everything already persisted
            unique, dups = [], 0
            for item in result.items:
                iid = actor.item_id(item)
                if iid and iid in dataset.seen_ids:
                    dups += 1
                    continue
                unique.append(item)

            # 3b. HARD item cap: the run must never persist more than
            # options.max_items UNIQUE items, even when a single page
            # overshoots the remaining budget. Dupes were already excluded
            # above, so they never consume the cap.
            remaining = options.max_items - len(dataset.seen_ids)
            overflow = len(unique) > remaining
            if overflow:
                unique = unique[:max(remaining, 0)]

            # 4. Advance pagination state (stall / empty / completion semantics).
            # NOTE: the GROSS page items are passed (with deduplicated=dups) —
            # identical to the TikTok collector — so a fully-duplicated page
            # with an unchanged cursor registers as a pagination stall, not an
            # empty page. items_seen is corrected right after via record_items.
            state.process_page(
                comments=result.items,
                next_cursor=result.next_cursor,
                has_more=result.has_more if result.has_more is not None else False,
                deduplicated=dups,
            )

            # 5. Persist batch FIRST, checkpoint only AFTER the write succeeded
            if unique:
                items_written += dataset.append(unique)

            state.record_items(len(dataset.seen_ids))
            self._commit(ckpt, ctx, state, dataset, status="running")

            # Cap reached mid-page: stop with the legacy cap reason AFTER the
            # truncated batch is durably persisted and checkpointed. Cap
            # dominates COMPLETION signals (the run was budget-cut even if the
            # provider also said has_more=false); genuine failure reasons
            # (auth/stall/parse) recorded by process_page keep priority.
            if overflow and state.termination_reason in (
                    None, "has_more_false", "finished"):
                state.has_more = False
                state.termination_reason = "max_comments_reached"
                self._print(f"[runtime] cap {options.max_items} reached mid-page — stopping")

            if state.termination_reason:
                self._print(f"[runtime] pagination finished: {state.termination_reason}")
                break

        if state.termination_reason is None:
            state.termination_reason = "finished"

        state.metrics.ended_at = utc_now()
        state.metrics.duration_seconds = round(time.monotonic() - started, 3)
        self._commit(ckpt, ctx, state, dataset, status="done")

        return self._summary(ctx, dataset, state, items_written, resumed, actor)

    # ── internals ─────────────────────────────────────────────────────────
    def _commit(
        self,
        ckpt: CheckpointStore,
        ctx: RunContext,
        state: PaginationState,
        dataset: JsonlDataset,
        status: str,
    ) -> None:
        ckpt.save({
            "run_id": ctx.run_id,
            "job_id": ctx.job_id,
            "provider": ctx.provider,
            "target": ctx.target,
            "started_at": ctx.started_at,
            "status": status,
            # additive lifecycle provenance: "started" while executing, the
            # terminal RunLifecycle state on the final commit. Legacy readers
            # ignore unknown keys; legacy values ("running"/"done") unchanged.
            "lifecycle": (
                RunLifecycle.STARTED if status == "running"
                else lifecycle_for_outcome(classify_termination(state.termination_reason))
            ),
            "updated_at": utc_now(),
            "items_seen": len(dataset.seen_ids),
            "dataset_path": str(ctx.dataset_path),
            "checkpoint_path": str(ctx.checkpoint_path),
            "pagination": state.to_dict(),
            "metrics": state.metrics.to_dict(),
        })

    def _summary(
        self,
        ctx: RunContext,
        dataset: JsonlDataset,
        state: PaginationState,
        items_written: int,
        resumed: bool,
        actor: Optional[AcquisitionActor] = None,
    ) -> RunSummary:
        reason = state.termination_reason or "finished"
        outcome = classify_termination(reason)
        return RunSummary(
            run_id=ctx.run_id,
            job_id=ctx.job_id,
            provider=ctx.provider,
            resumed=resumed,
            termination_reason=reason,
            outcome=outcome,
            items_written=items_written,
            items_seen=len(dataset.seen_ids),
            metrics=state.metrics.to_dict(),
            dataset_path=str(ctx.dataset_path),
            checkpoint_path=str(ctx.checkpoint_path),
            actor_id=getattr(actor, "actor_id", ""),
            actor_version=getattr(actor, "actor_version", ""),
            lifecycle_state=lifecycle_for_outcome(outcome),
        )
