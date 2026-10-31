"""plan: split the question and resolve entities against the graph. clarify: ask the user."""

from __future__ import annotations

from langgraph.types import interrupt
from shared.contracts import CLARIFY_TIMEOUT_CHOICE, EventType

from agent.deps import AgentDeps
from agent.emitter import add_usage
from agent.prompts import PLAN_SYSTEM
from agent.schemas import PlanOutput
from agent.state import AgentState


async def _resolve(deps: AgentDeps, kind: str, text: str) -> list[dict]:
    if kind == "author":
        return await deps.graph.resolve_author(text)
    if kind == "mesh":
        return await deps.graph.resolve_mesh(text)
    found = await deps.graph.resolve_pmid(text.strip())
    return [found] if found else []


def make_plan(deps: AgentDeps):
    """Build the `plan` node."""

    async def plan(state: AgentState) -> dict:
        ev = deps.emitter("plan", state)
        await ev.emit(EventType.NODE_ENTER, "planning sub-questions and resolving entities")
        update: dict = {}
        if not state.get("sub_questions"):  # first visit (not a return from clarify)
            out, usage = await deps.llm.structured(PLAN_SYSTEM, f"Question: {state['question']}", PlanOutput)
            await ev.emit(EventType.LLM_CALL, "plan", model=deps.llm.model_name,
                          input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
            await ev.emit(EventType.REASONING, "sub-questions: " + " | ".join(out.sub_questions))
            update.update(sub_questions=out.sub_questions, definitions=out.definitions,
                          mentions=[m.model_dump() for m in out.entities],
                          tokens=add_usage(state.get("tokens"), usage))
        mentions = update.get("mentions", state.get("mentions", []))
        entities = dict(state.get("entities", {}))
        unresolved = list(state.get("unresolved", []))
        pending = None
        for mention in mentions:
            text, kind = mention["text"], mention["kind"]
            if text in entities or text in unresolved:
                continue
            candidates = await _resolve(deps, kind, text)
            exact = [c for c in candidates if c["label"].lower() == text.lower()]
            if len(exact) == 1 or len(candidates) == 1:
                entities[text] = {"kind": kind, **(exact or candidates)[0]}
                await ev.emit(EventType.REASONING, f"resolved {kind} '{text}' → {entities[text]['label']}",
                              id=entities[text]["id"])
            elif not candidates:
                unresolved.append(text)
                await ev.emit(EventType.REASONING, f"{kind} '{text}' is not in the corpus")
            else:
                pending = {"mention": text, "kind": kind, "options": candidates[:5]}
                await ev.emit(EventType.REASONING, f"{kind} '{text}' is ambiguous: {len(candidates)} matches")
                break
        await ev.emit(EventType.NODE_EXIT, "plan done")
        return {**update, "entities": entities, "unresolved": unresolved,
                "pending_clarification": pending, "step": ev.step}

    return plan


def make_clarify(deps: AgentDeps):
    """Build the `clarify` node: pause with interrupt(), resume with the user's choice."""

    async def clarify(state: AgentState) -> dict:
        ev = deps.emitter("clarify", state)
        pending = state["pending_clarification"]
        options = pending["options"]
        await ev.emit(EventType.NODE_ENTER, f"asking the user which '{pending['mention']}' they mean")
        await ev.emit(EventType.CLARIFICATION_NEEDED,
                      f"'{pending['mention']}' matches {len(options)} {pending['kind']}s; please choose one",
                      mention=pending["mention"], options=options)
        choice = interrupt({"mention": pending["mention"], "kind": pending["kind"], "options": options})
        notes = list(state.get("notes", []))
        chosen = next((o for o in options if choice in (o["id"], o["label"])), None)
        if chosen is None:
            chosen = options[0]  # options are sorted by paper count: most likely first
            why = "no answer within the time limit" if choice == CLARIFY_TIMEOUT_CHOICE else f"unknown choice '{choice}'"
            notes.append(f"Clarification: {why}; proceeded with the most likely match for "
                         f"'{pending['mention']}': {chosen['label']} ({chosen['id']}).")
        entities = {**state.get("entities", {}), pending["mention"]: {"kind": pending["kind"], **chosen}}
        await ev.emit(EventType.REASONING, f"using {chosen['label']} for '{pending['mention']}'", id=chosen["id"])
        await ev.emit(EventType.NODE_EXIT, "clarified")
        return {"entities": entities, "pending_clarification": None, "notes": notes, "step": ev.step}

    return clarify
