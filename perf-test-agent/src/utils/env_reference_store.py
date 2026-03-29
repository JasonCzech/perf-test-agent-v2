"""Environment reference storage utilities.

Provides manifest-aware helpers to list, load, and update per-application
configuration references that live under config/environment_references.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field

from src.models.env_config import ApplicationConfig, ConfigField, EnvironmentReference, InspectionTool
from src.runtime import get_repo_root

REPO_ROOT = get_repo_root()
CONFIG_ROOT = REPO_ROOT / "config"
ENV_REF_ROOT = CONFIG_ROOT / "environment_references"
MANIFEST_PATH = ENV_REF_ROOT / "manifest.yaml"
ARCHIVE_ROOT = ENV_REF_ROOT / "archive"


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
    endpoint_url: Optional[str] = None
    owner_team: Optional[str] = None
    version: Optional[str] = None
    display_on_new_test: bool = True
    is_active: bool = True
    archived_at: Optional[datetime] = None
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
    include_inactive: bool = False,
) -> list[EnvironmentReferenceRecord]:
    """Return references filtered by optional criteria."""
    manifest = load_manifest()
    refs = manifest.references

    if not include_inactive:
        refs = [r for r in refs if r.is_active]

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
    include_inactive: bool = False,
) -> EnvironmentReferenceRecord:
    """Fetch a single record from the manifest."""
    matches = list_references(
        application_key=application_key,
        environment=environment,
        lab_environment=lab_environment,
        release_code=release_code,
        api_variant=api_variant,
        include_inactive=include_inactive,
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
        return get_reference_record(
            application_key,
            environment,
            lab_environment,
            release_code,
            api_variant,
            include_inactive=False,
        )
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


def _normalize_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for tag in tags:
        next_tag = (tag or "").strip()
        if not next_tag:
            continue
        key = next_tag.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(next_tag)
    return normalized


def _build_app_tags(tags: list[str], owner_team: Optional[str], version: Optional[str]) -> list[str]:
    merged = list(tags)
    if owner_team:
        merged.append(f"owner:{owner_team}")
    if version:
        merged.append(f"version:{version}")
    return _normalize_tags(merged)


def _to_repo_relative(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _upsert_endpoint_field(model: EnvironmentReference, endpoint_url: str) -> None:
    if not model.applications:
        model.applications = [ApplicationConfig(app_name=model.application_display_name, config_fields=[])]

    app = model.applications[0]
    for idx, field in enumerate(app.config_fields):
        if field.field_name == "BASE_ENDPOINT_URL":
            app.config_fields[idx] = ConfigField(
                field_name="BASE_ENDPOINT_URL",
                expected_perf_value=endpoint_url,
                inspection_tool=InspectionTool.HTTP_PROBE,
                description="Primary endpoint URL for this application in PERF",
            )
            return

    app.config_fields.append(
        ConfigField(
            field_name="BASE_ENDPOINT_URL",
            expected_perf_value=endpoint_url,
            inspection_tool=InspectionTool.HTTP_PROBE,
            description="Primary endpoint URL for this application in PERF",
        )
    )


def list_applications(*, include_inactive: bool = False) -> list[dict[str, Any]]:
    """Return app catalog entries derived from manifest references."""
    manifest = load_manifest()
    grouped: dict[str, list[EnvironmentReferenceRecord]] = {}
    for record in manifest.references:
        if not include_inactive and not record.is_active:
            continue
        grouped.setdefault(record.application_key, []).append(record)

    items: list[dict[str, Any]] = []
    for key, records in grouped.items():
        records_sorted = sorted(records, key=lambda r: r.last_updated, reverse=True)
        latest = records_sorted[0]
        items.append(
            {
                "id": key.replace("-", "_"),
                "reference_key": key,
                "application_name": latest.application_name,
                "api_variant": latest.api_variant,
                "endpoint_url": latest.endpoint_url or "",
                "owner_team": latest.owner_team or "",
                "version": latest.version or "",
                "tags": latest.tags,
                "display_on_new_test": latest.display_on_new_test,
                "is_active": latest.is_active,
                "updated_by": latest.updated_by,
                "last_updated": latest.last_updated,
                "reference_count": len(records),
            }
        )

    return sorted(items, key=lambda item: (item["application_name"] or item["reference_key"]).lower())


def register_application(
    *,
    application_key: str,
    application_name: str,
    endpoint_url: str,
    api_variant: str,
    tags: list[str],
    owner_team: Optional[str],
    version: Optional[str],
    display_on_new_test: bool,
    updated_by: str,
    environment: str = "PERF",
    lab_environment: str = "PERF",
    release_code: str = "current",
) -> EnvironmentReferenceRecord:
    """Register a new application and create an initial PERF/current YAML reference."""
    key = application_key.strip().lower()
    manifest = load_manifest()
    if any(r.application_key == key and r.is_active for r in manifest.references):
        raise ValueError(f"Application '{key}' already exists")

    app_tags = _build_app_tags(tags, owner_team, version)
    model = EnvironmentReference(
        environment_name=environment.upper(),
        lab_environment=lab_environment.upper(),
        release_code=release_code,
        application_key=key,
        application_display_name=application_name.strip(),
        api_variant=api_variant,
        updated_by=updated_by,
        applications=[
            ApplicationConfig(
                app_name=application_name.strip(),
                app_description="Runtime registered application",
                tags=app_tags,
                config_fields=[
                    ConfigField(
                        field_name="BASE_ENDPOINT_URL",
                        expected_perf_value=endpoint_url.strip(),
                        inspection_tool=InspectionTool.HTTP_PROBE,
                        description="Primary endpoint URL for this application in PERF",
                    )
                ],
            )
        ],
    )
    yaml_content = yaml.safe_dump(model.model_dump(mode="json"), sort_keys=False)
    save_reference(
        application_key=key,
        environment=environment,
        lab_environment=lab_environment,
        release_code=release_code,
        api_variant=api_variant,
        yaml_content=yaml_content,
        updated_by=updated_by,
        application_name=application_name,
    )

    manifest = load_manifest()
    for record in manifest.references:
        if (
            record.application_key == key
            and record.environment.lower() == environment.lower()
            and record.lab_environment.lower() == lab_environment.lower()
            and record.release_code.lower() == release_code.lower()
            and record.api_variant == api_variant
        ):
            record.endpoint_url = endpoint_url.strip()
            record.owner_team = (owner_team or "").strip() or None
            record.version = (version or "").strip() or None
            record.tags = app_tags
            record.display_on_new_test = bool(display_on_new_test)
            record.is_active = True
            record.archived_at = None
            record.updated_by = updated_by
            record.last_updated = datetime.utcnow()
            save_manifest(manifest)
            return record

    raise RuntimeError(f"Unable to locate newly created record for application '{key}'")


def update_application(
    *,
    application_key: str,
    application_name: str,
    endpoint_url: str,
    api_variant: str,
    tags: list[str],
    owner_team: Optional[str],
    version: Optional[str],
    display_on_new_test: Optional[bool],
    updated_by: str,
) -> list[EnvironmentReferenceRecord]:
    """Update metadata/YAML for all active references under an application key."""
    key = application_key.strip().lower()
    manifest = load_manifest()
    targets = [r for r in manifest.references if r.application_key == key and r.is_active]
    if not targets:
        raise KeyError(f"Application '{key}' not found")

    app_tags = _build_app_tags(tags, owner_team, version)
    now = datetime.utcnow()
    for record in targets:
        model = load_reference_model(record)
        model.application_key = key
        model.application_display_name = application_name.strip()
        model.api_variant = api_variant
        model.updated_by = updated_by
        model.last_updated = now
        for app_cfg in model.applications:
            app_cfg.app_name = application_name.strip()
            app_cfg.tags = app_tags
        _upsert_endpoint_field(model, endpoint_url.strip())

        payload = model.model_dump(mode="json")
        path = record.resolve_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(payload, sort_keys=False))

        record.application_name = application_name.strip()
        record.api_variant = api_variant
        record.endpoint_url = endpoint_url.strip()
        record.owner_team = (owner_team or "").strip() or None
        record.version = (version or "").strip() or None
        record.tags = app_tags
        if display_on_new_test is not None:
            record.display_on_new_test = bool(display_on_new_test)
        record.updated_by = updated_by
        record.last_updated = now

    save_manifest(manifest)
    return targets


def archive_application(*, application_key: str, updated_by: str) -> int:
    """Archive all active references for an application key and mark them inactive."""
    key = application_key.strip().lower()
    manifest = load_manifest()
    targets = [r for r in manifest.references if r.application_key == key and r.is_active]
    if not targets:
        raise KeyError(f"Application '{key}' not found")

    now = datetime.utcnow()
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    archive_base = ARCHIVE_ROOT / key / timestamp
    archive_base.mkdir(parents=True, exist_ok=True)

    for record in targets:
        source = record.resolve_path()
        if source.exists():
            dest = archive_base / source.name
            if dest.exists():
                dest = archive_base / f"{source.stem}-{int(now.timestamp())}{source.suffix}"
            shutil.move(str(source), str(dest))
            record.path = _to_repo_relative(dest)
        record.is_active = False
        record.archived_at = now
        record.updated_by = updated_by
        record.last_updated = now

    save_manifest(manifest)
    return len(targets)


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
