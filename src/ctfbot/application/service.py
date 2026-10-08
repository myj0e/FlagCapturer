"""Shared Stage B application service for previewing and running one challenge."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ctfbot.agent.loop import RunLimits, RunResult
from ctfbot.application.baseline import BaselineAdmissionError, run_baseline, validate_baseline_snapshot
from ctfbot.application.control import RunControl
from ctfbot.application.memory import MemoryView
from ctfbot.challenge.local_service import LocalServiceSpec
from ctfbot.challenge.remote import RemoteSpec
from ctfbot.model_adapters.protocol import ModelSession
from ctfbot.model_adapters.codex_session import CodexModelSession
from ctfbot.application.llm_setup import read_llm_config
from ctfbot.runtime.docker import validate_image_reference


@dataclass(frozen=True, slots=True)
class ChallengeInputPreview:
    path: str
    sha256: str
    bytes: int


@dataclass(frozen=True, slots=True)
class ChallengePreview:
    challenge_id: str
    category: str | None
    workspace: str
    import_mode: str
    inputs: tuple[ChallengeInputPreview, ...]
    model_data_authorized: bool
    authorization_basis: str | None
    verifier: str
    runtime_profile: str
    runtime_image: str
    limits: RunLimits
    service_endpoint: str | None = None
    start_block_reason: str | None = None
    memory_policy: dict[str, Any] | None = None
    additional_prompt: str = ""

    @property
    def start_allowed(self) -> bool:
        return self.model_data_authorized and bool(self.authorization_basis) and self.start_block_reason is None


ModelFactory = Callable[[], ModelSession]
RuntimeFactory = Callable[[Path, str], Any]
ServiceRuntimeFactory = Callable[[Path, str, LocalServiceSpec, Callable[[dict[str, Any]], None]], Any]


class LocalChallengeService:
    """One shared policy path for the headless command and Stage B TUI."""

    def __init__(
        self,
        *,
        model_factory: ModelFactory,
        model_metadata: dict[str, Any],
        runtime_factory: RuntimeFactory | None = None,
        service_runtime_factory: ServiceRuntimeFactory | None = None,
        remote_runtime_factory: Any | None = None,
        memory_root: Path | None = None,
        memory_namespaces: tuple[str, ...] = (),
        evaluation_mode: str = "blind",
    ) -> None:
        self.model_factory = model_factory
        self.model_metadata = model_metadata
        self.runtime_factory = runtime_factory
        self.service_runtime_factory = service_runtime_factory
        self.remote_runtime_factory = remote_runtime_factory
        self.memory_root, self.memory_namespaces, self.evaluation_mode = memory_root, memory_namespaces, evaluation_mode

    def preview(
        self,
        workspace: Path,
        runtime_image: str,
        runs_root: Path,
        limits: RunLimits = RunLimits(),
        *, additional_prompt: str = "",
    ) -> ChallengePreview:
        """Validate and describe a private imported snapshot without starting dependencies."""
        validate_image_reference(runtime_image)
        resolved_workspace, provenance, _task = validate_baseline_snapshot(workspace)
        raw_inputs = provenance["input_files"]
        inputs = tuple(
            ChallengeInputPreview(
                path=record["path"],
                sha256=record["sha256"],
                bytes=record["bytes"],
            )
            for record in raw_inputs
        )
        basis = provenance.get("model_data_authorization_basis")
        if not isinstance(basis, str) or not basis.strip():
            basis = None
        spec = (LocalServiceSpec.from_manifest(provenance["local_service"])
                if provenance["import_mode"] == "local_service" else None)
        block_reason = None
        if spec:
            if self.service_runtime_factory is None:
                block_reason = ("Live local services are disabled pending execution-profile isolation acceptance "
                                "and Docker timeout recovery checks.")
            else:
                validate_profile = getattr(self.service_runtime_factory, "validate", None)
                if callable(validate_profile):
                    try:
                        validate_profile(spec, runtime_image, workspace=resolved_workspace, runs_root=runs_root)
                    except (OSError, ValueError, RuntimeError) as exc:
                        block_reason = str(exc)
        remote = RemoteSpec.from_manifest(provenance["remote"]) if provenance["import_mode"] == "remote" else None
        if remote is not None:
            validator = getattr(self.remote_runtime_factory, "validate", None)
            if not callable(validator):
                block_reason = "Remote execution is disabled pending C4 runtime acceptance."
            else:
                try:
                    validator(remote, runtime_image, workspace=resolved_workspace, runs_root=runs_root)
                except (OSError, ValueError, RuntimeError) as exc:
                    block_reason = str(exc)
        memory_policy = None
        try:
            memory_policy = MemoryView(self.memory_root, namespaces=self.memory_namespaces,
                                       mode=self.evaluation_mode, challenge_id=provenance["challenge_id"],
                                       workspace=resolved_workspace, runs_root=runs_root).metadata()
        except (OSError, ValueError, RuntimeError) as exc:
            block_reason = str(exc)
        return ChallengePreview(
            challenge_id=str(provenance["challenge_id"]),
            category=provenance.get("category") if isinstance(provenance.get("category"), str) else None,
            workspace=str(resolved_workspace),
            import_mode=str(provenance["import_mode"]),
            inputs=inputs,
            model_data_authorized=provenance.get("model_data_authorized") is True and basis is not None,
            authorization_basis=basis,
            verifier="model-selected candidate; correctness unverified",
            runtime_profile=("candidate controller TCP connector; offline solver; pinned IP and bounded authorization window"
                             if remote else "local service candidate; isolated IPv4 bridge; no external DNS or published ports"
                             if spec else "offline Docker; read-only input; bounded tmpfs workdir"),
            runtime_image=runtime_image,
            limits=limits,
            service_endpoint=remote.endpoint if remote else spec.endpoint if spec else None,
            start_block_reason=block_reason,
            memory_policy=memory_policy,
            additional_prompt=additional_prompt,
        )

    def run(
        self,
        workspace: Path,
        runtime_image: str,
        runs_root: Path,
        limits: RunLimits = RunLimits(),
        *,
        control: RunControl | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        additional_prompt: str = "",
    ) -> RunResult:
        preview = self.preview(workspace, runtime_image, runs_root, limits, additional_prompt=additional_prompt)
        if not preview.start_allowed:
            raise BaselineAdmissionError(preview.start_block_reason or
                                         "challenge attachment transmission to a model has not been authorized")
        return run_baseline(
            workspace=Path(preview.workspace),
            runtime_image=runtime_image,
            runs_root=runs_root,
            model_factory=self.model_factory,
            model_metadata=self.model_metadata,
            runtime_factory=self.runtime_factory,
            service_runtime_factory=self.service_runtime_factory,
            remote_runtime_factory=self.remote_runtime_factory,
            memory_root=self.memory_root, memory_namespaces=self.memory_namespaces, evaluation_mode=self.evaluation_mode,
            limits=limits,
            control=control,
            event_sink=event_sink,
            additional_prompt=preview.additional_prompt,
        )


def create_codex_application_service(*, runtime_factory: RuntimeFactory | None = None,
                                     service_profile: Path | None = None,
                                     remote_profile: Path | None = None,
                                     remote_grant: Path | None = None,
                                     memory_root: Path | None = None, memory_namespaces: tuple[str, ...] = (),
                                     evaluation_mode: str = "blind") -> LocalChallengeService:
    """Build a lazy Codex service; configuration and process startup wait until a run passes preflight."""
    metadata: dict[str, Any] = {}

    def make_model() -> ModelSession:
        config = read_llm_config()
        if not config:
            raise BaselineAdmissionError("no model is configured; run `ctfbot llm setup` first")
        metadata.update({
            "provider": str(config["provider"]),
            "model": str(config["model"]),
            "reasoning_effort": config.get("reasoning_effort"),
            "provider_api": "Codex App Server experimental dynamicTools",
        })
        return CodexModelSession(
            str(config["model"]),
            str(config["reasoning_effort"]) if config.get("reasoning_effort") else None,
        )

    return LocalChallengeService(
        model_factory=make_model,
        model_metadata=metadata,
        runtime_factory=runtime_factory,
        service_runtime_factory=_reviewed_service_factory(service_profile),
        remote_runtime_factory=_reviewed_remote_factory(remote_profile, remote_grant),
        memory_root=memory_root, memory_namespaces=memory_namespaces, evaluation_mode=evaluation_mode,
    )


def _reviewed_service_factory(path: Path | None):
    from ctfbot.application.service_profile import ReviewedServiceFactory
    return ReviewedServiceFactory(path)


def _reviewed_remote_factory(path: Path | None, grant: Path | None):
    from ctfbot.application.remote_profile import ReviewedRemoteFactory
    return ReviewedRemoteFactory(path, grant)
