"""Environment reference storage utilities.

Provides manifest-aware helpers to list, load, and update per-application
configuration references that live under config/environment_references.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

from src.models.env_config import EnvironmentReference

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = REPO_ROOT / "config"
ENV_REF_ROOT = CONFIG_ROOT / "environment_references"
MANIFEST_PATH = ENV_REF_ROOT / "manifest.yaml"


def _ensure_dirs() -> None:
    ENV_REF_ROOT.mkdir(parents=True, exist_ok=True)


class EnvironmentReferenceRecord(BaseModel):
    application_key: str
    application_name: str
    api_variant: str = "core"
    environment: str = "PERF"
    lab_environment: str = "PERF"
    release_code: str = "current"
    path: str
    tags: list[str] = Field(default_factory=list)
    last_updated: datetime = Field(default_factory=datetime.utcnow)
    updated_by: str = "perf-engineering-team"

    def resolve_path(self) -> Path:
        path = Path(self.path)
        if not path.is_absolute():
            path = REPO_ROOT / path
        return path


class EnvironmentReferenceManifest(BaseModel):
    version: str = "1.0.0"
    references: list[EnvironmentReferenceRecord] = Field(default_factory=list)


def load_manifest() -> EnvironmentReferenceManifest:
    """Load the manifest file, creating an empty one if absent."""
    _ensure_dirs()
    if not MANIFEST_PATH.exists():
        manifest = EnvironmentReferenceManifest()
        save_manifest(manifest)
        return manifest

    data = yaml.safe_load(MANIFEST_PATH.read_text()) or {}
    return EnvironmentReferenceManifest.model_validate(data)


def save_manifest(manifest: EnvironmentReferenceManifest) -> None:
    """Persist the manifest to disk."""
    _ensure_dirs()
    payload = manifest.model_dump(mode="json")
    MANIFEST_PATH.write_text(yaml.safe_dump(payload, sort_keys=False))


def list_references(
    *,
    application_key: Optional[str] = None,
    environment: Optional[str] = None,
    lab_environment: Optional[str] = None,
    release_code: Optional[str] = None,
    api_variant: Optional[str] = None,
) -> list[EnvironmentReferenceRecord]:
    """Return references filtered by optional criteria."""
    manifest = load_manifest()
    refs = manifest.references

    if application_key:
        refs = [r for r in refs if r.application_key == application_key]
    if environment:
        refs = [r for r in refs if r.environment.lower() == environment.lower()]
    if lab_environment:
        refs = [r for r in refs if r.lab_environment.lower() == lab_environment.lower()]
    if release_code:
        refs = [r for r in refs if r.release_code.lower() == release_code.lower()]
    if api_variant:
        refs = [r for r in refs if r.api_variant == api_variant]

    return sorted(
        refs,
        key=lambda r: (r.application_key, r.environment, r.lab_environment, r.release_code, r.api_variant),
    )


def get_reference_record(
    application_key: str,
    environment: str,
    lab_environment: str = "PERF",
    release_code: str = "current",
    api_variant: str = "core",
) -> EnvironmentReferenceRecord:
    """Fetch a single record from the manifest."""
    matches = list_references(
        application_key=application_key,
        environment=environment,
        lab_environment=lab_environment,
        release_code=release_code,
        api_variant=api_variant,
    )
    if not matches:
        raise KeyError(
            f"No environment reference registered for {application_key}/{environment}/{lab_environment}/{release_code} ({api_variant})"
        )
    return matches[0]


def read_reference_yaml(record: EnvironmentReferenceRecord) -> str:
    """Read the raw YAML contents for a reference."""
    path = record.resolve_path()
    if not path.exists():
        raise FileNotFoundError(path)
    return path.read_text()


def load_reference_model(record: EnvironmentReferenceRecord) -> EnvironmentReference:
    """Load and validate a reference file into the EnvironmentReference model."""
    raw = yaml.safe_load(read_reference_yaml(record)) or {}
    return EnvironmentReference.model_validate(raw)


def _ensure_record(
    manifest: EnvironmentReferenceManifest,
    *,
    application_key: str,
    application_name: Optional[str],
    environment: str,
    lab_environment: str,
    release_code: str,
    api_variant: str,
) -> EnvironmentReferenceRecord:
    try:
        return get_reference_record(application_key, environment, lab_environment, release_code, api_variant)
    except KeyError:
        release_slug = release_code.lower().replace(" ", "-").replace("/", "-")
        relative_path = (
            Path("config")
            / "environment_references"
            / application_key
            / f"{lab_environment.lower()}-{release_slug}.yaml"
        )
        record = EnvironmentReferenceRecord(
            application_key=application_key,
            application_name=application_name or application_key,
            api_variant=api_variant,
            environment=environment.upper(),
            lab_environment=lab_environment.upper(),
            release_code=release_code,
            path=str(relative_path),
        )
        manifest.references.append(record)
        return record


def save_reference(
    *,
    application_key: str,
    environment: str,
    lab_environment: str,
    release_code: str,
    api_variant: str,
    yaml_content: str,
    updated_by: str = "web-ui",
    application_name: Optional[str] = None,
) -> EnvironmentReference:
    """Validate and persist an environment reference, updating the manifest."""
    manifest = load_manifest()
    record = _ensure_record(
        manifest,
        application_key=application_key,
        application_name=application_name,
        environment=environment,
        lab_environment=lab_environment,
        release_code=release_code,
        api_variant=api_variant,
    )

    parsed = yaml.safe_load(yaml_content) or {}
    model = EnvironmentReference.model_validate(parsed)
    now = datetime.utcnow()
    model.application_key = application_key
    model.api_variant = api_variant
    model.environment_name = environment.upper()
    model.lab_environment = lab_environment.upper()
    model.release_code = release_code
    if not model.application_display_name:
        model.application_display_name = application_name or record.application_name
    model.last_updated = now
    model.updated_by = updated_by

    payload = model.model_dump(mode="json")

    path = record.resolve_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False))

    record.application_name = model.application_display_name
    record.environment = model.environment_name
    record.lab_environment = model.lab_environment
    record.release_code = model.release_code
    record.api_variant = model.api_variant
    record.last_updated = now
    record.updated_by = updated_by
    record.tags = model.get_all_tags()

    save_manifest(manifest)
    return model