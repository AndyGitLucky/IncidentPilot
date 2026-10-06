"""M5 fairness, honest scoring, trace privacy, and interrupted-run cleanup."""

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("opentelemetry.sdk")

from langgraph.types import Command
from agent.graph import build_graph, collect_evidence
from eval.benchmark import FrozenTools, empty_row
from eval.comparison import ComparisonDiagnoser, retrieve_runbooks
from eval.scoring import aggregate, markdown_table, percentile, score
from telemetry.runtime import Telemetry
from test_diagnosis import FakeTools
from test_incidents import IncidentDiagnoser, IncidentTools


def test_frozen_snapshot_is_identical_and_does_not_reread_live_tools():
    async def check():
        live = FakeTools()
        snapshot = FrozenTools(live)
        original = await collect_evidence(snapshot)
        snapshot.recording = False
        live.pods.clear()
        live.failure = "get_deployment"
        replayed = await collect_evidence(snapshot)
        assert [e.data for e in original] == [e.data for e in replayed]
        replayed[0].data.clear()
        second = await collect_evidence(snapshot)
        assert second[0].data == original[0].data
        assert len(live.calls) == snapshot.calls == 5
    asyncio.run(check())


def test_failed_shared_tool_is_replayed_without_retry():
    async def check():
        live = FakeTools()
        live.failure = "get_events"
        snapshot = FrozenTools(live)
        original = await collect_evidence(snapshot)
        snapshot.recording = False
        live.failure = None
        replayed = await collect_evidence(snapshot)
        assert original[2].error and replayed[2].error
        assert len(live.calls) == 5
    asyncio.run(check())


def test_retrieval_uses_observed_keywords_without_incident_label(tmp_path):
    (tmp_path / "one.md").write_text("shared crashloop startup failed", encoding="utf-8")
    (tmp_path / "two.md").write_text("shared memory OOMKilled", encoding="utf-8")
    evidence = asyncio.run(collect_evidence(IncidentTools("oom")))
    assert retrieve_runbooks(evidence, directory=tmp_path)[0]["name"] == "two.md"
    assert retrieve_runbooks([], directory=tmp_path) == []


@pytest.mark.parametrize("mode", ["raw", "structured", "runbook"])
def test_comparison_never_sends_injection_ground_truth(mode):
    evidence = asyncio.run(collect_evidence(FakeTools()))
    provider = SimpleNamespace(api_key="sensitive-test-key", model="test", transport=None)
    diagnoser = ComparisonDiagnoser(provider, mode)
    prompt = diagnoser.system_prompt(evidence) + diagnoser.evidence_prompt(evidence)
    assert "sensitive-test-key" not in prompt and "injection script" not in prompt
    if mode == "raw":
        assert "e1 [kubernetes]" in prompt
        assert "startup_profile=crashloop_fixture" not in prompt
    else:
        assert '"evidence"' in prompt
    assert bool(diagnoser.retrieved) == (mode == "runbook")


def test_candidate_and_supported_accuracy_are_separate():
    async def check():
        class Unsupported(IncidentDiagnoser):
            async def diagnose(self, evidence):
                result = await super().diagnose(evidence)
                result.diagnosis.evidence_ids = ["fabricated"]
                return result
        report = (await build_graph(IncidentTools("oom"), Unsupported("oom"), repair=True).ainvoke(
            {}, {"configurable": {"thread_id": "unsupported"}}))["report"]
        scored = score(report, "oom")
        assert scored["candidate_correct"] is True
        assert scored["diagnosis_correct"] is False
        assert scored["unsupported_claim"] is True
        assert scored["evidence_completeness"] == 0
    asyncio.run(check())


def test_top2_requires_an_explicit_first_alternative_key():
    async def check():
        report = (await build_graph(IncidentTools("oom"), IncidentDiagnoser("bad_env"), repair=True).ainvoke(
            {}, {"configurable": {"thread_id": "top2"}}))["report"]
        report.model_diagnosis.alternative_hypotheses = ["oom"]
        assert score(report, "oom")["top2_correct"]
        report.model_diagnosis.alternative_hypotheses = ["maybe out of memory"]
        assert not score(report, "oom")["top2_correct"]
        report.model_diagnosis.alternative_hypotheses = ["crashloop", "oom"]
        assert not score(report, "oom")["top2_correct"]
    asyncio.run(check())


def summary_with_rows(rows):
    return {"modes": ["structured"], "episodes": [{"incident_id": "oom", "comparisons": rows,
        "restored": True, "duration_seconds": 10, "shared_tool_calls": 5, "manual_recovery_seconds": 2}]}


def test_failed_attempts_stay_in_denominator_and_missing_usage_is_not_zero():
    good = empty_row("structured", "oom")
    good.update(diagnosis_correct=True, candidate_correct=True, tokens=100)
    metrics = aggregate(summary_with_rows([good, empty_row("structured", "oom")]))["comparisons"]["structured"]
    assert metrics["attempts"] == 2 and metrics["root_cause_accuracy"] == .5
    assert metrics["tokens_per_comparison"] == 100 and metrics["token_usage_coverage"] == .5


def test_manual_recovery_and_denial_do_not_score_agent_repairs():
    metrics = aggregate(summary_with_rows([empty_row("structured", "oom")]))
    scored = metrics["comparisons"]["structured"]
    assert scored["approved_repair_attempts"] == 0
    assert scored["successful_repair_rate"] is None and scored["mttr_seconds"] is None
    assert scored["regression_rate"] is None and metrics["manual_recovery_seconds"] == 2
    assert "N/A" in markdown_table(metrics)


def test_only_approved_attempts_count_in_repair_success_denominator():
    success = empty_row("structured", "oom")
    success.update(repair_attempted=True, agent_repair_success=True, agent_recovery_seconds=12, regression=False)
    failed = empty_row("structured", "oom")
    failed.update(repair_attempted=True, agent_repair_success=False)
    scored = aggregate(summary_with_rows([success, failed, empty_row("structured", "oom")]))["comparisons"]["structured"]
    assert scored["approved_repair_attempts"] == 2 and scored["successful_repair_rate"] == .5
    assert scored["mttr_seconds"] == 12


def test_healthy_controls_measure_unnecessary_proposals_separately():
    healthy = empty_row("structured", "none")
    healthy["unnecessary_proposal"] = True
    scored = aggregate(summary_with_rows([empty_row("structured", "oom"), healthy]))["comparisons"]["structured"]
    assert scored["attempts"] == 1 and scored["healthy_controls"] == 1
    assert scored["unnecessary_repair_rate"] == 1


def test_small_sample_percentiles_use_documented_nearest_rank():
    assert percentile([1, 2, 3, 4, None], .5) == 2
    assert percentile([1, 2, 3, 4], .95) == 4
    assert percentile([], .95) is None


def test_approval_pause_is_traced_without_writes_or_sensitive_data(tmp_path):
    telemetry = Telemetry(tmp_path)
    factory = Mock()
    async def check():
        with telemetry.activate(), telemetry.span("incident", incident_type="oom"):
            graph = build_graph(IncidentTools("oom"), IncidentDiagnoser("oom"), repair=True, runner_factory=factory)
            configuration = {"configurable": {"thread_id": "trace-test"}}
            pending = await graph.ainvoke({}, configuration)
            assert pending["__interrupt__"]
            final = await graph.ainvoke(Command(resume="deny"), configuration)
            assert final["report"].outcome == "denied"
    asyncio.run(check())
    telemetry.close()
    factory.assert_not_called()
    spans = [json.loads(line) for line in (tmp_path / "traces.jsonl").read_text().splitlines()]
    root = next(s for s in spans if s["name"] == "incident")
    assert {s["trace_id"] for s in spans} == {root["trace_id"]}
    collect = next(s for s in spans if s["name"] == "COLLECT")
    tool = next(s for s in spans if s["name"] == "get_deployment")
    assert tool["parent_span_id"] == collect["span_id"]
    assert collect["parent_span_id"] == root["span_id"]
    assert all(s["status"] != "ERROR" for s in spans)
    assert "EXECUTE" not in {s["name"] for s in spans}
    assert "Database health check" not in json.dumps(spans) and "DB_PASSWORD" not in json.dumps(spans)


def test_trace_exceptions_are_sanitized(tmp_path):
    telemetry = Telemetry(tmp_path)
    with pytest.raises(RuntimeError), telemetry.span("test_step"):
        raise RuntimeError("sensitive credential or log contents")
    telemetry.close()
    encoded = (tmp_path / "traces.jsonl").read_text()
    assert "sensitive credential" not in encoded and json.loads(encoded)["status"] == "ERROR"


def test_telemetry_export_failure_is_recorded_without_losing_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr("telemetry.runtime.push_to_gateway", Mock(side_effect=RuntimeError("credentials")))
    telemetry = Telemetry(tmp_path, pushgateway="127.0.0.1:1")
    with telemetry.span("incident"):
        pass
    telemetry.close()
    assert telemetry.export_errors == 1
    assert (tmp_path / "metrics.prom").is_file() and (tmp_path / "traces.jsonl").is_file()


@pytest.mark.parametrize("failure", ["comparison", "cleanup", "startup"])
def test_benchmark_retains_failure_artifact_and_always_restores_injected_fixture(tmp_path, monkeypatch, failure):
    from eval import benchmark
    state = {"incident": "none"}
    mutations = []
    api = Mock()
    api.__enter__ = Mock(return_value=api)
    api.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(benchmark.client, "ApiClient", Mock(return_value=api))
    monkeypatch.setattr(benchmark.config, "load_kube_config", Mock())
    reader = SimpleNamespace(get_deployment=lambda *a: {"deployment": {"uid": "api", "generation": 1}})
    monkeypatch.setattr(benchmark, "KubernetesReader", lambda api: reader)
    verifier = Mock()
    if failure == "startup":
        verifier.verify.side_effect = RuntimeError("startup")
    monkeypatch.setattr(benchmark, "RestrictedRepairRunner", lambda api: verifier)
    monkeypatch.setattr(benchmark, "smoke_check", lambda: None)
    monkeypatch.setattr(benchmark, "resource_signature", lambda reader: [("Deployment", "api", state["incident"])])
    monkeypatch.setattr(benchmark.OpenRouterDiagnoser, "from_env", lambda: SimpleNamespace(model="test"))
    def mutate(reader, incident, injecting):
        mutations.append((incident, injecting))
        state["incident"] = incident if injecting else "none"
        if failure == "cleanup" and not injecting:
            raise RuntimeError("cleanup failed")
        return {"deployment_uid": "api", "generation": 1}
    monkeypatch.setattr(benchmark, "mutate", mutate)
    async def observe(*args):
        pass
    monkeypatch.setattr(benchmark, "observe", observe)
    @asynccontextmanager
    async def connect():
        class Tools:
            async def call(self, name, arguments):
                source = FakeTools(healthy=True) if state["incident"] == "none" else IncidentTools(state["incident"])
                return await source.call(name, arguments)
        yield Tools()
    monkeypatch.setattr(benchmark, "connect_tools", connect)
    async def compare(snapshot, provider, mode, incident, *args):
        if incident != "none" and failure == "comparison":
            raise RuntimeError("model failed")
        row = empty_row(mode, incident)
        row.update(passed=True, unsafe_write=False)
        return row
    monkeypatch.setattr(benchmark, "compare", compare)
    args = SimpleNamespace(output=tmp_path, fixtures_only=False, human_repair=False, repetitions=1,
                           timeout=1, otlp_endpoint=None, pushgateway=None)
    assert asyncio.run(benchmark.evaluate(args)) == 2
    summary = json.loads((tmp_path / "results.json").read_text())
    assert not summary["passed"] and (tmp_path / "metrics.md").exists()
    if failure == "startup":
        assert not mutations and summary["error"]
    else:
        assert ("bad_image", True) in mutations and ("bad_image", False) in mutations
        if failure == "cleanup":
            assert len(summary["episodes"]) == 2
        else:
            assert summary["metrics"]["comparisons"]["raw"]["attempts"] == 5


def test_dashboard_is_one_local_dashboard_and_queries_required_panels():
    root = Path(__file__).resolve().parents[1]
    dashboard = json.loads((root / "telemetry/grafana/incidentpilot.json").read_text(encoding="utf-8"))
    assert dashboard["uid"] == "incidentpilot"
    queries = {t["expr"] for p in dashboard["panels"] for t in p.get("targets", [])}
    assert {"incident_active > 0", "diagnosis_stage > 0", "agent_mttr_seconds > 0", "agent_latest_trace"} <= queries
    assert len({p["id"] for p in dashboard["panels"]}) == len(dashboard["panels"])


def test_every_observability_deployment_has_a_matching_service():
    import yaml
    root = Path(__file__).resolve().parents[1]
    resources = list(yaml.safe_load_all((root / "cluster/observability/stack.yaml").read_text()))
    deployments = {r["metadata"]["name"]: r for r in resources if r["kind"] == "Deployment"}
    services = {r["metadata"]["name"]: r for r in resources if r["kind"] == "Service"}
    assert set(deployments) == set(services) == {"grafana", "tempo", "pushgateway"}
    for name in deployments:
        assert services[name]["spec"]["selector"] == deployments[name]["spec"]["template"]["metadata"]["labels"]


@pytest.mark.parametrize("incident", ["bad_image", "crashloop", "oom", "bad_env", "bad_selector"])
@pytest.mark.parametrize("mode", ["raw", "structured", "runbook"])
def test_full_comparison_scores_provider_output_denies_and_retains_reports(tmp_path, monkeypatch, incident, mode):
    import httpx
    from agent.diagnosis import OpenRouterDiagnoser
    from eval import benchmark
    from test_diagnosis import model_response
    calls = []

    def respond(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert "tools" not in payload and "ground_truth.yaml:" not in json.dumps(payload)
        return httpx.Response(200, json=model_response({
            "status": "diagnosed", "incident_type": incident, "suspected_root_cause": "Offline mock provider hypothesis",
            "confidence": .95, "evidence_ids": ["e1", "e2", "e3", "e4", "e5"], "alternative_hypotheses": []}))
    provider = OpenRouterDiagnoser("mock-key", transport=httpx.MockTransport(respond))
    telemetry = Telemetry(tmp_path)
    monkeypatch.setattr(benchmark, "resource_signature", lambda admin: [("Deployment", "api", "unchanged")])

    async def check():
        snapshot = FrozenTools(IncidentTools(incident))
        await collect_evidence(snapshot)
        snapshot.recording = False
        with telemetry.activate(), telemetry.span("incident", incident_type=incident):
            return await benchmark.compare(snapshot, provider, mode, incident, tmp_path, 0, None, telemetry)
    row = asyncio.run(check())
    telemetry.close()
    assert row["passed"] and row["diagnosis_correct"] and row["proposal_correct"]
    assert row["denied"] and row["unsafe_write"] is False and not row["repair_attempted"]
    assert row["tokens"] == 590 and len(calls) == 1
    final = json.loads((tmp_path / f"{mode}-result.json").read_text())
    assert final["outcome"] == "denied" and "EXECUTE" not in final["stages"]
    assert row["runbooks"] if mode == "runbook" else not row["runbooks"]


def test_absent_provider_usage_remains_missing():
    import httpx
    from agent.diagnosis import OpenRouterDiagnoser
    from test_diagnosis import diagnosis, model_response
    body = model_response(diagnosis())
    body.pop("usage")
    provider = OpenRouterDiagnoser("mock", transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
    result = asyncio.run(provider.diagnose(asyncio.run(collect_evidence(FakeTools()))))
    assert result.usage == {}


@pytest.mark.parametrize("outcome", ["denied", "recovered", "failed", "regression"])
def test_human_repair_uses_fresh_evidence_and_only_scores_exact_approval(tmp_path, monkeypatch, outcome):
    import httpx
    import re
    from agent.diagnosis import OpenRouterDiagnoser
    from agent.repair import RepairError
    from eval import benchmark
    from test_diagnosis import model_response
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=model_response({
            "status": "diagnosed", "incident_type": "oom", "suspected_root_cause": "Offline mock OOM diagnosis",
            "confidence": .9, "evidence_ids": ["e1", "e2", "e3", "e4", "e5"], "alternative_hypotheses": []}))
    provider = OpenRouterDiagnoser("mock", transport=httpx.MockTransport(respond))
    runner = Mock()
    runner.increase_memory_limit_to_known_safe_value.return_value = {"deployment_uid": "api", "generation": 6}
    runner.verify.return_value = {"healthy": True}
    if outcome == "failed":
        runner.increase_memory_limit_to_known_safe_value.side_effect = RepairError("Concurrent edit rejected")
    factory = Mock(return_value=runner)
    original = benchmark.build_graph
    monkeypatch.setattr(benchmark, "build_graph", lambda tools, diagnoser, **kw: original(
        tools, diagnoser, runner_factory=factory, **kw))
    monkeypatch.setattr("builtins.input", lambda prompt: "deny" if outcome == "denied" else
                        "approve " + re.search(r"approve ([a-f0-9]+)", prompt).group(1))
    monkeypatch.setattr(benchmark, "smoke_check", lambda: None)
    independent = Mock()
    if outcome == "regression":
        independent.verify.side_effect = RepairError("Post-repair verification failed")
    telemetry = Telemetry(tmp_path)
    row = empty_row("structured", "oom")
    row["passed"] = True
    tools = IncidentTools("oom")

    async def check():
        snapshot = FrozenTools(tools)
        await collect_evidence(snapshot)
        snapshot.recording = False
        with telemetry.activate(), telemetry.span("incident", incident_type="oom"):
            await benchmark.human_repair(tools, provider, "oom", tmp_path, 0, row, telemetry, independent)
        assert len(tools.calls) == 10  # five frozen reads, then five fresh live reads
    asyncio.run(check())
    telemetry.close()
    assert row["human_repair_usage"]["total_tokens"] == 590 and len(calls) == 1
    if outcome == "denied":
        factory.assert_not_called()
        assert not row["repair_attempted"] and row["agent_repair_success"] is None
    else:
        assert row["repair_attempted"]
        assert row["agent_repair_success"] == (outcome != "failed")
        if outcome == "regression":
            assert row["regression"] is True and row["passed"] is False
        elif outcome == "recovered":
            assert row["regression"] is False and row["agent_recovery_seconds"] > 0
        else:
            assert row["passed"] is False and row["regression"] is None
