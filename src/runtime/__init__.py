#!/usr/bin/env python3
"""
Acquisition Runtime — provider-independent execution core ("mini Apify runtime").

Boundary:
    Actor/Provider  →  RunContext  →  AcquisitionRuntime
                                        ├── job identity        (RunContext)
                                        ├── pagination/checkpoint (PaginationState + CheckpointStore)
                                        ├── retry policy         (PaginationState budgets)
                                        ├── incremental persistence (JsonlDataset)
                                        ├── metrics              (AcquisitionMetrics)
                                        ├── termination reason   (TerminationReason/Outcome)
                                        └── resume support       (checkpoint + dataset replay)

The runtime NEVER imports provider code. A future provider implements
`AcquisitionActor` only; the runtime supplies everything else.
"""
from src.runtime.context import RunContext, utc_now
from src.runtime.termination import TerminationReason, Outcome, classify_termination
from src.runtime.metrics import AcquisitionMetrics
from src.runtime.state import PaginationState, PaginationDiagnostic
from src.runtime.checkpoint import CheckpointStore, CheckpointCorrupt
from src.runtime.dataset import JsonlDataset, append_records, load_seen_ids
from src.runtime.actor import AcquisitionActor, PageResult
from src.runtime.engine import AcquisitionRuntime, RunOptions, RunSummary

__all__ = [
    "RunContext",
    "utc_now",
    "TerminationReason",
    "Outcome",
    "classify_termination",
    "AcquisitionMetrics",
    "PaginationState",
    "PaginationDiagnostic",
    "CheckpointStore",
    "CheckpointCorrupt",
    "JsonlDataset",
    "append_records",
    "load_seen_ids",
    "AcquisitionActor",
    "PageResult",
    "AcquisitionRuntime",
    "RunOptions",
    "RunSummary",
]
