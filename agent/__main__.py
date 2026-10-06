"""Run once and emit a self-contained JSON diagnosis with evidence citations."""

import argparse
import asyncio
import json
from pathlib import Path
import sys

from agent.diagnosis import DiagnosisError, OpenRouterDiagnoser
from agent.graph import build_graph, collect_evidence
from agent.mcp_client import connect_tools


async def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Also save the JSON report to this path")
    parser.add_argument("--collect-only", action="store_true", help="Inspect evidence locally without calling OpenRouter")
    arguments = parser.parse_args()
    try:
        diagnoser = None if arguments.collect_only else OpenRouterDiagnoser.from_env()
        async with connect_tools() as tools:
            if arguments.collect_only:
                evidence = await collect_evidence(tools)
                encoded = json.dumps({"stages": ["COLLECT"], "evidence": [e.model_dump(mode="json") for e in evidence]}, indent=2)
                result_code = 2 if any(e.error for e in evidence) else 0
            else:
                state = await build_graph(tools, diagnoser).ainvoke({})
                report = state["report"]
                encoded = report.model_dump_json(indent=2)
                result_code = 2 if report.diagnosis.status == "inconclusive" else 0
        if arguments.output:
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(encoded + "\n", encoding="utf-8")
        print(encoded)
        return result_code
    except DiagnosisError as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception:
        print("Diagnosis could not start. Check OPENROUTER_API_KEY in .env, agent dependencies, and scripts/setup-diagnosis.ps1.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
