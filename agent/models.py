"""Structured evidence, diagnosis, and the five fixed repair contracts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    source: Literal["kubernetes", "events", "logs", "metrics"]
    query: str
    observation: str
    timestamp: datetime | None
    data: dict
    error: bool = False


class Diagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["diagnosed", "no_active_incident", "inconclusive"]
    incident_type: Literal["bad_image", "crashloop", "oom", "bad_env", "bad_selector", "none", "unknown"]
    suspected_root_cause: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(max_length=30)
    alternative_hypotheses: list[str] = Field(max_length=10)

    @model_validator(mode="after")
    def consistent_status(self):
        expected = {"diagnosed": {"bad_image", "crashloop", "oom", "bad_env", "bad_selector"},
                    "no_active_incident": {"none"}, "inconclusive": {"unknown"}}
        if self.incident_type not in expected[self.status]:
            raise ValueError("incident_type must match diagnosis status")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("Evidence citations must be unique")
        if self.status != "inconclusive" and not self.evidence_ids:
            raise ValueError("A supported conclusion requires evidence citations")
        return self


class ModelResult(BaseModel):
    diagnosis: Diagnosis
    model: str
    usage: dict[str, int]


class DiagnosisReport(BaseModel):
    stages: list[str]
    evidence: list[Evidence]
    diagnosis: Diagnosis
    model_diagnosis: Diagnosis | None
    model: str
    usage: dict[str, int]


class RepairParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    namespace: Literal["incidentpilot"] = "incidentpilot"
    deployment: Literal["api"] = "api"
    container: Literal["api"] = "api"
    image: Literal["incidentpilot-api:m0"] = "incidentpilot-api:m0"


class RestartParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    namespace: Literal["incidentpilot"] = "incidentpilot"
    deployment: Literal["api"] = "api"
    container: Literal["api"] = "api"
    startup: Literal["image_default"] = "image_default"


class MemoryParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    namespace: Literal["incidentpilot"] = "incidentpilot"
    deployment: Literal["api"] = "api"
    container: Literal["api"] = "api"
    memory_request: Literal["64Mi"] = "64Mi"
    memory_limit: Literal["128Mi"] = "128Mi"


class EnvParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    namespace: Literal["incidentpilot"] = "incidentpilot"
    deployment: Literal["api"] = "api"
    container: Literal["api"] = "api"
    env_name: Literal["DB_HOST"] = "DB_HOST"
    env_value: Literal["postgres"] = "postgres"


class SelectorParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    namespace: Literal["incidentpilot"] = "incidentpilot"
    service: Literal["api"] = "api"
    selector: Literal["app=api"] = "app=api"


class RepairProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    action: Literal["rollback_image_tag", "restart_deployment", "increase_memory_limit_to_known_safe_value",
                    "restore_known_env_value", "restore_known_service_selector"] = "rollback_image_tag"
    parameters: RepairParameters | RestartParameters | MemoryParameters | EnvParameters | SelectorParameters = Field(default_factory=RepairParameters)
    deployment_uid: str
    generation: int
    expected_image: Literal["incidentpilot-api:m1-does-not-exist", "incidentpilot-api:m0"] = "incidentpilot-api:m1-does-not-exist"
    target_uid: str | None = None
    resource_version: str | None = None
    expires_at: datetime
    expected_effect: str = "Restore the known-good API image and complete the stalled rollout."
    risk: Literal["low"] = "low"
    evidence_ids: list[str]

    @model_validator(mode="after")
    def fixed_action_parameters(self):
        types = {"rollback_image_tag": RepairParameters, "restart_deployment": RestartParameters,
                 "increase_memory_limit_to_known_safe_value": MemoryParameters,
                 "restore_known_env_value": EnvParameters, "restore_known_service_selector": SelectorParameters}
        if not isinstance(self.parameters, types[self.action]):
            raise ValueError("Parameters must match the fixed action")
        expected = "incidentpilot-api:m1-does-not-exist" if self.action == "rollback_image_tag" else "incidentpilot-api:m0"
        if self.expected_image != expected:
            raise ValueError("Expected image must match the action fixture")
        if self.action == "restore_known_service_selector" and (not self.target_uid or not self.resource_version):
            raise ValueError("Service repair requires target identity and resource version")
        return self


class RepairReport(DiagnosisReport):
    proposal: RepairProposal | None = None
    approval: Literal["not_requested", "pending", "approved", "denied"] = "not_requested"
    outcome: Literal["not_needed", "blocked", "pending", "denied", "failed", "recovered"] = "blocked"
    execution: dict = Field(default_factory=dict)
    verification: dict = Field(default_factory=dict)
    error: str | None = None
