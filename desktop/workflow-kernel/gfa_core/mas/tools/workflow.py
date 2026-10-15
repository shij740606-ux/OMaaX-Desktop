from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from gfa_core.mas.tools.executor import ExecutionResult, ExecutionStatus, ToolExecutor


class WorkflowStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class WorkflowStep:
    step_id: str
    tool_id: str
    name: str
    inputs: dict[str, str] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    result: ExecutionResult | None = None


@dataclass
class WorkflowResult:
    workflow_id: str
    status: WorkflowStatus
    steps: list[WorkflowStep] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class GeneFamilyWorkflow:
    def __init__(self, executor: ToolExecutor | None = None):
        self.executor = executor or ToolExecutor()

    def create_identification_workflow(
        self,
        hmm_file: str,
        genome_file: str,
        e_value: float = 1e-5,
        species_name: str = "unknown",
    ) -> list[WorkflowStep]:
        return [
            WorkflowStep(
                step_id="hmmsearch",
                tool_id="hmmsearch",
                name="HMM搜索鉴定",
                inputs={
                    "query": hmm_file,
                    "target": genome_file,
                },
                parameters={
                    "e_value": e_value,
                    "cpu": 4,
                },
                dependencies=[],
            ),
        ]

    def create_full_analysis_workflow(
        self,
        hmm_file: str,
        genome_file: str,
        protein_file: str,
        e_value: float = 1e-5,
        species_name: str = "unknown",
    ) -> list[WorkflowStep]:
        return [
            WorkflowStep(
                step_id="hmmsearch",
                tool_id="hmmsearch",
                name="HMM搜索鉴定",
                inputs={
                    "query": hmm_file,
                    "target": protein_file,
                },
                parameters={
                    "e_value": e_value,
                    "cpu": 4,
                },
                dependencies=[],
            ),
            WorkflowStep(
                step_id="mafft",
                tool_id="mafft",
                name="多序列比对",
                inputs={
                    "sequences": "",  # Will be filled from hmmsearch output
                },
                parameters={
                    "strategy": "auto",
                    "maxiterate": 1000,
                },
                dependencies=["hmmsearch"],
            ),
            WorkflowStep(
                step_id="iqtree",
                tool_id="iqtree",
                name="系统发育树构建",
                inputs={
                    "alignment": "",  # Will be filled from mafft output
                },
                parameters={
                    "model": "MFP",
                    "bootstrap": 1000,
                    "threads": 4,
                },
                dependencies=["mafft"],
            ),
        ]

    async def run_workflow(
        self,
        steps: list[WorkflowStep],
        workflow_id: str = "",
    ) -> WorkflowResult:
        workflow_id = workflow_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        started_at = datetime.now()
        
        completed_steps: dict[str, WorkflowStep] = {}
        outputs: dict[str, str] = {}

        for step in steps:
            step_inputs = dict(step.inputs)
            
            for dep_id in step.dependencies:
                if dep_id in completed_steps:
                    dep_step = completed_steps[dep_id]
                    if dep_step.result and dep_step.result.outputs:
                        for output_name, output_path in dep_step.result.outputs.items():
                            if not step_inputs.get(output_name):
                                step_inputs[output_name] = output_path

            result = await self.executor.aexecute(
                step.tool_id,
                step_inputs,
                step.parameters,
                work_subdir=f"{workflow_id}/{step.step_id}",
            )
            
            step.result = result
            completed_steps[step.step_id] = step
            
            if result.status == ExecutionStatus.COMPLETED:
                outputs[step.step_id] = str(result.outputs)
            elif result.status in [ExecutionStatus.FAILED, ExecutionStatus.TIMEOUT]:
                return WorkflowResult(
                    workflow_id=workflow_id,
                    status=WorkflowStatus.FAILED,
                    steps=list(completed_steps.values()),
                    outputs=outputs,
                    started_at=started_at,
                    completed_at=datetime.now(),
                    error=f"Step {step.step_id} failed: {result.error or result.stderr}",
                )

        return WorkflowResult(
            workflow_id=workflow_id,
            status=WorkflowStatus.COMPLETED,
            steps=list(completed_steps.values()),
            outputs=outputs,
            started_at=started_at,
            completed_at=datetime.now(),
        )

    def get_available_tools(self) -> list[dict]:
        tools = self.executor.list_tools()
        return [
            {
                "id": t.id,
                "name": t.name,
                "category": t.category,
                "executable": t.executable,
                "docker_image": t.docker_image,
            }
            for t in tools
        ]
