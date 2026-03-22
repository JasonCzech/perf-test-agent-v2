"""Phase 1: Story Analysis models.

Defines the structured output of test case extraction from Jira stories,
solution intent docs, and RAG-retrieved enterprise documentation.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from .pipeline_state import Protocol, RiskLevel, SLATarget, SystemTier, TestHarness


class TransactionType(str, Enum):
    QUERY = "query"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    ASYNC_EVENT = "async_event"
    BATCH = "batch"
    AUTHENTICATION = "authentication"


class DataRequirement(BaseModel):
    """Bulk data needed for a test case."""
    entity_type: str = Field(description="e.g. 'customer_account', 'order', 'billing_record'")
    quantity: int = Field(description="Number of records needed")
    attributes: dict[str, str] = Field(default_factory=dict, description="Key attribute constraints")
    creation_method: str = Field(description="sql_insert | api_call | data_tool | manual")
    source_system: str = ""


class TransactionFlow(BaseModel):
    """A single transaction flow to be performance tested."""
    name: str = Field(description="e.g. 'Create_New_Order'")
    description: str
    transaction_type: TransactionType
    protocol: Protocol
    entry_system: str = Field(description="Frontend system where flow originates")
    systems_involved: list[str] = Field(description="All systems touched in the flow")
    api_endpoints: list[str] = Field(default_factory=list)
    expected_sequence: list[str] = Field(
        default_factory=list,
        description="Ordered list of system calls: ['IDP -> CSI', 'CSI -> TLG', 'TLG -> BSSe']"
    )
    has_async_component: bool = False
    mq_topics: list[str] = Field(default_factory=list, description="Solace/MQ topics if async")


class TestCase(BaseModel):
    """A performance test case extracted from a Jira story."""
    id: str = Field(description="Auto-generated: TC-{story_key}-{sequence}")
    source_story_key: str
    source_story_summary: str
    title: str
    description: str
    transaction_flows: list[TransactionFlow]
    sla_targets: list[SLATarget]
    risk_level: RiskLevel = RiskLevel.MEDIUM
    risk_rationale: str = ""
    recommended_harness: TestHarness = TestHarness.LOADRUNNER
    harness_rationale: str = ""
    recommended_protocol: Protocol = Protocol.REST_JSON
    data_requirements: list[DataRequirement] = []
    preconditions: list[str] = []
    tags: list[str] = []

    # Populated by RAG enrichment
    related_incidents: list[str] = Field(
        default_factory=list,
        description="ServiceNow/Jira incident keys from similar past issues"
    )
    historical_baselines: dict[str, float] = Field(
        default_factory=dict,
        description="Transaction name -> historical p90 response time from Snowflake"
    )


class StoryAnalysisOutput(BaseModel):
    """Complete output of Phase 1: Story Analysis."""
    analyzed_stories: list[str] = Field(description="Jira story keys that were analyzed")
    test_cases: list[TestCase]
    sla_targets: list[SLATarget]
    risk_summary: str
    rag_documents_consulted: list[str] = []
    confidence_score: float = Field(ge=0.0, le=1.0, description="Agent's confidence in extraction")
    warnings: list[str] = []
    suggested_followups: list[str] = Field(
        default_factory=list,
        description="Questions the agent couldn't resolve autonomously"
    )
