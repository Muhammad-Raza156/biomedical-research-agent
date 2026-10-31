"""Start a ResearchWorkflow. Usage: python scripts/start_query.py "question" [query_id]  → prints query_id.

Passing your own query_id lets you arm chaos_kill.py on thinking:<query_id> *before* the run starts.
"""

import asyncio
import sys
import uuid

from shared.contracts import (
    RESEARCH_WORKFLOW,
    TASK_QUEUE_RESEARCH,
    ResearchInput,
    research_workflow_id,
)

from bootstrap import connect_temporal


async def main() -> None:
    client = await connect_temporal("localhost:7233", "default")
    query_id = sys.argv[2] if len(sys.argv) > 2 else uuid.uuid4().hex[:12]
    await client.start_workflow(RESEARCH_WORKFLOW, ResearchInput(query_id=query_id, question=sys.argv[1]),
                                id=research_workflow_id(query_id), task_queue=TASK_QUEUE_RESEARCH)
    print(query_id)


asyncio.run(main())
