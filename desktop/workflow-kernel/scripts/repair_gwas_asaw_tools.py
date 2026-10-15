#!/usr/bin/env python3
"""Repair migrated ASAW GWAS tool wrappers and record before/after evidence."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ASAW_ENV = Path("/data/miniconda3/envs/ASAW")
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "项目记录" / "GWAS环境修订_20260622"

TOOL_COMMANDS = {
    "bcftools": ["--version"],
    "tabix": ["--version"],
    "bgzip": ["--version"],
    "java": ["-version"],
    "run_tassel_pipeline.pl": [],
    "run_pipeline.pl": [],
}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_command(cmd: list[str], *, timeout: int = 30, env: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return {
            "cmd": cmd,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
            "success": proc.returncode == 0,
        }
    except Exception as exc:
        return {
            "cmd": cmd,
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
            "success": False,
        }


def env_for(asaw_env: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{asaw_env / 'bin'}:{env.get('PATH', '')}"
    env["CONDA_PREFIX"] = str(asaw_env)
    return env


def symlink_info(path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "lexists": os.path.lexists(path),
        "is_symlink": path.is_symlink(),
        "target": "",
        "broken": False,
    }
    if path.is_symlink():
        target = os.readlink(path)
        info["target"] = target
        info["broken"] = not path.exists()
    return info


def audit(asaw_env: Path) -> dict[str, Any]:
    bin_dir = asaw_env / "bin"
    lib_dir = asaw_env / "lib"
    env = env_for(asaw_env)
    tools: list[dict[str, Any]] = []
    for tool, args in TOOL_COMMANDS.items():
        path = bin_dir / tool
        present = path.exists() and path.is_file()
        result = run_command([str(path), *args], env=env) if present else {
            "cmd": [str(path), *args],
            "returncode": None,
            "stdout": "",
            "stderr": "not present",
            "success": False,
        }
        output = (result.get("stdout") or result.get("stderr") or "").strip()
        first_line = output.splitlines()[0] if output else ""
        tools.append({
            "tool": tool,
            "path": str(path),
            "present": present,
            "returncode": result.get("returncode"),
            "version_or_probe": first_line,
            "stdout_tail": result.get("stdout", ""),
            "stderr_tail": result.get("stderr", ""),
            "usable": present and result.get("returncode") == 0,
            "dynamic_library_error": "error while loading shared libraries" in output.lower(),
        })
    return {
        "schema_version": "gwas_asaw_tool_repair_audit_v1",
        "created_at": now(),
        "asaw_env": str(asaw_env),
        "bin_dir": str(bin_dir),
        "tools": tools,
        "symlinks": {
            "libcrypto.so.1.0.0": symlink_info(lib_dir / "libcrypto.so.1.0.0"),
            "libssl.so.1.0.0": symlink_info(lib_dir / "libssl.so.1.0.0"),
            "run_pipeline.pl": symlink_info(bin_dir / "run_pipeline.pl"),
            "run_tassel_pipeline.pl": symlink_info(bin_dir / "run_tassel_pipeline.pl"),
        },
        "conda_unpack": {
            "path": str(bin_dir / "conda-unpack"),
            "present": (bin_dir / "conda-unpack").exists(),
        },
    }


def run_conda_unpack(asaw_env: Path) -> dict[str, Any]:
    unpack = asaw_env / "bin" / "conda-unpack"
    if not unpack.exists():
        return {"success": False, "skipped": True, "reason": "conda-unpack not found", "cmd": [str(unpack)]}
    return run_command([str(unpack)], timeout=300, env=env_for(asaw_env))


def repair_tassel_script(script: Path, asaw_env: Path) -> dict[str, Any]:
    if not script.exists():
        return {"script": str(script), "changed": False, "reason": "missing"}
    text = script.read_text(encoding="utf-8", errors="ignore")
    original = text
    text = text.replace("#!/opt/anaconda1anaconda2anaconda3/bin/perl -w", f"#!{asaw_env}/bin/perl -w")
    text = text.replace('$top = "/opt/anaconda1anaconda2anaconda3/share/tassel-5.2.37";', f'$top = "{asaw_env}/share/tassel-5.2.37";')
    text = text.replace('$top="/opt/anaconda1anaconda2anaconda3/share/tassel-5.2.37";', f'$top="{asaw_env}/share/tassel-5.2.37";')
    legacy_check = textwrap.dedent(
        r'''
        my $java_minor_ver = (split /\./, $java_version[2])[1];
        if($java_minor_ver lt 8) {
            print STDERR "ERROR: Java 1.8 or above must be installed to run TASSEL.\n";
            exit 1;
        }
        '''
    ).strip()
    compatible_check = textwrap.dedent(
        r'''
        my $java_token = $java_version[2] || "";
        $java_token =~ s/"//g;
        my @java_parts = split /\./, $java_token;
        my $java_major_ver = ($java_parts[0] eq "1") ? ($java_parts[1] || 0) : ($java_parts[0] || 0);
        if($java_major_ver < 8) {
            print STDERR "ERROR: Java 1.8 or above must be installed to run TASSEL.\n";
            exit 1;
        }
        '''
    ).strip()
    if legacy_check in text:
        text = text.replace(legacy_check, compatible_check)
    if text != original:
        script.write_text(text, encoding="utf-8")
        mode = script.stat().st_mode
        script.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return {"script": str(script), "changed": True}
    return {"script": str(script), "changed": False, "reason": "already compatible or unrecognized format"}


def ensure_run_pipeline_compat(asaw_env: Path) -> dict[str, Any]:
    compat = asaw_env / "bin" / "run_pipeline.pl"
    target = asaw_env / "bin" / "run_tassel_pipeline.pl"
    if not target.exists():
        return {"changed": False, "success": False, "reason": "run_tassel_pipeline.pl missing"}
    if compat.exists() or os.path.lexists(compat):
        if compat.is_symlink() and os.readlink(compat) == "run_tassel_pipeline.pl":
            return {"changed": False, "success": True, "reason": "already linked"}
        backup = compat.with_name(f"{compat.name}.pre_gwas_repair_{datetime.now().strftime('%Y%m%d%H%M%S')}")
        shutil.move(str(compat), str(backup))
    os.symlink("run_tassel_pipeline.pl", compat)
    return {"changed": True, "success": True, "link": str(compat), "target": "run_tassel_pipeline.pl"}


def validate_htslib(asaw_env: Path, out: Path) -> dict[str, Any]:
    validation_dir = out / "validation" / "htslib"
    validation_dir.mkdir(parents=True, exist_ok=True)
    vcf = validation_dir / "tiny.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=chr1>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\ts1\n"
        "chr1\t1\t.\tA\tG\t60\tPASS\t.\tGT\t0/1\n",
        encoding="utf-8",
    )
    env = env_for(asaw_env)
    bgzip = run_command([str(asaw_env / "bin" / "bgzip"), "-f", str(vcf)], env=env, timeout=60)
    gz = validation_dir / "tiny.vcf.gz"
    tabix = run_command([str(asaw_env / "bin" / "tabix"), "-f", "-p", "vcf", str(gz)], env=env, timeout=60) if gz.exists() else {"success": False, "stderr": "bgzip output missing"}
    view = run_command([str(asaw_env / "bin" / "bcftools"), "view", "-H", str(gz)], env=env, timeout=60) if gz.exists() else {"success": False, "stderr": "bgzip output missing"}
    return {"bgzip": bgzip, "tabix": tabix, "bcftools_view": view, "success": bool(bgzip.get("success") and tabix.get("success") and view.get("success"))}


def validate_tassel(asaw_env: Path) -> dict[str, Any]:
    env = env_for(asaw_env)
    return {
        "run_tassel_pipeline": run_command([str(asaw_env / "bin" / "run_tassel_pipeline.pl")], env=env, timeout=60),
        "run_pipeline_compat": run_command([str(asaw_env / "bin" / "run_pipeline.pl")], env=env, timeout=60),
    }


def write_report(out: Path, before: dict[str, Any], actions: dict[str, Any], after: dict[str, Any], validations: dict[str, Any]) -> None:
    def tool_line(audit_record: dict[str, Any], tool: str) -> str:
        row = next((item for item in audit_record["tools"] if item["tool"] == tool), {})
        status = "usable" if row.get("usable") else "failed"
        return f"- `{tool}`: {status}, returncode={row.get('returncode')}, probe={row.get('version_or_probe', '')}"

    lines = [
        "# GWAS ASAW 工具修订记录",
        "",
        f"- created_at: `{now()}`",
        f"- asaw_env: `{after.get('asaw_env')}`",
        f"- scope: htslib 工具组与 TASSEL 旧命令名兼容；不安装 GEMMA/EMMAX/Beagle/GAPIT。",
        "",
        "## Before",
        *[tool_line(before, tool) for tool in TOOL_COMMANDS],
        "",
        "## Actions",
        "```json",
        json.dumps(actions, indent=2, ensure_ascii=False),
        "```",
        "",
        "## After",
        *[tool_line(after, tool) for tool in TOOL_COMMANDS],
        "",
        "## Validation",
        f"- htslib tiny VCF validation: `{validations.get('htslib', {}).get('success')}`",
        f"- TASSEL wrapper probe returncodes: run_tassel_pipeline.pl=`{validations.get('tassel', {}).get('run_tassel_pipeline', {}).get('returncode')}`, run_pipeline.pl=`{validations.get('tassel', {}).get('run_pipeline_compat', {}).get('returncode')}`",
        "",
        "## Interpretation",
        "- `bcftools/tabix/bgzip` 属于 ASAW 迁移后的动态库/prefix 修订对象。",
        "- `run_pipeline.pl` 是旧 GWAS 脚本命名兼容入口，后端映射到现有 `run_tassel_pipeline.pl`。",
        "- 本次没有运行 `Gwas.sh`，也没有把 GEMMA/EMMAX/Beagle/GAPIT 作为当前阶段阻塞项。",
    ]
    (out / "env_repair_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asaw-env", type=Path, default=DEFAULT_ASAW_ENV)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--repair", action="store_true", help="apply conda-unpack and TASSEL compatibility repairs")
    args = parser.parse_args()

    asaw_env = args.asaw_env.resolve()
    out = args.output_root.resolve()
    out.mkdir(parents=True, exist_ok=True)

    before = audit(asaw_env)
    write_json(out / "env_repair_before.json", before)
    actions: dict[str, Any] = {"schema_version": "gwas_asaw_tool_repair_actions_v1", "created_at": now(), "repair_requested": args.repair, "steps": []}
    if args.repair:
        actions["steps"].append({"name": "conda_unpack", "result": run_conda_unpack(asaw_env)})
        actions["steps"].append({"name": "repair_tassel_run_tassel_pipeline", "result": repair_tassel_script(asaw_env / "bin" / "run_tassel_pipeline.pl", asaw_env)})
        actions["steps"].append({"name": "repair_tassel_start_tassel", "result": repair_tassel_script(asaw_env / "bin" / "start_tassel.pl", asaw_env)})
        actions["steps"].append({"name": "ensure_run_pipeline_compat", "result": ensure_run_pipeline_compat(asaw_env)})
    write_json(out / "env_repair_actions.json", actions)

    after = audit(asaw_env)
    write_json(out / "env_repair_after.json", after)
    validations = {
        "schema_version": "gwas_asaw_tool_repair_validation_v1",
        "created_at": now(),
        "htslib": validate_htslib(asaw_env, out),
        "tassel": validate_tassel(asaw_env),
    }
    write_json(out / "env_repair_validation.json", validations)
    write_report(out, before, actions, after, validations)

    htslib_ok = bool(validations["htslib"].get("success"))
    tassel_ok = (after_tool(after, "run_pipeline.pl").get("present") and after_tool(after, "run_tassel_pipeline.pl").get("present"))
    return 0 if (htslib_ok and tassel_ok) else 2


def after_tool(audit_record: dict[str, Any], tool: str) -> dict[str, Any]:
    return next((item for item in audit_record.get("tools", []) if item.get("tool") == tool), {})


if __name__ == "__main__":
    raise SystemExit(main())
