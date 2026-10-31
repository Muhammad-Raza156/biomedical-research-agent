"""Replay committed histories through the current workflow code (catches non-determinism)."""

from pathlib import Path

import pytest
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from workflows.ingestion import IngestionWorkflow
from workflows.research import ResearchWorkflow

HISTORIES = sorted((Path(__file__).parent / "histories").glob("*.json"))


@pytest.mark.parametrize("path", HISTORIES, ids=[p.stem for p in HISTORIES])
async def test_history_replays_without_nondeterminism(path: Path):
    # The replayer doesn't check the workflow ID; the file name is a readable label in failures.
    history = WorkflowHistory.from_json(path.stem, path.read_text(encoding="utf-8"))
    replayer = Replayer(workflows=[IngestionWorkflow, ResearchWorkflow])
    await replayer.replay_workflow(history)


def test_histories_exist():
    names = {p.stem for p in HISTORIES}
    assert any(n.startswith("ingestion") for n in names) and any(n.startswith("research") for n in names)
