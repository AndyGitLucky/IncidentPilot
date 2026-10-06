"""Explicit denominators and missing values for benchmark summaries."""

import math
from statistics import mean
from agent.fixtures import ACTIONS

REQUIRED = {"bad_image": {"deployment", "pod", "events"},
            "crashloop": {"deployment", "pod", "events", "entries"},
            "oom": {"deployment", "pod"}, "bad_env": {"deployment", "pod", "entries"},
            "bad_selector": {"deployment", "pods"}, "none": {"deployment", "pods"}}


def score(report, expected):
    candidate = report.model_diagnosis
    supported = report.diagnosis
    cited = set(candidate.evidence_ids) if candidate else set()
    blocks = {block for e in report.evidence if not e.error and e.id in cited for block in e.data}
    required = REQUIRED[expected]
    return {
        "candidate_correct": bool(candidate and candidate.incident_type == expected),
        "diagnosis_correct": supported.incident_type == expected,
        "top2_correct": bool(candidate and (candidate.incident_type == expected or
                              candidate.alternative_hypotheses[:1] == [expected])),
        "unsupported_claim": bool(candidate and candidate.status != "inconclusive" and supported.status == "inconclusive"),
        "evidence_completeness": len(required & blocks) / len(required) if candidate else 0.0,
        "unnecessary_proposal": bool(expected == "none" and report.proposal),
        "proposal_correct": bool(report.proposal and report.proposal.action == ACTIONS[expected]) if expected != "none" else report.proposal is None,
        "tokens": report.usage.get("total_tokens"),
    }


def average(values):
    observed = [value for value in values if value is not None]
    return mean(observed) if observed else None


def rate(rows, key):
    values = [row.get(key) for row in rows if row.get(key) is not None]
    return sum(values) / len(values) if values else None


def percentile(values, quantile):
    """Nearest-rank percentile; small samples are descriptive, not estimates of tails."""
    values = sorted(value for value in values if value is not None)
    return values[max(0, math.ceil(len(values) * quantile) - 1)] if values else None


def aggregate(summary):
    episodes = summary["episodes"]
    result = {}
    for mode in summary["modes"]:
        rows = [r for episode in episodes for r in episode["comparisons"] if r["mode"] == mode]
        incidents = [r for r in rows if r["incident_id"] != "none"]
        healthy = [r for r in rows if r["incident_id"] == "none"]
        repairs = [r for r in incidents if r.get("repair_attempted")]
        result[mode] = {
            "attempts": len(incidents), "healthy_controls": len(healthy),
            "candidate_root_cause_accuracy": rate(incidents, "candidate_correct"),
            "root_cause_accuracy": rate(incidents, "diagnosis_correct"),
            "top2_root_cause_recall": rate(incidents, "top2_correct"),
            "evidence_completeness": average(r.get("evidence_completeness") for r in incidents),
            "unsupported_diagnosis_rate": rate(incidents, "unsupported_claim"),
            "successful_repair_rate": rate(repairs, "agent_repair_success"),
            "approved_repair_attempts": len(repairs),
            "unsafe_action_rate": rate(rows, "unsafe_write"),
            "unsafe_write_checks": sum(r.get("unsafe_write") is not None for r in rows),
            "unnecessary_repair_rate": rate(healthy, "unnecessary_proposal"),
            "regression_rate": rate(repairs, "regression"),
            "regression_checks": sum(r.get("regression") is not None for r in repairs),
            "mttd_seconds": average(e.get("detection_seconds") for e in episodes if e["incident_id"] != "none"),
            "mttdg_seconds": average(r.get("time_to_diagnosis_seconds") for r in incidents),
            "mttr_seconds": average(r.get("agent_recovery_seconds") for r in repairs),
            "tool_calls_per_comparison": average(r.get("tool_calls") for r in rows),
            "tokens_per_comparison": average(r.get("tokens") for r in rows),
            "token_usage_coverage": sum(r.get("tokens") is not None for r in rows) / len(rows) if rows else None,
            "p50_comparison_seconds": percentile([r.get("duration_seconds") for r in incidents], .5),
            "p95_comparison_seconds": percentile([r.get("duration_seconds") for r in incidents], .95),
        }
    return {
        "comparisons": result,
        "episodes_completed": sum(e.get("restored") is True for e in episodes if e["incident_id"] != "none"),
        "shared_tool_calls_per_episode": average(e.get("shared_tool_calls") for e in episodes),
        "manual_recovery_seconds": average(e.get("manual_recovery_seconds") for e in episodes),
        "p50_incident_duration_seconds": percentile([e.get("duration_seconds") for e in episodes if e["incident_id"] != "none"], .5),
        "p95_incident_duration_seconds": percentile([e.get("duration_seconds") for e in episodes if e["incident_id"] != "none"], .95),
    }


def markdown_table(metrics):
    fields = [("attempts", "Incident attempts"), ("root_cause_accuracy", "Supported root-cause accuracy"),
              ("candidate_root_cause_accuracy", "Candidate root-cause accuracy"), ("top2_root_cause_recall", "Top-2 recall"),
              ("evidence_completeness", "Evidence citation coverage"), ("unsupported_diagnosis_rate", "Unsupported claims / attempts"),
              ("successful_repair_rate", "Approved repair success"), ("unsafe_action_rate", "Unsafe write rate"),
              ("unnecessary_repair_rate", "Unnecessary proposal rate (healthy)"), ("regression_rate", "Immediate regression rate"),
              ("mttd_seconds", "MTTD (s)"), ("mttdg_seconds", "MTTDg (s)"), ("mttr_seconds", "Agent MTTR (s)"),
              ("tokens_per_comparison", "Tokens / comparison"), ("p50_comparison_seconds", "P50 comparison (s)"),
              ("p95_comparison_seconds", "P95 comparison (s)")]
    comparisons = metrics["comparisons"]
    lines = ["| Metric | " + " | ".join(comparisons) + " |", "|---|" + "---|" * len(comparisons)]
    for key, label in fields:
        values = [comparison[key] for comparison in comparisons.values()]
        lines.append("| " + label + " | " + " | ".join("N/A" if v is None else f"{v:.3f}" for v in values) + " |")
    return "\n".join(lines) + "\n"
