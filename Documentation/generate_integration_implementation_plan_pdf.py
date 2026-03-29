from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from textwrap import dedent


OUTPUT_PATH = Path("Documentation/perf_test_agent_integration_implementation_plan.pdf")

PAGE_WIDTH = 612
PAGE_HEIGHT = 792
LEFT = 54
RIGHT = 54
TOP = 54
BOTTOM = 48
CONTENT_WIDTH = PAGE_WIDTH - LEFT - RIGHT

NAVY = (0.106, 0.227, 0.361)
BLUE = (0.020, 0.408, 0.682)
MID = (0.353, 0.396, 0.471)
TEXT = (0.102, 0.102, 0.180)
LIGHT_FILL = (0.933, 0.953, 0.980)
ALT_FILL = (0.957, 0.965, 0.973)
WHITE = (1.0, 1.0, 1.0)
LINE = (0.800, 0.824, 0.855)


def pdf_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )


def color_cmd(rgb: tuple[float, float, float], stroke: bool = False) -> str:
    op = "RG" if stroke else "rg"
    return f"{rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f} {op}"


def approx_char_width(ch: str, font_size: float) -> float:
    if ch in "W@%M":
        factor = 0.90
    elif ch in "ABCDEFGHKNOPQRSTUVXYZmw":
        factor = 0.72
    elif ch in "abcdeghnopquvxyz":
        factor = 0.56
    elif ch in "firtIjJl":
        factor = 0.34
    elif ch in " .,:;|!":
        factor = 0.26
    elif ch in "-_/":
        factor = 0.33
    elif ch.isdigit():
        factor = 0.56
    else:
        factor = 0.50
    return factor * font_size


def text_width(text: str, font_size: float) -> float:
    return sum(approx_char_width(ch, font_size) for ch in text)


def wrap_text(text: str, font_size: float, max_width: float) -> list[str]:
    if not text:
        return [""]
    paragraphs = text.split("\n")
    lines: list[str] = []
    for paragraph in paragraphs:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if text_width(trial, font_size) <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


class PDFWriter:
    def __init__(self) -> None:
        self.objects: list[bytes] = []

    def add_object(self, body: bytes | str) -> int:
        payload = body.encode("latin-1") if isinstance(body, str) else body
        self.objects.append(payload)
        return len(self.objects)

    def build(self, root_id: int, info_id: int) -> bytes:
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for idx, obj in enumerate(self.objects, start=1):
            offsets.append(len(out))
            out.extend(f"{idx} 0 obj\n".encode("latin-1"))
            out.extend(obj)
            out.extend(b"\nendobj\n")
        xref_pos = len(out)
        out.extend(f"xref\n0 {len(self.objects) + 1}\n".encode("latin-1"))
        out.extend(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            out.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
        out.extend(
            (
                f"trailer\n<< /Size {len(self.objects) + 1} /Root {root_id} 0 R /Info {info_id} 0 R >>\n"
                f"startxref\n{xref_pos}\n%%EOF\n"
            ).encode("latin-1")
        )
        return bytes(out)


class Canvas:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def rect(self, x: float, y: float, w: float, h: float, fill: tuple[float, float, float], stroke: tuple[float, float, float] | None = None, line_width: float = 1.0) -> None:
        self.commands.append("q")
        self.commands.append(color_cmd(fill))
        if stroke:
            self.commands.append(color_cmd(stroke, stroke=True))
            self.commands.append(f"{line_width:.2f} w")
            self.commands.append(f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re B")
        else:
            self.commands.append(f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re f")
        self.commands.append("Q")

    def line(self, x1: float, y1: float, x2: float, y2: float, stroke: tuple[float, float, float], line_width: float = 1.0) -> None:
        self.commands.append("q")
        self.commands.append(color_cmd(stroke, stroke=True))
        self.commands.append(f"{line_width:.2f} w")
        self.commands.append(f"{x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")
        self.commands.append("Q")

    def text(self, x: float, y: float, text: str, font: str, size: float, fill: tuple[float, float, float]) -> None:
        self.commands.append("BT")
        self.commands.append(color_cmd(fill))
        self.commands.append(f"/{font} {size:.2f} Tf")
        self.commands.append(f"1 0 0 1 {x:.2f} {y:.2f} Tm")
        self.commands.append(f"({pdf_escape(text)}) Tj")
        self.commands.append("ET")

    def centered_text(self, center_x: float, y: float, text: str, font: str, size: float, fill: tuple[float, float, float]) -> None:
        x = center_x - text_width(text, size) / 2.0
        self.text(x, y, text, font, size, fill)

    def render(self) -> bytes:
        content = "\n".join(self.commands).encode("latin-1")
        return f"<< /Length {len(content)} >>\nstream\n".encode("latin-1") + content + b"\nendstream"


@dataclass
class Page:
    canvas: Canvas
    number: int


class DocumentBuilder:
    def __init__(self) -> None:
        self.pages: list[Page] = []
        self.page_number = 0
        self.canvas = Canvas()
        self.y = PAGE_HEIGHT - TOP
        self.in_cover = False

    def new_page(self, cover: bool = False) -> None:
        if self.page_number > 0:
            self.pages.append(Page(self.canvas, self.page_number))
        self.page_number += 1
        self.canvas = Canvas()
        self.y = PAGE_HEIGHT - TOP
        self.in_cover = cover
        if not cover:
            self._draw_header_footer()

    def close(self) -> None:
        if self.page_number > 0:
            self.pages.append(Page(self.canvas, self.page_number))

    def _draw_header_footer(self) -> None:
        self.canvas.line(LEFT, PAGE_HEIGHT - 34, PAGE_WIDTH - RIGHT, PAGE_HEIGHT - 34, LINE, 0.8)
        self.canvas.text(LEFT, PAGE_HEIGHT - 28, "AT&T CTx CQE  |  Performance Engineering & Cybersecurity", "F3", 8.5, MID)
        self.canvas.line(LEFT, 30, PAGE_WIDTH - RIGHT, 30, LINE, 0.8)
        self.canvas.text(LEFT, 18, "Perf Test Agent v2.0 Integration / Implementation Plan", "F3", 8.5, MID)
        self.canvas.text(PAGE_WIDTH - RIGHT - 10, 18, str(self.page_number), "F1", 8.5, MID)
        self.y = PAGE_HEIGHT - 54

    def ensure(self, height: float) -> None:
        if self.y - height < BOTTOM:
            self.new_page()

    def spacer(self, amount: float) -> None:
        self.y -= amount

    def paragraph(self, text: str, size: float = 10.3, leading: float = 13.4, color: tuple[float, float, float] = TEXT) -> None:
        lines = wrap_text(text, size, CONTENT_WIDTH)
        needed = len(lines) * leading + 4
        self.ensure(needed)
        for line in lines:
            self.canvas.text(LEFT, self.y, line, "F1", size, color)
            self.y -= leading
        self.y -= 4

    def bullets(self, items: list[str], size: float = 10.1, leading: float = 13.2) -> None:
        for item in items:
            wrapped = wrap_text(item, size, CONTENT_WIDTH - 20)
            needed = len(wrapped) * leading + 2
            self.ensure(needed)
            bullet_y = self.y
            self.canvas.text(LEFT + 2, bullet_y, chr(149), "F1", size + 1, BLUE)
            for idx, line in enumerate(wrapped):
                self.canvas.text(LEFT + 14, self.y, line, "F1", size, TEXT)
                self.y -= leading
            self.y -= 2

    def heading1(self, text: str) -> None:
        self.ensure(34)
        self.y -= 4
        self.canvas.text(LEFT, self.y, text, "F2", 17, NAVY)
        self.y -= 7
        self.canvas.line(LEFT, self.y, PAGE_WIDTH - RIGHT, self.y, BLUE, 1.5)
        self.y -= 16

    def heading2(self, text: str) -> None:
        self.ensure(24)
        self.canvas.text(LEFT, self.y, text, "F2", 12.7, BLUE)
        self.y -= 16

    def small_note(self, text: str) -> None:
        lines = wrap_text(text, 9.1, CONTENT_WIDTH - 20)
        needed = len(lines) * 11.0 + 18
        self.ensure(needed)
        box_y = self.y - needed + 10
        self.canvas.rect(LEFT, box_y, CONTENT_WIDTH, needed - 6, LIGHT_FILL, LINE, 0.8)
        line_y = self.y - 12
        for line in lines:
            self.canvas.text(LEFT + 10, line_y, line, "F3", 9.1, NAVY)
            line_y -= 11
        self.y = box_y - 10

    def table(self, headers: list[str], rows: list[list[str]], col_widths: list[float], font_size: float = 9.1, leading: float = 11.2) -> None:
        total_width = sum(col_widths)
        scale = CONTENT_WIDTH / total_width
        widths = [w * scale for w in col_widths]

        def row_height(values: list[str]) -> float:
            max_lines = 1
            for value, width in zip(values, widths):
                lines = wrap_text(value, font_size, width - 10)
                max_lines = max(max_lines, len(lines))
            return max_lines * leading + 10

        header_h = row_height(headers) + 2
        self.ensure(header_h + 6)
        self._draw_row(headers, widths, header_h, True, font_size, leading)
        for idx, row in enumerate(rows):
            h = row_height(row)
            self.ensure(h + 2)
            self._draw_row(row, widths, h, False, font_size, leading, alt=(idx % 2 == 0))
        self.y -= 8

    def _draw_row(self, values: list[str], widths: list[float], height: float, header: bool, font_size: float, leading: float, alt: bool = False) -> None:
        fill = NAVY if header else (ALT_FILL if alt else WHITE)
        text_fill = WHITE if header else TEXT
        stroke = LINE
        x = LEFT
        y_bottom = self.y - height
        for value, width in zip(values, widths):
            self.canvas.rect(x, y_bottom, width, height, fill, stroke, 0.6)
            lines = wrap_text(value, font_size, width - 10)
            text_y = self.y - 12
            font = "F2" if header else "F1"
            for line in lines:
                self.canvas.text(x + 5, text_y, line, font, font_size, text_fill)
                text_y -= leading
            x += width
        self.y = y_bottom


def build_document() -> DocumentBuilder:
    doc = DocumentBuilder()
    doc.new_page(cover=True)
    c = doc.canvas

    c.centered_text(PAGE_WIDTH / 2, 700, "AT&T CTx CQE", "F2", 12, (0.000, 0.624, 0.859))
    c.centered_text(PAGE_WIDTH / 2, 682, "Performance Engineering & Cybersecurity", "F1", 10.5, MID)
    c.line(138, 638, 474, 638, (0.000, 0.624, 0.859), 1.8)
    c.centered_text(PAGE_WIDTH / 2, 644, "Project Implementation Plan", "F2", 24, NAVY)
    c.centered_text(PAGE_WIDTH / 2, 606, "Perf Test Agent v2.0", "F2", 20, BLUE)
    c.centered_text(PAGE_WIDTH / 2, 582, "Implementation and Live Integration Plan", "F3", 13, MID)

    meta_x = 128
    meta_y = 508
    row_h = 34
    left_w = 148
    right_w = 210
    metadata = [
        ("Document Version", "2.1.0  |  March 29, 2026"),
        ("Classification", "AT&T Internal  |  Confidential"),
        ("Prepared By", "Codex working draft for CTx CQE Performance Engineering"),
        ("Status", "DRAFT  |  Updated to reflect live integrations not yet implemented"),
    ]
    for idx, (label, value) in enumerate(metadata):
        y = meta_y - idx * row_h
        fill = LIGHT_FILL if idx % 2 == 0 else WHITE
        c.rect(meta_x, y, left_w, row_h, fill, LINE, 0.8)
        c.rect(meta_x + left_w, y, right_w, row_h, fill, LINE, 0.8)
        c.text(meta_x + 10, y + 12, label, "F2", 9.5, NAVY)
        c.text(meta_x + left_w + 10, y + 12, value, "F1", 9.5, TEXT)

    note = [
        "This revision supersedes the legacy status narrative.",
        "All live-tool integrations should currently be treated as implementation targets, not production-ready capabilities.",
        "Code-level clients and agent/tool scaffolding exist for several systems, but credentials, network connectivity, schema hardening, end-to-end workflows, and operational sign-off remain to be completed.",
    ]
    note_y = 292
    c.rect(82, note_y, 448, 102, LIGHT_FILL, LINE, 0.8)
    yy = note_y + 72
    for line in note:
        lines = wrap_text(line, 10.1, 420)
        for wrapped in lines:
            c.text(96, yy, wrapped, "F1", 10.1, TEXT)
            yy -= 13
        yy -= 2

    c.centered_text(PAGE_WIDTH / 2, 154, "Primary Objective", "F2", 11.5, BLUE)
    c.centered_text(
        PAGE_WIDTH / 2,
        136,
        "Deliver a phased, validated path from scaffolded code to production-grade enterprise integrations.",
        "F1",
        10.5,
        NAVY,
    )

    doc.new_page()
    doc.heading1("1. Executive Summary")
    doc.paragraph(
        "This document provides a detailed implementation and integration plan for Perf Test Agent v2.0 based on the current repository state on March 29, 2026. The plan intentionally updates the legacy implementation narrative to reflect a stricter and more realistic position: while the codebase contains multiple integration clients, LangChain tool wrappers, prompts, and phase models, the program should currently assume that live integrations with enterprise systems have not yet been implemented for production use."
    )
    doc.paragraph(
        "The path forward is therefore not a simple deployment exercise. It is an integration program consisting of foundation hardening, service-account and network enablement, schema and artifact migration into Snowflake, live-tool adapter completion, end-to-end non-production validation, human-in-the-loop workflow refinement, and controlled rollout. The recommended sequence is six waves so the team can prove value early without creating false confidence in unvalidated automation."
    )
    doc.table(
        ["Goal", "Target Outcome"],
        [
            ["Establish truthful current state", "Replace legacy 'active' integration assumptions with scaffold-only status until validated live connectivity exists."],
            ["Enable production-grade integrations", "Complete secure, testable adapters for Jira/iTrack, Azure AI Search, Snowflake, Dynatrace, LRE, Jenkins, Prometheus, ELK, SharePoint/MS Graph, and environment inspection tooling."],
            ["Build an institutional corpus", "Migrate existing scripts, test result documents, Jira/iTrack defects, reports, and related artifacts into Snowflake so the agent can use durable historical context."],
            ["Protect rollout quality", "Gate each wave with explicit entry/exit criteria, auditability, HITL checkpoints, and rollback options."],
        ],
        [150, 354],
    )
    doc.small_note(
        "Planning assumption used in this draft: implementation will proceed in lower environments first, then PERF, with enterprise IAM, network, and vendor dependencies resolved in parallel with application development."
    )

    doc.heading1("2. Current-State Alignment")
    doc.paragraph(
        "The repository already includes meaningful scaffolding: phase agents, prompts, Pydantic models, integration clients, a FastAPI surface, a React dashboard, Snowflake DDL, and environment reference assets. That is a strong starting point. The critical update is that these assets should be treated as design-time accelerators rather than evidence of live operational capability."
    )
    doc.table(
        ["Area", "Observed State in Repo", "Implementation Meaning"],
        [
            ["External clients", "Jira, Azure AI Search, Snowflake, Dynatrace, LRE, Jenkins, Prometheus, ELK, and SharePoint client classes exist.", "Reusable client scaffolding exists, but live authentication, permissions, endpoint validation, and operational reliability still need completion and proof."],
            ["Phase 3 live checks", "AKS and App Gateway tools explicitly return 'tool_not_connected'.", "Environment triage live inspection is not implemented yet."],
            ["Phase 4 data setup", "Bulk data provisioning tool returns 'placeholder'.", "Test-data creation orchestration is not implemented yet."],
            ["Phase 6 reporting", "Word report generation is placeholder text output.", "Formal report generation and publishing workflow is not implemented yet."],
            ["Phase 7 knowledge capture", "RAG lesson indexing returns placeholder success text.", "Knowledge-base writeback is not implemented yet."],
            ["Operational status", "No evidence in repo of validated end-to-end runs against live enterprise tools.", "Program should assume live integrations are pending until test evidence is produced."],
        ],
        [106, 180, 218],
    )
    doc.bullets(
        [
            "The legacy documentation should not be used as the source of truth for integration readiness.",
            "Every integration must pass both technical validation and operational ownership review before it is labeled active.",
            "Snowflake should become the durable system of record for migrated historical artifacts as well as future run outputs.",
        ]
    )

    doc.heading1("3. Delivery Principles")
    doc.bullets(
        [
            "Implement live integrations behind explicit feature flags and environment-aware configuration so scaffolded code can coexist with validated production pathways.",
            "Favor small vertical slices: one end-to-end path working in a lower environment is more valuable than many nominally complete adapters with no execution proof.",
            "Treat security, IAM, audit logging, data classification, and retention as first-class implementation tasks, not follow-up work.",
            "Persist every important artifact: prompts, tool calls, inputs, outputs, run metadata, defects, reports, and postmortem entries.",
            "Keep HITL gates in place until measurable trust thresholds are met for each phase and each live integration.",
        ]
    )

    doc.heading1("4. Workstream Plan")
    doc.heading2("4.1 Workstream A: Platform Foundation and Controls")
    doc.paragraph(
        "Before any live-tool cutover, the team should harden the runtime and operating model. This includes completing environment variable validation, secrets management, per-environment configuration overlays, structured audit events, correlation IDs across phases, retry/timeout policy standardization, feature flags for each integration, and a clear execution mode model distinguishing local development, integration test, PERF, and production-style runs."
    )
    doc.table(
        ["Task", "Key Actions", "Primary Deliverables"],
        [
            ["Configuration hardening", "Add startup validation for required settings by phase and integration; fail fast on incomplete config.", "Validated settings contract and deployment readiness checklist."],
            ["Secrets and identity", "Move credentials to approved secret stores, define service accounts, rotate tokens, and document least-privilege scopes.", "Per-system credential matrix and secret injection pattern."],
            ["Feature gating", "Introduce flags for read-only mode, create/update mode, and mock/live mode per integration.", "Controlled rollout switchboard with safe defaults."],
            ["Observability", "Standardize logging, latency metrics, error taxonomy, and trace IDs across client wrappers and agents.", "Runbook-ready telemetry and triage dashboard inputs."],
        ],
        [108, 230, 166],
    )

    doc.heading2("4.2 Workstream B: Core Pipeline Hardening")
    doc.paragraph(
        "The agent phases should be stabilized as application workflows rather than prompt experiments. The team should formalize state contracts between phases, add recovery behavior for partial failures, persist intermediate artifacts consistently, and create deterministic test fixtures for every phase. This work reduces risk before enterprise systems are introduced."
    )
    doc.bullets(
        [
            "Add regression tests for `PipelineState` transitions and per-phase output parsing.",
            "Capture agent prompts, tool inputs, tool outputs, and approval events in a structured artifact store.",
            "Normalize run directories and retention rules so executions are auditable and reproducible.",
            "Refine dashboard messaging to clearly distinguish simulated, scaffolded, and validated live actions.",
        ]
    )

    doc.heading2("4.3 Workstream C: Live Enterprise Integrations")
    doc.paragraph(
        "This workstream completes and validates each external integration. The common pattern is: confirm ownership and access, finalize data contracts, implement resilient adapter behavior, add lower-environment smoke tests, capture evidence, and only then enable agent tool usage beyond mock or read-only mode."
    )
    doc.table(
        ["Integration", "Current Interpretation", "Implementation Focus", "Exit Criteria"],
        [
            ["Jira / iTrack", "Client and tools exist; production create/update readiness unproven.", "Validate fields, issue types, linking model, comments, transitions, rate limits, and service-account permissions.", "Read and write flows succeed in non-prod and are approved by Jira/iTrack owners."],
            ["Azure AI Search RAG", "Retriever exists; corpus completeness and live query validation pending.", "Define index schema, chunking rules, source metadata, filters, and refresh cadence.", "Search results are relevant, attributable, and tied to governed source feeds."],
            ["Snowflake", "Connector and DDL exist; broader corpus model and migration pipeline pending.", "Finalize schemas for baselines, raw artifacts, curated dimensions, and lineage metadata.", "Historical artifacts and new execution outputs land reliably with queryable lineage."],
            ["Dynatrace", "Client exists; metric selectors and entity mapping need validation.", "Confirm management zones, service IDs, token scopes, thresholds, and alert interpretation.", "Known services return correct metrics and problem events for test windows."],
            ["LRE / JMeter / Jenkins", "Adapters exist in pieces; live orchestration not yet proven.", "Complete script packaging, job parameters, run polling, timeout handling, and result normalization.", "Automated test execution completes and returns normalized results for at least one validated flow."],
            ["Prometheus / ELK", "Monitoring clients exist; production query catalog not finalized.", "Define approved queries, labels, index patterns, and anomaly rules aligned to environments.", "Monitoring returns stable data during controlled executions with known alert behavior."],
            ["SharePoint / MS Graph", "Upload client exists; auth and destination contracts unvalidated.", "Confirm tenant auth, site/drive IDs, document paths, naming, and retention.", "Reports publish successfully with correct metadata and access controls."],
            ["AKS / App Gateway / infra checks", "Phase 3 checks are explicitly not connected.", "Implement real inspection adapters for deployments, config maps, replicas, and gateway routing.", "Golden config validation runs live and detects deliberate drift."],
            ["Solace / IBM MQ / Amdocs endpoints", "Modeled in prompts and references; adapter coverage incomplete.", "Implement or finalize protocol-specific checks, queue/topic inspection, and endpoint verification.", "Environment triage and execution support cover all in-scope dependency classes."],
        ],
        [86, 126, 170, 122],
        font_size=8.6,
        leading=10.4,
    )

    doc.heading2("4.4 Workstream D: Snowflake Corpus Migration")
    doc.paragraph(
        "A new migration step should be added explicitly to the implementation sequence so the system can learn from what already exists. The migration objective is to move historical performance artifacts into Snowflake in a way that preserves provenance, supports retrieval, and feeds both reporting and future model context. This is broader than execution baselines alone."
    )
    doc.table(
        ["Artifact Class", "Examples to Migrate", "Recommended Snowflake Landing Pattern"],
        [
            ["Scripts", "VuGen projects, JMeter JMX plans, helper utilities, parameter files.", "Raw file registry plus curated script dimension with application, protocol, author, and date metadata."],
            ["Test result documents", "Word reports, PDFs, spreadsheets, dashboards exported to files.", "Raw document table with extracted text, structured summary table, and lineage back to source path or SharePoint location."],
            ["Jira / iTrack defects", "Historical perf defects, linked stories, comments, resolution notes.", "Defect fact table plus linkage tables for systems, releases, runs, and remediation themes."],
            ["Run outputs", "CSV extracts, transaction summaries, throughput/error tables, screenshots.", "Execution fact tables with run IDs and transaction-level measures."],
            ["Knowledge artifacts", "Postmortems, lessons learned, architecture notes, environment references.", "Corpus tables with source metadata and optional downstream Azure AI Search indexing."],
        ],
        [110, 160, 234],
    )
    doc.bullets(
        [
            "Create a raw ingestion zone in Snowflake first so nothing is lost during early migration.",
            "Add metadata columns for source system, original location, checksum, artifact date, owning team, application, environment, confidentiality, and ingestion batch ID.",
            "Build a curation pass that extracts searchable text, normalizes key fields, and maps artifacts to runs, systems, stories, defects, and releases.",
            "Publish a corpus quality scorecard covering completeness, duplicate rate, extraction quality, and search usefulness.",
        ]
    )

    doc.heading2("4.5 Workstream E: Reporting, Publishing, and Knowledge Writeback")
    doc.paragraph(
        "Reporting should be treated as a product surface. Replace placeholder Word generation with a template-driven reporting service, standardize executive and engineering sections, and align SharePoint publication with naming, foldering, and retention policies. Likewise, lesson indexing should write to governed stores first and only then surface to retrieval systems."
    )
    doc.bullets(
        [
            "Implement a formal report renderer with reusable styles aligned to the existing document suite.",
            "Separate report generation from publication so validation can occur before documents are distributed.",
            "Record SharePoint publication events, URLs, and access outcomes in pipeline state and Snowflake.",
            "Treat RAG writeback as a governed ingest pipeline with approval, dedupe, and metadata enrichment.",
        ]
    )

    doc.heading2("4.6 Workstream F: Validation, Rollout, and Support")
    doc.paragraph(
        "Each wave should end with evidence, not only code completion. Required evidence includes smoke tests, live integration test logs, dashboard screenshots or output captures, defect records for failed scenarios, runbooks, rollback steps, and owner sign-off. Start with a single representative transaction family, then expand coverage incrementally."
    )
    doc.table(
        ["Validation Layer", "Required Evidence"],
        [
            ["Unit and contract", "Client adapter tests, payload validation, parser tests, and error-path coverage."],
            ["Integration", "Authenticated calls against lower environments, rate-limit behavior, and negative test results."],
            ["Scenario", "One end-to-end pipeline run with HITL gates and persisted artifacts."],
            ["Operational readiness", "Runbook, owner mapping, alert response path, rollback instructions, and support handoff."],
        ],
        [164, 340],
    )

    doc.heading1("5. Recommended Delivery Sequence")
    doc.table(
        ["Wave", "Scope", "Primary Outcome"],
        [
            ["Wave 1", "Foundation, feature flags, settings validation, audit logging, and Snowflake schema finalization.", "Safe base to connect live systems without uncontrolled behavior."],
            ["Wave 2", "Snowflake corpus migration MVP plus Azure AI Search / RAG read path validation.", "Historical knowledge becomes queryable and useful to planning phases."],
            ["Wave 3", "Jira/iTrack, Dynatrace, Prometheus, and ELK live read validation; dashboard status clarity.", "Phases 1, 2, and monitoring inputs become trustworthy."],
            ["Wave 4", "AKS/App Gateway live checks, test-data provisioning, and execution adapters for Jenkins/LRE/JMeter.", "Phases 3 through 5 can operate in controlled lower-environment slices."],
            ["Wave 5", "Formal reporting, SharePoint publish, and governed postmortem / lesson writeback.", "Phases 6 and 7 move from placeholder to operationally useful."],
            ["Wave 6", "PERF cutover rehearsal, support model, rollback drills, and phased expansion of application coverage.", "Production-style operating model with evidence-backed trust."],
        ],
        [70, 250, 184],
    )
    doc.small_note(
        "If staffing is constrained, do not parallelize too aggressively. The highest-value path is Snowflake corpus + RAG + Jira read path + one validated execution slice."
    )

    doc.heading1("6. Phase-by-Phase Implementation Updates")
    doc.table(
        ["Phase", "Legacy Expectation", "Updated Implementation Guidance"],
        [
            ["1. Story Analysis", "Assumed ready access to Jira, RAG, Snowflake, and Dynatrace.", "Keep phase operational in read-mostly mode first. Validate story fetch, corpus retrieval, baseline queries, and service discovery independently before combining them."],
            ["2. Test Planning", "Assumed calibrated workload generation and Jira artifact creation.", "Use curated templates and historical corpus first. Enable Jira write actions only after field mappings and governance are approved."],
            ["3. Env Triage", "Assumed live AKS/App Gateway inspection and golden-config persistence.", "Treat this phase as not live yet. Implement real infra inspection adapters, then prove drift detection with injected mismatches."],
            ["4. Script and Data", "Assumed generated scripts plus automated data provisioning.", "Script generation scaffolding exists, but automated bulk data setup remains placeholder. Prioritize one supported provisioning path per system family."],
            ["5. Execution", "Assumed live LRE/Jenkins execution with monitoring.", "Build one reliable execution path end to end. Normalize run IDs, timing, and result payloads before widening scenario coverage."],
            ["6. Reporting", "Assumed formatted Word output and SharePoint publication.", "Replace placeholder report generation and validate document lifecycle separately from pipeline reasoning."],
            ["7. Postmortem", "Assumed Snowflake archival and searchable lesson indexing.", "Archive structured data first; enable RAG writeback only after governance, dedupe, and source attribution controls are in place."],
        ],
        [62, 160, 282],
        font_size=8.7,
        leading=10.6,
    )

    doc.heading1("7. Risk Register")
    doc.table(
        ["Risk", "Impact", "Mitigation"],
        [
            ["Treating scaffolded clients as production-ready integrations", "False readiness signal, failed demos, and loss of trust.", "Use explicit readiness states: scaffolded, connected, validated, operational."],
            ["IAM and network approvals lag implementation", "Blocked schedules and partially testable code.", "Track access dependencies as first-class milestones with named owners."],
            ["Historical artifacts are not migrated early", "Weak RAG quality and poor baseline accuracy.", "Start Snowflake corpus migration in Wave 2 and measure coverage weekly."],
            ["Schema drift across tools and reports", "Broken parsers, inconsistent analytics, and report defects.", "Define versioned contracts and add contract tests for each adapter."],
            ["Over-automation before HITL workflows mature", "Operational risk and hard-to-debug outcomes.", "Keep approval gates and clear simulation/live labels until trust metrics are met."],
        ],
        [168, 132, 204],
    )

    doc.heading1("8. Exit Criteria")
    doc.bullets(
        [
            "At least one representative pipeline path completes end to end in a lower environment using live reads and the approved execution path.",
            "Snowflake contains migrated historical artifacts and current run outputs with lineage back to their original sources.",
            "Each live integration has a named owner, credential model, smoke test, runbook, and operational sign-off.",
            "Dashboard and artifacts clearly distinguish simulated behavior from validated live actions.",
            "Reporting and postmortem outputs are publishable, attributable, and retained under the agreed governance model.",
        ]
    )

    doc.heading1("9. Additional Information Requested")
    doc.paragraph(
        "This draft is complete enough to guide implementation sequencing now, but the next revision would be materially stronger with a few project-specific inputs from the team."
    )
    doc.bullets(
        [
            "Priority order of integrations for the first live slice, especially whether Jira, Snowflake corpus migration, or execution tooling should go first.",
            "Known enterprise constraints: approved service accounts, lower-environment endpoints, network segments, and any vendor or firewall blockers.",
            "Preferred Snowflake information model if one already exists for raw files, curated facts, and searchable corpus text.",
            "The authoritative list of legacy artifact sources to migrate: repositories, SharePoint libraries, Jira/iTrack projects, result folders, and report archives.",
            "Target audience for the generated reports so formatting, summary depth, and publication workflow can be tuned appropriately.",
        ]
    )

    doc.close()
    return doc


def write_pdf(doc: DocumentBuilder, path: Path) -> int:
    writer = PDFWriter()
    font_obj = writer.add_object(
        "<< /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> "
        "/F2 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >> "
        "/F3 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Oblique >> >> >>"
    )

    page_ids: list[int] = []
    content_ids: list[int] = []
    pages_placeholder = writer.add_object("<< >>")

    for page in doc.pages:
        content_id = writer.add_object(page.canvas.render())
        content_ids.append(content_id)
        page_ids.append(
            writer.add_object(
                dedent(
                    f"""
                    << /Type /Page
                       /Parent {pages_placeholder} 0 R
                       /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}]
                       /Resources {font_obj} 0 R
                       /Contents {content_id} 0 R
                    >>
                    """
                ).strip()
            )
        )

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    writer.objects[pages_placeholder - 1] = (
        f"<< /Type /Pages /Count {len(page_ids)} /Kids [ {kids} ] >>".encode("latin-1")
    )

    root_id = writer.add_object("<< /Type /Catalog /Pages " + f"{pages_placeholder} 0 R >>")
    info_id = writer.add_object(
        f"<< /Title ({pdf_escape('Perf Test Agent v2.0 Implementation and Live Integration Plan')}) "
        f"/Author ({pdf_escape('OpenAI Codex')}) "
        f"/Producer ({pdf_escape('Custom Python PDF generator')}) "
        f"/CreationDate (D:{date.today().strftime('%Y%m%d')}000000) >>"
    )
    pdf = writer.build(root_id, info_id)
    path.write_bytes(pdf)
    return len(page_ids)


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    doc = build_document()
    page_count = write_pdf(doc, OUTPUT_PATH)
    print(f"Wrote {OUTPUT_PATH} ({page_count} pages)")


if __name__ == "__main__":
    main()
