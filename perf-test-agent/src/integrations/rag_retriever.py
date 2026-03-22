"""RAG Retriever — Azure AI Search integration.

Queries the pre-indexed Microsoft Graph content (SharePoint wikis, PowerBI
reports, ServiceNow records) to enrich test case extraction and planning.

The indexing is handled by Microsoft Graph connectors — this client only
performs retrieval via Azure AI Search.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config.settings import Settings, get_settings
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class RAGDocument:
    """A document retrieved from the RAG index."""
    doc_id: str
    title: str
    content: str
    source: str  # "sharepoint" | "powerbi" | "servicenow" | "confluence"
    url: str
    score: float
    metadata: dict[str, Any]


class RAGRetriever:
    """Azure AI Search retriever for enterprise knowledge base.

    Queries the pre-indexed Microsoft Graph content to find:
    - Solution intent documentation
    - Architecture diagrams and specs
    - Past performance test reports
    - ServiceNow incidents related to performance
    - PowerBI dashboards and metrics
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.endpoint = self.settings.azure_search_endpoint.rstrip("/")
        self.index = self.settings.azure_search_index
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.endpoint,
                headers={
                    "Content-Type": "application/json",
                    "api-key": self.settings.azure_search_key,
                },
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def search(
        self,
        query: str,
        top: int = 10,
        filters: Optional[str] = None,
        semantic: bool = True,
    ) -> list[RAGDocument]:
        """Search the enterprise knowledge base.

        Args:
            query: Natural language search query.
            top: Maximum results to return.
            filters: OData filter expression (e.g., "source eq 'sharepoint'").
            semantic: Use semantic ranking if available.
        """
        body: dict[str, Any] = {
            "search": query,
            "top": top,
            "select": "id,title,content,source,url,metadata",
            "queryType": "semantic" if semantic else "simple",
        }
        if semantic:
            body["semanticConfiguration"] = "default"
        if filters:
            body["filter"] = filters

        resp = await self.client.post(
            f"/indexes/{self.index}/docs/search?api-version=2024-07-01",
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

        docs = []
        for result in data.get("value", []):
            docs.append(RAGDocument(
                doc_id=result.get("id", ""),
                title=result.get("title", ""),
                content=result.get("content", "")[:4000],  # Truncate for context window
                source=result.get("source", "unknown"),
                url=result.get("url", ""),
                score=result.get("@search.score", 0.0),
                metadata=result.get("metadata", {}),
            ))

        log.info("rag_search_complete", query=query[:60], results=len(docs))
        return docs

    async def search_for_story(self, story_key: str, story_summary: str) -> list[RAGDocument]:
        """Search RAG for content related to a specific Jira story.

        Combines the story key and summary for a targeted search, looking
        for solution intent docs, architecture specs, and past incidents.
        """
        query = f"{story_key} {story_summary} performance testing requirements"
        return await self.search(query, top=5)

    async def search_past_incidents(self, system_name: str) -> list[RAGDocument]:
        """Search for past performance incidents related to a system."""
        query = f"{system_name} performance incident degradation timeout"
        return await self.search(
            query,
            top=5,
            filters="source eq 'servicenow'",
        )

    async def search_architecture_docs(self, system_name: str) -> list[RAGDocument]:
        """Search for architecture documentation for a system."""
        query = f"{system_name} architecture design specification API"
        return await self.search(query, top=5)

    async def search_test_history(self, transaction_name: str) -> list[RAGDocument]:
        """Search for past performance test reports mentioning a transaction."""
        query = f"performance test results {transaction_name} response time throughput"
        return await self.search(query, top=3)
