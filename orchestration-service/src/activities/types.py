"""Small dataclasses passed between IngestionWorkflow and its activities (Temporal-serialisable)."""

from dataclasses import dataclass


@dataclass
class BatchInput:
    """Identifies one batch of one corpus version."""

    corpus_version: str
    index: int


@dataclass
class StageResult:
    """What a stage/activity reports back to the workflow (kept tiny: history stores it)."""

    items: int = 0
    batches: int = 0
    cached: int = 0
    skipped: int = 0
    detail: str = ""


@dataclass
class EventInput:
    """A stage-level progress event the workflow wants published."""

    run_id: str
    type: str
    content: str
    meta: str = ""
