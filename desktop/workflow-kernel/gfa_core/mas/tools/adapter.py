import asyncio
import subprocess
from pathlib import Path
from typing import Any

from .registry import ToolMetadata


class ToolAdapter:
    def __init__(self, metadata: ToolMetadata, work_dir: str = "/tmp/gene-family"):
        self.metadata = metadata
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def execute(self, inputs: dict[str, str], parameters: dict[str, Any]) -> dict:
        cmd = self._build_command(inputs, parameters)
        
        try:
            result = subprocess.run(
                cmd,
                cwd=str(self.work_dir),
                capture_output=True,
                text=True,
                timeout=self.metadata.timeout,
            )
            
            return {
                "return_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "outputs": self._collect_outputs(),
            }
        except subprocess.TimeoutExpired:
            return {
                "return_code": -1,
                "stdout": "",
                "stderr": f"Tool {self.metadata.id} timed out after {self.metadata.timeout}s",
                "outputs": {},
            }
        except Exception as e:
            return {
                "return_code": -1,
                "stdout": "",
                "stderr": str(e),
                "outputs": {},
            }

    async def aexecute(self, inputs: dict[str, str], parameters: dict[str, Any]) -> dict:
        return await asyncio.to_thread(self.execute, inputs, parameters)

    def _build_command(self, inputs: dict[str, str], parameters: dict[str, Any]) -> list[str]:
        cmd = [self.metadata.executable]
        
        if self.metadata.id == "hmmsearch":
            cmd.extend([
                "--tblout", str(self.work_dir / "output.tbl"),
                "--domtblout", str(self.work_dir / "dom.tbl"),
                "-E", str(parameters.get("e_value", 1e-5)),
                "--cpu", str(parameters.get("cpu", 4)),
                inputs.get("query", ""),
                inputs.get("target", ""),
            ])
        elif self.metadata.id == "blastp":
            cmd.extend([
                "-query", inputs.get("query", ""),
                "-db", inputs.get("db", ""),
                "-evalue", str(parameters.get("e_value", 1e-5)),
                "-max_target_seqs", str(parameters.get("max_target_seqs", 500)),
                "-outfmt", str(parameters.get("outfmt", 6)),
                "-out", str(self.work_dir / "blast_results.txt"),
            ])
        elif self.metadata.id == "mafft":
            strategy = parameters.get("strategy", "auto")
            if strategy != "auto":
                cmd.append(f"--{strategy}")
            cmd.extend([
                "--maxiterate", str(parameters.get("maxiterate", 1000)),
                inputs.get("sequences", ""),
            ])
        elif self.metadata.id == "iqtree":
            cmd.extend([
                "-s", inputs.get("alignment", ""),
                "-m", parameters.get("model", "MFP"),
                "-bb", str(parameters.get("bootstrap", 1000)),
                "-nt", str(parameters.get("threads", 4)),
            ])
        elif self.metadata.id == "meme":
            cmd.extend([
                inputs.get("sequences", ""),
                "-nmotifs", str(parameters.get("nmotifs", 10)),
                "-minw", str(parameters.get("min_width", 6)),
                "-maxw", str(parameters.get("max_width", 200)),
                "-oc", str(self.work_dir / "meme_out"),
            ])
        
        return cmd

    def _collect_outputs(self) -> dict[str, str]:
        outputs = {}
        for output in self.metadata.outputs:
            if output.name == "tblout":
                path = self.work_dir / "output.tbl"
            elif output.name == "domtblout":
                path = self.work_dir / "dom.tbl"
            elif output.name == "tree":
                path = self.work_dir / "*.treefile"
            elif output.name == "alignment":
                path = self.work_dir / "aligned.fasta"
            else:
                continue
            
            if path.exists():
                outputs[output.name] = str(path)
        
        return outputs
