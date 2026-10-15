import os
from dataclasses import dataclass, field
from typing import Any, Callable


# 镜像标准形态: 每镜像=单一 conda 环境, 工具一律 PATH 裸命令。
# tartary_spl_analysis 环境在 dev 实测不存在(历史残留), hmmsearch/hmmscan/blastp/
# makeblastdb 按 test4py311 实际工具核验处理; mafft/iqtree/meme 实测在
# test4py311 bin(镜像 bioflow-gf), gfpy312 bin 无这三个工具(dev 历史残留,
# TL 2026-09-06 裁决: 工具阶段主环境单镜像执行, 无需跨镜像拆分)。
TOOL_PATHS = {
    "hmmsearch": "hmmsearch",
    "hmmscan": "hmmscan",
    "blastp": "blastp",
    "makeblastdb": "makeblastdb",
    "mafft": "mafft",
    "iqtree": "iqtree",
    "meme": "meme",
    "emapper": "emapper.py",
}


@dataclass
class ToolParameter:
    name: str
    type: str
    default: Any = None
    required: bool = True
    min_value: float | None = None
    max_value: float | None = None
    enum_values: list[str] | None = None


@dataclass
class ToolInput:
    name: str
    type: str
    required: bool = True


@dataclass
class ToolOutput:
    name: str
    type: str


@dataclass
class ToolMetadata:
    id: str
    name: str
    category: str
    executable: str
    docker_image: str | None = None
    inputs: list[ToolInput] = field(default_factory=list)
    outputs: list[ToolOutput] = field(default_factory=list)
    parameters: dict[str, ToolParameter] = field(default_factory=dict)
    timeout: int = 3600


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolMetadata] = {}

    def register(self, tool: ToolMetadata) -> None:
        self._tools[tool.id] = tool

    def get(self, tool_id: str) -> ToolMetadata | None:
        return self._tools.get(tool_id)

    def list_by_category(self, category: str) -> list[ToolMetadata]:
        return [t for t in self._tools.values() if t.category == category]

    def list_all(self) -> list[ToolMetadata]:
        return list(self._tools.values())


def create_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    
    registry.register(ToolMetadata(
        id="hmmsearch",
        name="HMMER hmmsearch",
        category="identification",
        executable=TOOL_PATHS.get("hmmsearch", "hmmsearch"),
        inputs=[
            ToolInput("query", "hmm", required=True),
            ToolInput("target", "fasta", required=True),
        ],
        outputs=[
            ToolOutput("tblout", "tblout"),
            ToolOutput("domtblout", "domtblout"),
        ],
        parameters={
            "e_value": ToolParameter("e_value", "float", default=1e-5, min_value=1e-100, max_value=1.0),
            "coverage": ToolParameter("coverage", "float", default=0.5, min_value=0.1, max_value=1.0),
            "cpu": ToolParameter("cpu", "int", default=4),
        },
        timeout=7200,
    ))
    
    registry.register(ToolMetadata(
        id="blastp",
        name="BLASTP",
        category="identification",
        executable=TOOL_PATHS.get("blastp", "blastp"),
        inputs=[
            ToolInput("query", "fasta", required=True),
            ToolInput("db", "blastdb", required=True),
        ],
        outputs=[
            ToolOutput("results", "blast_out"),
        ],
        parameters={
            "e_value": ToolParameter("e_value", "float", default=1e-5),
            "max_target_seqs": ToolParameter("max_target_seqs", "int", default=500),
            "outfmt": ToolParameter("outfmt", "int", default=6),
        },
        timeout=3600,
    ))
    
    registry.register(ToolMetadata(
        id="mafft",
        name="MAFFT",
        category="alignment",
        executable=TOOL_PATHS.get("mafft", "mafft"),
        inputs=[
            ToolInput("sequences", "fasta", required=True),
        ],
        outputs=[
            ToolOutput("alignment", "fasta"),
        ],
        parameters={
            "strategy": ToolParameter("strategy", "enum", default="auto", enum_values=["auto", "linsi", "ginsi"]),
            "maxiterate": ToolParameter("maxiterate", "int", default=1000),
        },
        timeout=7200,
    ))
    
    registry.register(ToolMetadata(
        id="iqtree",
        name="IQ-TREE",
        category="phylogeny",
        executable=TOOL_PATHS.get("iqtree", "iqtree"),
        inputs=[
            ToolInput("alignment", "fasta", required=True),
        ],
        outputs=[
            ToolOutput("tree", "newick"),
            ToolOutput("log", "log"),
        ],
        parameters={
            "model": ToolParameter("model", "enum", default="MFP", enum_values=["MFP", "LG", "WAG", "JTT"]),
            "bootstrap": ToolParameter("bootstrap", "int", default=1000),
            "threads": ToolParameter("threads", "int", default=4),
        },
        timeout=14400,
    ))
    
    registry.register(ToolMetadata(
        id="mcscanx",
        name="MCScanX",
        category="comparative",
        executable="MCScanX",
        inputs=[
            ToolInput("gff", "gff", required=True),
            ToolInput("blast", "blast_out", required=True),
        ],
        outputs=[
            ToolOutput("collinearity", "collinearity"),
            ToolOutput("tandem", "tandem"),
        ],
        parameters={
            "match_score": ToolParameter("match_score", "int", default=50),
            "gap_penalty": ToolParameter("gap_penalty", "int", default=-1),
        },
        timeout=3600,
    ))
    
    registry.register(ToolMetadata(
        id="meme",
        name="MEME",
        category="annotation",
        executable=TOOL_PATHS.get("meme", "meme"),
        inputs=[
            ToolInput("sequences", "fasta", required=True),
        ],
        outputs=[
            ToolOutput("motifs", "xml"),
        ],
        parameters={
            "nmotifs": ToolParameter("nmotifs", "int", default=10),
            "min_width": ToolParameter("min_width", "int", default=6),
            "max_width": ToolParameter("max_width", "int", default=200),
        },
        timeout=7200,
    ))
    
    registry.register(ToolMetadata(
        id="emapper",
        name="eggNOG-mapper",
        category="annotation",
        executable=TOOL_PATHS.get("emapper", "emapper.py"),
        inputs=[
            ToolInput("protein_fasta", "fasta", required=True),
        ],
        outputs=[
            ToolOutput("annotations", "emapper_annotations"),
            ToolOutput("seed_orthologs", "emapper_seed"),
            ToolOutput("pfam", "emapper_pfam"),
        ],
        parameters={
            "mode": ToolParameter("mode", "enum", default="diamond", enum_values=["diamond", "mmseqs", "hmmer"]),
            "e_value": ToolParameter("e_value", "float", default=0.001, min_value=1e-10, max_value=1.0),
            "score": ToolParameter("score", "int", default=60, min_value=0),
            "query_cover": ToolParameter("query_cover", "int", default=20, min_value=0, max_value=100),
            "subject_cover": ToolParameter("subject_cover", "int", default=20, min_value=0, max_value=100),
            "tax_scope": ToolParameter("tax_scope", "enum", default="auto", enum_values=["auto", "bact", "arch", "euk", "viruses"]),
            "cpu": ToolParameter("cpu", "int", default=8),
        },
        timeout=14400,
    ))
    
    return registry
