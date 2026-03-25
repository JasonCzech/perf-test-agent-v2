"""Base agent infrastructure for the ReAct pipeline.

All phase agents inherit from BaseAgent, which provides:
- Azure OpenAI LLM initialization with task-appropriate model selection
- Tool registration and ReAct agent creation via LangChain
- Structured output parsing (JSON -> Pydantic)
- Retry logic and error handling
- Structured logging
- Artifact persistence
"""
from __future__ import annotations

import json
import re
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Generic, Optional, Type, TypeVar

try:
    # LangChain >=1.0 moved classic ReAct agent APIs.
    from langchain_classic.agents import AgentExecutor, create_react_agent
except ImportError:  # pragma: no cover - compatibility fallback
    from langchain.agents import AgentExecutor, create_react_agent

try:
    from langchain_core.prompts import PromptTemplate
except ImportError:  # pragma: no cover - compatibility fallback
    from langchain.prompts import PromptTemplate
from langchain_core.tools import BaseTool
from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config.settings import LLMTask, Settings, get_settings
from src.models.pipeline_state import (
    PhaseResult,
    PhaseStatus,
    PipelinePhase,
    PipelineState,
)
from src.utils.logging import get_logger

T = TypeVar("T", bound=BaseModel)


class BaseAgent(ABC, Generic[T]):
    """Abstract base class for all pipeline phase agents.

    Each agent:
    1. Receives PipelineState as input
    2. Runs a ReAct loop with registered tools
    3. Produces a typed output (Pydantic model)
    4. Updates PipelineState and returns it
    """

    # Subclasses must set these
    phase: PipelinePhase
    output_model: Type[T]
    llm_task: LLMTask = LLMTask.COMPLEX_REASONING

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.log = get_logger(self.__class__.__name__)
        self.run_dir: Optional[Path] = None
        self._llm: Optional[AzureChatOpenAI] = None
        self._agent_executor: Optional[AgentExecutor] = None

    _SENSITIVE_KEYS = (
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "session",
    )

    # ── LLM ───────────────────────────────────────────────────────────

    @property
    def llm(self) -> AzureChatOpenAI:
        if self._llm is None:
            deployment = self.settings.get_llm_deployment(self.llm_task)
            max_tokens = self.settings.get_llm_max_tokens(self.llm_task)
            self._llm = AzureChatOpenAI(
                azure_endpoint=self.settings.azure_openai_endpoint,
                api_key=self.settings.azure_openai_api_key,
                api_version=self.settings.azure_openai_api_version,
                azure_deployment=deployment,
                max_tokens=max_tokens,
                temperature=0.1,  # Low temp for consistency
            )
            self.log.info("llm_initialized", deployment=deployment, max_tokens=max_tokens)
        return self._llm

    # ── Tools ─────────────────────────────────────────────────────────

    @abstractmethod
    def get_tools(self) -> list[BaseTool]:
        """Return the tools available to this agent."""
        ...

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the system prompt for this agent."""
        ...

    # ── ReAct Agent ───────────────────────────────────────────────────

    def _build_agent_executor(self, system_prompt_override: Optional[str] = None) -> AgentExecutor:
        """Build the LangChain ReAct agent with tools."""
        tools = self.get_tools()
        tool_names = [t.name for t in tools]

        # Standard ReAct prompt template
        base_system_prompt = system_prompt_override or self.get_system_prompt()

        react_prompt = PromptTemplate.from_template(
            base_system_prompt + """

You have access to the following tools:
{tools}

Use the following format:

Thought: I need to think about what to do next
Action: the action to take, should be one of [{tool_names}]
Action Input: the input to the action
Observation: the result of the action
... (this Thought/Action/Action Input/Observation can repeat N times)
Thought: I now have enough information to produce the final answer
Final Answer: the final answer (must be valid JSON matching the output schema)

Begin!

Question: {{input}}
Thought: {{agent_scratchpad}}"""
        )

        agent = create_react_agent(
            llm=self.llm,
            tools=tools,
            prompt=react_prompt,
        )

        return AgentExecutor(
            agent=agent,
            tools=tools,
            verbose=True,
            max_iterations=15,
            handle_parsing_errors=True,
            return_intermediate_steps=True,
        )

    @property
    def agent_executor(self) -> AgentExecutor:
        if self._agent_executor is None:
            self._agent_executor = self._build_agent_executor()
        return self._agent_executor

    # ── Execution ─────────────────────────────────────────────────────

    async def run(self, state: PipelineState) -> PipelineState:
        """Execute this agent's phase.

        1. Set up run directory and phase tracking
        2. Build the input prompt from pipeline state
        3. Run the ReAct agent loop
        4. Parse structured output
        5. Update pipeline state
        """
        self.run_dir = Path(self.settings.pipeline_run_dir) / state.run_id / self.phase.value
        self.run_dir.mkdir(parents=True, exist_ok=True)

        phase_result = PhaseResult(
            phase=self.phase,
            status=PhaseStatus.RUNNING,
            started_at=datetime.utcnow(),
        )

        self.log.info("phase_starting", phase=self.phase.value, run_id=state.run_id)

        try:
            # Build the input from pipeline state
            agent_input = self.build_agent_input(state)

            # Optional run-scoped context for current phase only.
            phase_context = (state.phase_pre_execution_context or {}).get(self.phase.value, "").strip()
            if phase_context:
                agent_input = (
                    f"{agent_input}\n\n"
                    "HITL pre-execution context for current phase:\n"
                    f"{phase_context}"
                )

            phase_prompt_override = (state.phase_prompt_overrides or {}).get(self.phase.value, "").strip()

            # Run the ReAct loop with run-scoped prompt override when present.
            if phase_prompt_override:
                override_executor = self._build_agent_executor(system_prompt_override=phase_prompt_override)
                result = await self._execute_agent(agent_input, executor=override_executor)
            else:
                result = await self._execute_agent(agent_input)

            # Parse structured output
            output = self.parse_output(result)

            reasoning_trace, tool_calls_summary = self._extract_reasoning_trace(result)

            # Update pipeline state with phase output
            state = self.update_state(state, output)

            # Save artifacts
            self._save_artifacts(output)

            phase_result.status = PhaseStatus.AWAITING_APPROVAL
            phase_result.completed_at = datetime.utcnow()
            phase_result.duration_seconds = (
                phase_result.completed_at - phase_result.started_at
            ).total_seconds()
            phase_result.summary = self.summarize_output(output)
            phase_result.artifacts["effective_prompt"] = phase_prompt_override or self.get_system_prompt()
            if phase_context:
                phase_result.artifacts["pre_execution_context"] = phase_context
            phase_result.reasoning_trace = reasoning_trace
            phase_result.tool_calls_summary = tool_calls_summary

            self.log.info(
                "phase_completed",
                phase=self.phase.value,
                duration_s=phase_result.duration_seconds,
            )

        except Exception as e:
            phase_result.status = PhaseStatus.FAILED
            phase_result.errors.append(str(e))
            phase_result.completed_at = datetime.utcnow()
            self.log.error("phase_failed", phase=self.phase.value, error=str(e))

        state.set_phase_result(phase_result)
        return state

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
    async def _execute_agent(
        self,
        agent_input: str,
        executor: Optional[AgentExecutor] = None,
    ) -> dict[str, Any]:
        """Execute the ReAct agent with retry logic."""
        run_executor = executor or self.agent_executor
        return await run_executor.ainvoke({"input": agent_input})

    # ── Abstract Methods for Subclasses ───────────────────────────────

    @abstractmethod
    def build_agent_input(self, state: PipelineState) -> str:
        """Build the input prompt from the current pipeline state."""
        ...

    @abstractmethod
    def parse_output(self, agent_result: dict[str, Any]) -> T:
        """Parse the agent's final answer into the typed output model."""
        ...

    @abstractmethod
    def update_state(self, state: PipelineState, output: T) -> PipelineState:
        """Update the pipeline state with this phase's output."""
        ...

    @abstractmethod
    def summarize_output(self, output: T) -> str:
        """Produce a human-readable summary of the output for HITL review."""
        ...

    # ── Utilities ─────────────────────────────────────────────────────

    def _save_artifacts(self, output: T) -> None:
        """Persist the phase output to the run directory."""
        if self.run_dir:
            output_path = self.run_dir / "output.json"
            output_path.write_text(output.model_dump_json(indent=2))
            self.log.info("artifacts_saved", path=str(output_path))

    @staticmethod
    def _compact_text(value: str, limit: int = 220) -> str:
        compact = " ".join(str(value or "").split())
        if len(compact) <= limit:
            return compact
        return f"{compact[: limit - 3]}..."

    def _sanitize_key_value(self, key: str, value: Any) -> Any:
        lowered = key.lower()
        if any(tag in lowered for tag in self._SENSITIVE_KEYS):
            return "[redacted]"
        return self._sanitize_value(value)

    def _sanitize_value(self, value: Any, depth: int = 0) -> Any:
        if depth > 3:
            return "[truncated]"

        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for idx, (k, v) in enumerate(value.items()):
                if idx >= 20:
                    sanitized["_truncated_keys"] = "[truncated]"
                    break
                sanitized[str(k)] = self._sanitize_key_value(str(k), v)
            return sanitized

        if isinstance(value, list):
            if len(value) > 12:
                return [self._sanitize_value(v, depth + 1) for v in value[:12]] + ["[truncated]"]
            return [self._sanitize_value(v, depth + 1) for v in value]

        if isinstance(value, str):
            return self._compact_text(value)

        if isinstance(value, (int, float, bool)) or value is None:
            return value

        return self._compact_text(str(value))

    def _parse_tool_input(self, tool_input: Any) -> Any:
        if isinstance(tool_input, str):
            parsed = tool_input
            try:
                parsed = json.loads(tool_input)
            except Exception:
                parsed = tool_input
            return self._sanitize_value(parsed)
        return self._sanitize_value(tool_input)

    def _extract_thought_summary(self, action_log: str) -> str:
        if not action_log:
            return ""
        match = re.search(r"Thought:\s*(.*?)(?:\nAction:|$)", action_log, re.DOTALL)
        if not match:
            return ""
        return self._compact_text(match.group(1), limit=180)

    def _extract_reasoning_trace(
        self,
        agent_result: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        raw_steps = agent_result.get("intermediate_steps") if isinstance(agent_result, dict) else None
        if not isinstance(raw_steps, list) or not raw_steps:
            return [], {}

        reasoning_trace: list[dict[str, Any]] = []
        tool_calls_summary: dict[str, int] = {}

        for idx, step in enumerate(raw_steps, start=1):
            if not isinstance(step, (list, tuple)) or len(step) < 2:
                continue

            action = step[0]
            observation = step[1]
            action_name = getattr(action, "tool", "unknown_tool") or "unknown_tool"
            tool_calls_summary[action_name] = tool_calls_summary.get(action_name, 0) + 1

            action_input = self._parse_tool_input(getattr(action, "tool_input", {}))
            thought_summary = self._extract_thought_summary(getattr(action, "log", ""))
            observation_summary = self._compact_text(str(observation), limit=260)

            item: dict[str, Any] = {
                "step": idx,
                "action": action_name,
                "action_input": action_input,
                "observation_summary": observation_summary,
            }
            if thought_summary:
                item["thought_summary"] = thought_summary

            reasoning_trace.append(item)

        return reasoning_trace, tool_calls_summary

    def _parse_json_output(self, raw: str, model: Type[T]) -> T:
        """Parse JSON from agent output, handling common LLM formatting issues."""
        # Strip markdown code blocks if present
        cleaned = raw.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        data = json.loads(cleaned)
        return model.model_validate(data)

    @staticmethod
    def generate_id(prefix: str = "") -> str:
        """Generate a short unique ID."""
        short = uuid.uuid4().hex[:8]
        return f"{prefix}{short}" if prefix else short
