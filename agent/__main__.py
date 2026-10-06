"""Run once and emit a self-contained JSON diagnosis with evidence citations."""

import argparse
import asyncio
import json
from pathlib import Path
import sys
from uuid import uuid4

from langgraph.types import Command

from agent.diagnosis import DiagnosisError, OpenRouterDiagnoser
from agent.graph import build_graph, collect_evidence
from agent.mcp_client import connect_tools


async def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Also save the JSON report to this path")
    parser.add_argument("--collect-only", action="store_true", help="Inspect evidence locally without calling OpenRouter")
    parser.add_argument("--repair", action="store_true", help="Propose the fixed repair and prompt for human approval")
    arguments = parser.parse_args()
    if arguments.collect_only and arguments.repair:
        parser.error("--collect-only and --repair cannot be combined")

    def save(encoded):
        if arguments.output:
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(encoded + "\n", encoding="utf-8")

    try:
        diagnoser = None if arguments.collect_only else OpenRouterDiagnoser.from_env()
        async with connect_tools() as tools:
            if arguments.collect_only:
                evidence = await collect_evidence(tools)
                encoded = json.dumps({"stages": ["COLLECT"], "evidence": [e.model_dump(mode="json") for e in evidence]}, indent=2)
                result_code = 2 if any(e.error for e in evidence) else 0
            else:
                graph = build_graph(tools, diagnoser, repair=arguments.repair)
                configuration = {"configurable": {"thread_id": str(uuid4())}}
                state = await graph.ainvoke({}, configuration)
                if state.get("__interrupt__"):
                    pending = state["report"].model_copy(update={
                        "stages": [*state["stages"], "WAIT_FOR_APPROVAL"],
                    })
                    save(pending.model_dump_json(indent=2))
                    print(pending.model_dump_json(indent=2), flush=True)
                    proposal = pending.proposal
                    print(f"\nHuman approval required: type approve {proposal.id} to execute {proposal.action}. "
                          f"{proposal.expected_effect} Expires {proposal.expires_at.isoformat()}.\n"
                          "Anything else denies. Approval: ", end="", flush=True)
                    try:
                        answer = input().strip()
                    except (EOFError, KeyboardInterrupt):
                        answer = "deny"
                    state = await graph.ainvoke(Command(resume=answer), configuration)
                report = state["report"]
                encoded = report.model_dump_json(indent=2)
                result_code = 2 if report.diagnosis.status == "inconclusive" else 0
                if arguments.repair and report.outcome not in {"recovered", "not_needed", "denied"}:
                    result_code = 2
        save(encoded)
        print(encoded)
        return result_code
    except DiagnosisError as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception:
        print("Agent could not start. Check OPENROUTER_API_KEY in .env, agent dependencies, and scripts/setup-diagnosis.ps1.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
