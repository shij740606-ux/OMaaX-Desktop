import asyncio
import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from gfa_core.mas.tools.registry import ToolMetadata, ToolRegistry, create_default_registry


class ExecutionStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class ExecutionResult:
    tool_id: str
    status: ExecutionStatus
    return_code: int = 0
    stdout: str = ""
    stderr: str = ""
    outputs: dict[str, str] = field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry | None = None,
        work_dir: str = "/tmp/gene-family-tools",
        use_docker: bool = False,
        simulation_mode: bool = False,
    ):
        self.registry = registry or create_default_registry()
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.use_docker = use_docker
        self.simulation_mode = simulation_mode

    def get_tool(self, tool_id: str) -> ToolMetadata | None:
        return self.registry.get(tool_id)

    def list_tools(self, category: str | None = None) -> list[ToolMetadata]:
        if category:
            return self.registry.list_by_category(category)
        return self.registry.list_all()

    def execute(
        self,
        tool_id: str,
        inputs: dict[str, str],
        parameters: dict[str, Any] | None = None,
        work_subdir: str | None = None,
    ) -> ExecutionResult:
        tool = self.registry.get(tool_id)
        if not tool:
            return ExecutionResult(
                tool_id=tool_id,
                status=ExecutionStatus.FAILED,
                error=f"Tool not found: {tool_id}",
            )

        parameters = parameters or {}
        work_path = self.work_dir / (work_subdir or tool_id)
        work_path.mkdir(parents=True, exist_ok=True)

        started_at = datetime.now()

        if self.simulation_mode:
            return self._simulate_execution(tool, inputs, parameters, work_path, started_at)

        try:
            if self.use_docker and tool.docker_image:
                cmd = self._build_docker_command(tool, inputs, parameters, work_path)
            else:
                cmd = self._build_command(tool, inputs, parameters, work_path)

            result = subprocess.run(
                cmd,
                cwd=str(work_path),
                capture_output=True,
                text=True,
                timeout=tool.timeout,
            )

            if tool.id == "mafft" and result.returncode == 0:
                mafft_output = work_path / "aligned.fasta"
                with open(mafft_output, "w") as f:
                    f.write(result.stdout)

            outputs = self._collect_outputs(tool, work_path)

            status = ExecutionStatus.COMPLETED if result.returncode == 0 else ExecutionStatus.FAILED

            return ExecutionResult(
                tool_id=tool_id,
                status=status,
                return_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                outputs=outputs,
                started_at=started_at,
                completed_at=datetime.now(),
            )

        except subprocess.TimeoutExpired:
            return ExecutionResult(
                tool_id=tool_id,
                status=ExecutionStatus.TIMEOUT,
                error=f"Tool timed out after {tool.timeout}s",
                started_at=started_at,
                completed_at=datetime.now(),
            )
        except FileNotFoundError:
            return ExecutionResult(
                tool_id=tool_id,
                status=ExecutionStatus.FAILED,
                error=f"Executable not found: {tool.executable}",
                started_at=started_at,
                completed_at=datetime.now(),
            )
        except Exception as e:
            return ExecutionResult(
                tool_id=tool_id,
                status=ExecutionStatus.FAILED,
                error=str(e),
                started_at=started_at,
                completed_at=datetime.now(),
            )

    async def aexecute(
        self,
        tool_id: str,
        inputs: dict[str, str],
        parameters: dict[str, Any] | None = None,
        work_subdir: str | None = None,
    ) -> ExecutionResult:
        return await asyncio.to_thread(
            self.execute, tool_id, inputs, parameters, work_subdir
        )

    def _build_command(
        self,
        tool: ToolMetadata,
        inputs: dict[str, str],
        parameters: dict[str, Any],
        work_path: Path,
    ) -> list[str]:
        cmd = [tool.executable]

        if tool.id == "hmmsearch":
            output_tbl = work_path / "output.tbl"
            dom_tbl = work_path / "dom.tbl"
            cmd.extend([
                "--tblout", str(output_tbl),
                "--domtblout", str(dom_tbl),
                "-E", str(parameters.get("e_value", 1e-5)),
                "--cpu", str(parameters.get("cpu", 4)),
                inputs.get("query", ""),
                inputs.get("target", ""),
            ])

        elif tool.id == "blastp":
            output_file = work_path / "blast_results.txt"
            cmd.extend([
                "-query", inputs.get("query", ""),
                "-db", inputs.get("db", ""),
                "-evalue", str(parameters.get("e_value", 1e-5)),
                "-max_target_seqs", str(parameters.get("max_target_seqs", 500)),
                "-outfmt", str(parameters.get("outfmt", 6)),
                "-out", str(output_file),
            ])

        elif tool.id == "mafft":
            strategy = parameters.get("strategy", "auto")
            if strategy != "auto":
                cmd.append(f"--{strategy}")
            cmd.extend([
                "--maxiterate", str(parameters.get("maxiterate", 1000)),
                "--reorder",
                "--thread", str(parameters.get("threads", 4)),
                inputs.get("sequences", ""),
            ])

        elif tool.id == "iqtree":
            cmd.extend([
                "-s", inputs.get("alignment", ""),
                "-m", parameters.get("model", "MFP"),
                "-bb", str(parameters.get("bootstrap", 1000)),
                "-nt", str(parameters.get("threads", 4)),
                "-pre", str(work_path / "iqtree"),
            ])

        elif tool.id == "meme":
            output_dir = work_path / "meme_out"
            cmd.extend([
                inputs.get("sequences", ""),
                "-nmotifs", str(parameters.get("nmotifs", 10)),
                "-minw", str(parameters.get("min_width", 6)),
                "-maxw", str(parameters.get("max_width", 200)),
                "-oc", str(output_dir),
                "-mod", "anr",
                "-protein",
            ])

        elif tool.id == "mcscanx":
            cmd.extend([
                inputs.get("gff", "").replace(".gff", ""),
            ])

        return cmd

    def _build_docker_command(
        self,
        tool: ToolMetadata,
        inputs: dict[str, str],
        parameters: dict[str, Any],
        work_path: Path,
    ) -> list[str]:
        cmd = [
            "docker", "run", "--rm",
            "-v", f"{work_path}:/data",
            "-w", "/data",
            tool.docker_image,
            tool.executable,
        ]

        base_cmd = self._build_command(tool, inputs, parameters, work_path)
        cmd.extend(base_cmd[1:])

        return cmd

    def _simulate_execution(
        self,
        tool: ToolMetadata,
        inputs: dict[str, str],
        parameters: dict[str, Any],
        work_path: Path,
        started_at: datetime,
    ) -> ExecutionResult:
        import random
        import time
        
        time.sleep(0.5)
        
        outputs = {}
        stdout_lines = [f"[SIMULATION] Executing {tool.name}"]
        stdout_lines.append(f"[SIMULATION] Input files: {list(inputs.values())}")
        stdout_lines.append(f"[SIMULATION] Parameters: {parameters}")
        
        if tool.id == "hmmsearch":
            output_tbl = work_path / "output.tbl"
            dom_tbl = work_path / "dom.tbl"
            
            target_file = inputs.get("target", "")
            num_hits = random.randint(3, 8)
            
            with open(output_tbl, "w") as f:
                f.write(f"# hmmsearch output (simulated)\n")
                f.write(f"# target: {target_file}\n")
                for i in range(num_hits):
                    evalue = 10 ** (-random.randint(5, 50))
                    score = random.uniform(50, 300)
                    f.write(f"gene{i+1}\t-\tSPL\t-\t{evalue:.2e}\t{score:.1f}\n")
            
            with open(dom_tbl, "w") as f:
                f.write(f"# domain hits (simulated)\n")
            
            outputs["tblout"] = str(output_tbl)
            outputs["domtblout"] = str(dom_tbl)
            stdout_lines.append(f"[SIMULATION] Found {num_hits} hits with E-value < {parameters.get('e_value', 1e-5)}")
            
        elif tool.id == "blastp":
            output_file = work_path / "blast_results.txt"
            
            num_hits = random.randint(5, 15)
            with open(output_file, "w") as f:
                for i in range(num_hits):
                    evalue = 10 ** (-random.randint(3, 30))
                    identity = random.uniform(30, 95)
                    f.write(f"query\tsubject{i+1}\t{identity:.1f}\t100\t85\t0\t1\t100\t{evalue:.2e}\t200\n")
            
            outputs["results"] = str(output_file)
            stdout_lines.append(f"[SIMULATION] Found {num_hits} similar sequences")
            
        elif tool.id == "mafft":
            output_file = work_path / "aligned.fasta"
            
            seq_file = inputs.get("sequences", "")
            aligned_seqs = ">gene1_aligned\nM-A-V-Q-K-L-I-R\n>gene2_aligned\nM-A-G-R-K-L-C-F\n>gene3_aligned\nM-S-D-R-Q-Q-L-V"
            
            with open(output_file, "w") as f:
                f.write(aligned_seqs)
            
            outputs["alignment"] = str(output_file)
            stdout_lines.append(f"[SIMULATION] Aligned sequences from {seq_file}")
            
        elif tool.id == "iqtree":
            tree_file = work_path / "iqtree.treefile"
            log_file = work_path / "iqtree.log"
            
            tree_content = "(gene1:0.1,gene2:0.2,(gene3:0.15,gene4:0.18):0.05);"
            with open(tree_file, "w") as f:
                f.write(tree_content)
            
            with open(log_file, "w") as f:
                f.write(f"IQ-TREE (simulated)\n")
                f.write(f"Best-fit model: {parameters.get('model', 'MFP')}\n")
                f.write(f"Bootstrap: {parameters.get('bootstrap', 1000)}\n")
            
            outputs["tree"] = str(tree_file)
            outputs["log"] = str(log_file)
            stdout_lines.append(f"[SIMULATION] Built phylogenetic tree")
            
        elif tool.id == "meme":
            meme_dir = work_path / "meme_out"
            meme_dir.mkdir(exist_ok=True)
            
            xml_file = meme_dir / "meme.xml"
            with open(xml_file, "w") as f:
                f.write('<?xml version="1.0"?>\n<meme>\n')
                for i in range(parameters.get("nmotifs", 10)):
                    f.write(f'<motif id="motif_{i+1}" width="{random.randint(6, 20)}" evalue="{10**-random.randint(5,20)}"/>\n')
                f.write('</meme>\n')
            
            outputs["motifs"] = str(xml_file)
            stdout_lines.append(f"[SIMULATION] Found {parameters.get('nmotifs', 10)} motifs")
            
        elif tool.id == "mcscanx":
            collinearity = work_path / "output.collinearity"
            tandem = work_path / "output.tandem"
            
            with open(collinearity, "w") as f:
                f.write("### Collinearity blocks (simulated)\n")
                f.write("chr1\tgene1\tgene2\tchr2\tgene3\tgene4\n")
            
            with open(tandem, "w") as f:
                f.write("# Tandem duplications (simulated)\n")
            
            outputs["collinearity"] = str(collinearity)
            outputs["tandem"] = str(tandem)
            stdout_lines.append(f"[SIMULATION] Analyzed synteny")
        
        return ExecutionResult(
            tool_id=tool.id,
            status=ExecutionStatus.COMPLETED,
            return_code=0,
            stdout="\n".join(stdout_lines),
            stderr="",
            outputs=outputs,
            started_at=started_at,
            completed_at=datetime.now(),
        )

    def _collect_outputs(self, tool: ToolMetadata, work_path: Path) -> dict[str, str]:
        outputs = {}

        output_mappings = {
            "tblout": "output.tbl",
            "domtblout": "dom.tbl",
            "tree": "iqtree.treefile",
            "log": "iqtree.log",
            "alignment": "aligned.fasta",
            "results": "blast_results.txt",
            "motifs": "meme_out/meme.xml",
            "collinearity": "output.collinearity",
            "tandem": "output.tandem",
        }

        for output_def in tool.outputs:
            if output_def.name in output_mappings:
                path = work_path / output_mappings[output_def.name]
                if path.exists():
                    outputs[output_def.name] = str(path)

        for f in work_path.glob("*.treefile"):
            outputs["tree"] = str(f)

        return outputs
