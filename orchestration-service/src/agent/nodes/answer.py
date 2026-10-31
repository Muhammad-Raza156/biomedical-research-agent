"""synthesize: draft claims from evidence. verify: re-check every claim; assemble the final answer."""

from __future__ import annotations

import json

from shared.contracts import EventType

from agent.deps import AgentDeps
from agent.emitter import add_usage
from agent.prompts import SYNTH_SYSTEM, as_untrusted
from agent.schemas import ClaimDraft, FinalAnswer, SynthOutput, VerifiedClaim
from agent.state import AgentState


def make_synthesize(deps: AgentDeps):
    """Build the `synthesize` node."""

    async def synthesize(state: AgentState) -> dict:
        ev = deps.emitter("synthesize", state)
        await ev.emit(EventType.NODE_ENTER, "drafting claims from the evidence")
        evidence = [{"evidence_index": e["index"], "sub_question": e["sub_question"], "rows": e["rows"]}
                    for e in state.get("evidence", [])]
        user = (f"Question: {state['question']}\n"
                f"Resolved entities: {json.dumps(state.get('entities', {}))}\n"
                f"Not in corpus: {state.get('unresolved', [])}\n"
                f"Definitions to use: {state.get('definitions', [])}\n"
                f"Notes: {state.get('notes', [])}\n"
                f"Verifier feedback on your previous draft: {state.get('verifier_feedback') or 'none'}\n"
                f"Evidence: {as_untrusted(evidence)}")
        out, usage = await deps.llm.structured(SYNTH_SYSTEM, user, SynthOutput)
        await ev.emit(EventType.LLM_CALL, "synthesize", model=deps.llm.model_name,
                      input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
        await ev.emit(EventType.REASONING, f"{len(out.claims)} claims drafted")
        await ev.emit(EventType.NODE_EXIT, "draft ready")
        return {"draft": out.model_dump(), "tokens": add_usage(state.get("tokens"), usage),
                "iterations": state.get("iterations", 0) + 1, "step": ev.step}

    return synthesize


async def _check_claim(deps: AgentDeps, claim: ClaimDraft, evidence: list[dict],
                       status: dict[str, dict]) -> tuple[list[str], list[str], list[str]]:
    """Return (failure reasons, warnings, verification queries) for one claim."""
    reasons, warnings, queries = [], [], []
    if not claim.pmids:
        reasons.append("claim cites no PMID")
    item = evidence[claim.evidence_index] if 0 <= claim.evidence_index < len(evidence) else None
    if item is None:
        reasons.append(f"evidence_index {claim.evidence_index} does not exist")
    rows_text = json.dumps(item["rows"], default=str) if item else ""
    for pmid in claim.pmids:
        info = status.get(pmid)
        if not info or not info["in_corpus"]:
            reasons.append(f"PMID {pmid} is not in the corpus")
            continue
        if f'"{pmid}"' not in rows_text and f": {pmid}," not in rows_text:
            reasons.append(f"PMID {pmid} does not appear in the cited evidence rows")
        if info["retracted"]:
            warnings.append(f"PMID {pmid} is retracted")
    for assertion in claim.assertions:
        holds, cypher = await deps.graph.check_assertion(assertion)
        queries.append(cypher)
        if not holds:
            reasons.append(f"assertion failed: {assertion.model_dump(exclude_none=True)}")
    return reasons, warnings, queries


def _final(state: AgentState, deps: AgentDeps, verified: list[VerifiedClaim], checked: int,
           no_answer_reason: str, extra_notes: list[str]) -> dict:
    tokens = state.get("tokens", {"input": 0, "output": 0})
    s = deps.settings
    notes = [*state.get("notes", []), *extra_notes, *(f"Definition: {d}" for d in state.get("definitions", []))]
    if verified:
        answer = "\n".join(f"- {c.text} (PMID {', '.join(c.pmids)})"
                           + (f" [warning: {'; '.join(c.warnings)}]" if c.warnings else "") for c in verified)
    else:
        answer = ("The knowledge graph does not contain verified information to answer this question."
                  + (f" {no_answer_reason}" if no_answer_reason else ""))
    final = FinalAnswer(
        answer=answer, claims=verified, claims_checked=checked, claims_passed=len(verified),
        claims_dropped=checked - len(verified), notes=notes,
        input_tokens=tokens["input"], output_tokens=tokens["output"], total_tokens=tokens["input"] + tokens["output"],
        estimated_cost_usd=round(tokens["input"] / 1e6 * s.price_input_per_mtok
                                 + tokens["output"] / 1e6 * s.price_output_per_mtok, 6))
    return final.model_dump()


def make_verify(deps: AgentDeps):
    """Build the `verify` node."""

    async def verify(state: AgentState) -> dict:
        ev = deps.emitter("verify", state)
        draft = SynthOutput.model_validate(state["draft"])
        evidence = state.get("evidence", [])
        await ev.emit(EventType.NODE_ENTER, f"verifying {len(draft.claims)} claims against the graph")
        status = await deps.graph.paper_status(sorted({p for c in draft.claims for p in c.pmids}))
        verified, failures = [], []
        for i, claim in enumerate(draft.claims):
            reasons, warnings, queries = await _check_claim(deps, claim, evidence, status)
            passed = not reasons
            await ev.emit(EventType.VERIFICATION, f"claim {i + 1} {'passed' if passed else 'failed'}: {claim.text}",
                          passed=passed, reasons=reasons, pmids=claim.pmids)
            if passed:
                cypher = evidence[claim.evidence_index]["cypher"]
                verified.append(VerifiedClaim(text=claim.text, pmids=claim.pmids, cypher=cypher,
                                              verification_queries=queries, warnings=warnings))
            else:
                failures.append(f"claim {i + 1} ('{claim.text}'): {'; '.join(reasons)}")
        s = deps.settings
        can_revise = (failures and state.get("verifier_rejections", 0) < s.max_verifier_rejections
                      and state.get("iterations", 0) < s.max_iterations)
        if can_revise:
            await ev.emit(EventType.REASONING, f"{len(failures)} claims failed; sending back to synthesize")
            await ev.emit(EventType.NODE_EXIT, "revision requested")
            return {"verifier_feedback": "\n".join(failures),
                    "verifier_rejections": state.get("verifier_rejections", 0) + 1, "step": ev.step}
        extra = []
        if state.get("iterations", 0) >= s.max_iterations:
            extra.append(f"Stopped after the iteration limit ({s.max_iterations}); the answer may be incomplete.")
        final = _final(state, deps, verified, len(draft.claims), draft.no_answer_reason, extra)
        await ev.emit(EventType.REASONING,
                      f"verification complete: {final['claims_checked']} checked, "
                      f"{final['claims_passed']} passed, {final['claims_dropped']} dropped")
        await ev.emit(EventType.NODE_EXIT, "answer ready")
        return {"final": final, "step": ev.step}

    return verify
