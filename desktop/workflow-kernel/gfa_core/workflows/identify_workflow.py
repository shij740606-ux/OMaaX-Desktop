import json
from dataclasses import asdict, is_dataclass
from pathlib import Path


def _to_dict(x):
    if is_dataclass(x):
        return asdict(x)
    if hasattr(x, "__dict__"):
        return dict(x.__dict__)
    return {"value": str(x)}


class IdentifyWorkflow:
    # final_fasta_agent 可选：不传也兼容老版本
    def __init__(self, merge_agent, extract_agent, domain_agent, report_agent, final_fasta_agent=None):
        self.merge = merge_agent
        self.extract = extract_agent
        self.domain = domain_agent
        self.final_fasta = final_fasta_agent
        self.report = report_agent

    def run(self, ctx, params):
        lg = ctx.logger()
        ws = Path(ctx.workspace).resolve()
        ws.mkdir(parents=True, exist_ok=True)

        # 关键产物路径统一在这里约定（后续 agent 只读 manifest.artifacts）
        artifacts = {
            "merged_id": str(ws / f"{ctx.prefix}.candidates.merged.id"),
            "evidence_tsv": str(ws / f"{ctx.prefix}.candidates.evidence.tsv"),
            "candidates_fa": str(ws / f"{ctx.prefix}.candidates.pep.fa"),
            "pfam_domains_tsv": str(ws / f"{ctx.prefix}.pfam.domains.tsv"),
            "final_id": str(ws / f"{ctx.prefix}.identified_members.id"),
            "final_tsv": str(ws / f"{ctx.prefix}.identified_members.tsv"),
            "final_fa": str(ws / f"{ctx.prefix}.identified_members.pep.fa"),
            "report_md": str(ws / f"{ctx.prefix}.identify_report.md"),
            "manifest_json": str(ws / f"{ctx.prefix}.identify.manifest.json"),
        }

        manifest = {
            "prefix": ctx.prefix,
            "workspace": str(ws),
            "params": _to_dict(params),
            "artifacts": artifacts,
            "steps": [],
        }

        # 1) merge
        if (not ctx.force) and self.merge.outputs_exist(ctx):
            lg.info("skip merge (outputs exist)")
            merged_id = artifacts["merged_id"]
        else:
            m = self.merge.run(ctx, params)
            merged_id = getattr(m, "merged_id", artifacts["merged_id"])
            manifest["steps"].append({"agent": self.merge.name, "out": _to_dict(m)})

        # 2) extract
        if (not ctx.force) and self.extract.outputs_exist(ctx):
            lg.info("skip extract (outputs exist)")
            candidates_fa = artifacts["candidates_fa"]
        else:
            e = self.extract.run(ctx, params, merged_id=merged_id)
            candidates_fa = getattr(e, "candidates_fa", artifacts["candidates_fa"])
            manifest["steps"].append({"agent": self.extract.name, "out": _to_dict(e)})

        # 3) domain
        if (not ctx.force) and self.domain.outputs_exist(ctx):
            lg.info("skip domain scan (outputs exist)")
        else:
            d = self.domain.run(ctx, params, candidates_fa=candidates_fa)
            manifest["steps"].append({"agent": self.domain.name, "out": _to_dict(d)})

        # 3.5) final fasta（可选）
        if self.final_fasta is not None:
            if (not ctx.force) and self.final_fasta.outputs_exist(ctx):
                lg.info("skip final fasta (outputs exist)")
            else:
                f = self.final_fasta.run(ctx, params)
                manifest["steps"].append({"agent": self.final_fasta.name, "out": _to_dict(f)})

        # 4) report（我们下一步会升级 report，先统一传 params；report 兼容 params=None）
        if (not ctx.force) and self.report.outputs_exist(ctx):
            lg.info("skip report (outputs exist)")
        else:
            r = self.report.run(ctx, params)
            manifest["steps"].append({"agent": self.report.name, "out": _to_dict(r)})

        mf = Path(artifacts["manifest_json"])
        mf.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        lg.info(f"manifest -> {mf}")
        return manifest
