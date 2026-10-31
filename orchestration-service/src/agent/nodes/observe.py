"""observe: summarise the latest rows and decide next / retry / done."""

from __future__ import annotations

from shared.contracts import EventType

from agent.deps import AgentDeps
from agent.emitter import add_usage
from agent.prompts import OBSERVE_SYSTEM, as_untrusted
from agent.schemas import ObserveOutput
from agent.state import AgentState


def make_observe(deps: AgentDeps):
    """Build the `observe` node."""

    async def observe(state: AgentState) -> dict:
        ev = deps.emitter("observe", state)
        index = state.get("current_index", 0)
        await ev.emit(EventType.NODE_ENTER, f"looking at the results of sub-question {index + 1}")
        failed = (not state.get("cypher_ok", True)) or bool(state.get("exec_error"))
        update: dict = {}
        if failed:
            # Retries for this sub-question are exhausted (routing sent us here): record and move on.
            decision = "next"
            note = f"Sub-question could not be answered: {state['sub_questions'][index]} ({state.get('cypher_feedback')})"
            update["notes"] = [*state.get("notes", []), note]
            await ev.emit(EventType.REASONING, note)
        else:
            latest = state["evidence"][-1]
            remaining = state["sub_questions"][index + 1:]
            user = (f"Question: {state['question']}\nSub-question: {latest['sub_question']}\n"
                    f"Cypher: {latest['cypher']}\nRow count: {latest['row_count']}\n"
                    f"Rows: {as_untrusted(latest['rows'][:20])}\nRemaining sub-questions: {remaining}")
            out, usage = await deps.llm.structured(OBSERVE_SYSTEM, user, ObserveOutput)
            await ev.emit(EventType.LLM_CALL, "observe", model=deps.llm.model_name,
                          input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
            await ev.emit(EventType.REASONING, f"{out.summary} → {out.decision}")
            decision = out.decision
            update["tokens"] = add_usage(state.get("tokens"), usage)
            if decision == "retry" and state.get("cypher_attempts", 0) < deps.settings.max_cypher_retries:
                update.update(cypher_feedback=f"previous query returned unhelpful rows: {out.retry_hint}",
                              cypher_attempts=state.get("cypher_attempts", 0) + 1)
            elif decision == "retry":
                decision = "next"  # out of retries for this sub-question
        if decision == "next":
            update.update(current_index=index + 1, cypher_attempts=0, cypher_feedback="", exec_error="")
        await ev.emit(EventType.NODE_EXIT, f"decision: {decision}")
        return {**update, "observe_decision": decision, "step": ev.step}

    return observe
