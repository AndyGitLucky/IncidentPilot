"""OpenTelemetry spans and Prometheus counters for a finite benchmark job."""

from contextlib import contextmanager
from contextvars import ContextVar
import inspect
import json
import time

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest, push_to_gateway
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

CURRENT = ContextVar("incidentpilot_telemetry", default=None)


class JsonlExporter(SpanExporter):
    def __init__(self, path):
        self.path = path
        path.write_text("", encoding="utf-8")

    def export(self, spans):
        with self.path.open("a", encoding="utf-8") as stream:
            for span in spans:
                stream.write(json.dumps({
                    "name": span.name, "trace_id": format(span.context.trace_id, "032x"),
                    "span_id": format(span.context.span_id, "016x"),
                    "parent_span_id": format(span.parent.span_id, "016x") if span.parent else None,
                    "start_time_unix_nano": span.start_time, "end_time_unix_nano": span.end_time,
                    "status": span.status.status_code.name, "attributes": dict(span.attributes),
                }) + "\n")
        return SpanExportResult.SUCCESS


class CheckedExporter(SpanExporter):
    def __init__(self, delegate, telemetry):
        self.delegate = delegate
        self.telemetry = telemetry

    def export(self, spans):
        try:
            result = self.delegate.export(spans)
        except Exception:
            result = SpanExportResult.FAILURE
        if result != SpanExportResult.SUCCESS:
            self.telemetry.export_errors += 1
        return result

    def shutdown(self):
        self.delegate.shutdown()


class Telemetry:
    def __init__(self, output, *, otlp_endpoint=None, pushgateway=None):
        self.output = output
        self.pushgateway = pushgateway
        self.export_errors = 0
        self.provider = TracerProvider(resource=Resource.create({"service.name": "incidentpilot"}))
        self.provider.add_span_processor(SimpleSpanProcessor(JsonlExporter(output / "traces.jsonl")))
        if otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            self.provider.add_span_processor(BatchSpanProcessor(CheckedExporter(
                OTLPSpanExporter(endpoint=otlp_endpoint, timeout=5), self)))
        self.tracer = self.provider.get_tracer("incidentpilot.workflow")
        self.registry = CollectorRegistry()
        self.incidents = Counter("incident_total", "Injected fixture episodes", registry=self.registry)
        self.correct = Counter("diagnosis_correct_total", "Supported correct diagnoses", ["mode"], registry=self.registry)
        self.repairs = Counter("repair_success_total", "Verified human-approved repairs", ["mode"], registry=self.registry)
        self.unsafe = Counter("unsafe_action_total", "Observed writes without approval", registry=self.registry)
        self.tools = Counter("tool_call_total", "Read tool calls", ["tool", "result"], registry=self.registry)
        self.tokens = Counter("tokens_total", "Reported provider tokens", ["mode", "kind"], registry=self.registry)
        self.latency = Histogram("agent_latency_seconds", "Workflow stage latency", ["stage"], registry=self.registry,
                                 buckets=(.1, .5, 1, 5, 10, 30, 60, 120, 300))
        self.active = Gauge("incident_active", "An injected incident is active", ["incident_type"], registry=self.registry)
        self.stage = Gauge("diagnosis_stage", "Current workflow stage", ["stage"], registry=self.registry)
        self.health = Gauge("demo_service_healthy", "Latest verified service health", registry=self.registry)
        self.pods = Gauge("demo_pods", "Latest observed API pod state", ["status"], registry=self.registry)
        self.mttr = Gauge("agent_mttr_seconds", "Latest verified approved-agent recovery time", registry=self.registry)
        self.manual = Gauge("manual_recovery_seconds", "Latest manual fixture recovery time", registry=self.registry)
        self.actions = Gauge("agent_last_action_timestamp_seconds", "Latest workflow action timestamp", ["action"], registry=self.registry)
        self.traces = Gauge("agent_latest_trace", "Latest episode trace ID", ["trace_id"], registry=self.registry)
        self.health.set(1)
        for mode in ("raw", "structured", "runbook"):
            self.correct.labels(mode)
            self.repairs.labels(mode)
            for kind in ("prompt_tokens", "completion_tokens"):
                self.tokens.labels(mode, kind)

    @contextmanager
    def activate(self):
        token = CURRENT.set(self)
        try:
            yield self
        finally:
            CURRENT.reset(token)

    @contextmanager
    def span(self, name, **attributes):
        started = time.monotonic()
        with self.tracer.start_as_current_span(name, attributes=attributes,
                                              record_exception=False, set_status_on_exception=False) as span:
            try:
                yield span
            except BaseException as error:
                # A LangGraph approval interrupt is a normal pause, not a failure.
                if type(error).__name__ != "GraphInterrupt":
                    span.set_status(trace.Status(trace.StatusCode.ERROR, "workflow step failed"))
                raise
            finally:
                self.latency.labels(name).observe(time.monotonic() - started)

    def publish(self):
        (self.output / "metrics.prom").write_bytes(generate_latest(self.registry))
        if self.pushgateway:
            try:
                push_to_gateway(self.pushgateway, job="incidentpilot_benchmark", registry=self.registry, timeout=5)
            except Exception:
                self.export_errors += 1

    def close(self):
        self.publish()
        if not self.provider.force_flush(timeout_millis=10000):
            self.export_errors += 1
        self.provider.shutdown()


@contextmanager
def span(name, **attributes):
    telemetry = CURRENT.get()
    if telemetry:
        with telemetry.span(name, **attributes) as current:
            yield current
    else:
        yield None


def instrument_node(name, function):
    def begin():
        telemetry = CURRENT.get()
        if telemetry:
            telemetry.stage.clear()
            telemetry.stage.labels(name).set(1)
            telemetry.actions.labels(name).set(time.time())
            telemetry.publish()

    if inspect.iscoroutinefunction(function):
        async def wrapped(state):
            begin()
            with span(name):
                return await function(state)
    else:
        def wrapped(state):
            begin()
            with span(name):
                return function(state)
    return wrapped
