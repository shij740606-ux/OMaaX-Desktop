#!/usr/bin/env python3
"""Download PlantTFDB species TF binding motif sets.

This script parses the official PlantTFDB download page and downloads links from
the "Sets of TF binding motifs for 156 species" motif section. It uses only the
Python standard library so it can run in a clean environment.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable


PLANTTFDB_DOWNLOAD_URL = "https://planttfdb.gao-lab.org/download.php"
DEFAULT_OUTDIR = Path("/home/xia-lab/下载/PlantTFDB_TF_binding_motifs_156_species")
USER_AGENT = "Mozilla/5.0 (compatible; bbav5-PlantTFDB-motif-downloader/1.0)"
SYSTEM_BIN_DIRS = ("/usr/bin", "/bin", "/usr/local/bin")
PREFER_EXTERNAL_TRANSPORT = False


@dataclass(frozen=True)
class MotifLink:
    url: str
    filename: str
    category: str
    species: str = ""


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.anchors: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attrs_dict = {k.lower(): v for k, v in attrs}
        href = attrs_dict.get("href")
        if href:
            self._href = href
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            text = " ".join(x.strip() for x in self._text if x.strip())
            self.anchors.append((self._href, text))
            self._href = None
            self._text = []


def urlopen(url: str, timeout: int, insecure: bool = False) -> bytes:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
    }
    request = urllib.request.Request(url, headers=headers)
    context = None
    if insecure:
        context = ssl._create_unverified_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        return response.read()


def downgrade_to_http(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        return url
    return urllib.parse.urlunparse(parsed._replace(scheme="http"))


def clean_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ("LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME"):
        env.pop(key, None)
    env["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    return env


def preferred_executable(name: str) -> str | None:
    for directory in SYSTEM_BIN_DIRS:
        candidate = Path(directory) / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which(name)


def curl_available() -> bool:
    return preferred_executable("curl") is not None


def wget_available() -> bool:
    return preferred_executable("wget") is not None


def wget_base_command(url: str, timeout: int, insecure: bool = False) -> list[str]:
    wget = preferred_executable("wget")
    if not wget:
        raise RuntimeError("wget is not available")
    command = [
        wget,
        "--inet4-only",
        "--tries=6",
        "--waitretry=2",
        "--retry-connrefused",
        "--no-http-keep-alive",
        f"--timeout={timeout}",
        f"--user-agent={USER_AGENT}",
    ]
    if insecure:
        command.append("--no-check-certificate")
    command.append(url)
    return command


def wget_fetch(url: str, timeout: int, insecure: bool = False) -> bytes:
    command = wget_base_command(url, timeout=timeout, insecure=insecure)
    command[-1:-1] = ["-O", "-"]
    proc = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=clean_subprocess_env(),
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"wget failed with exit code {proc.returncode}: {stderr}")
    return proc.stdout


def wget_download(url: str, dest: Path, timeout: int, insecure: bool = False) -> None:
    command = wget_base_command(url, timeout=timeout, insecure=insecure)
    command[-1:-1] = ["-O", str(dest)]
    proc = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=clean_subprocess_env(),
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"wget failed with exit code {proc.returncode}: {stderr}")


def curl_base_command(url: str, timeout: int, insecure: bool = False) -> list[str]:
    curl = preferred_executable("curl")
    if not curl:
        raise RuntimeError("curl is not available")
    command = [
        curl,
        "-4",
        "-L",
        "--http1.1",
        "--compressed",
        "--fail",
        "--retry",
        "2",
        "--connect-timeout",
        str(min(timeout, 30)),
        "--max-time",
        str(timeout),
        "-A",
        USER_AGENT,
    ]
    if insecure:
        command.append("-k")
    command.append(url)
    return command


def curl_fetch(url: str, timeout: int, insecure: bool = False) -> bytes:
    command = curl_base_command(url, timeout=timeout, insecure=insecure)
    proc = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=clean_subprocess_env(),
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"curl failed with exit code {proc.returncode}: {stderr}")
    return proc.stdout


def curl_download(url: str, dest: Path, timeout: int, insecure: bool = False) -> None:
    command = curl_base_command(url, timeout=timeout, insecure=insecure)
    command[-1:-1] = ["-o", str(dest)]
    proc = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=clean_subprocess_env(),
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"curl failed with exit code {proc.returncode}: {stderr}")


def urlopen_with_http_fallback(url: str, timeout: int, insecure: bool = False) -> tuple[bytes, str]:
    errors: list[str] = []
    if not PREFER_EXTERNAL_TRANSPORT:
        try:
            return urlopen(url, timeout=timeout, insecure=insecure), url
        except Exception as exc:  # noqa: BLE001 - network libraries raise several exception families
            errors.append(f"HTTPS/Python: {exc}")
            http_url = downgrade_to_http(url)
            if http_url != url:
                print(
                    f"[PlantTFDB] HTTPS/Python fetch failed ({exc}); retrying with HTTP/Python: {http_url}",
                    file=sys.stderr,
                )
                try:
                    return urlopen(http_url, timeout=timeout, insecure=insecure), http_url
                except Exception as http_exc:  # noqa: BLE001 - fall through to external transport
                    errors.append(f"HTTP/Python: {http_exc}")

    if wget_available():
        for candidate in dict.fromkeys([url, downgrade_to_http(url)]):
            try:
                return wget_fetch(candidate, timeout=timeout, insecure=insecure), candidate
            except Exception as exc:  # noqa: BLE001 - include all transport errors
                errors.append(f"{candidate}/wget: {exc}")

    if curl_available():
        for candidate in dict.fromkeys([url, downgrade_to_http(url)]):
            try:
                return curl_fetch(candidate, timeout=timeout, insecure=insecure), candidate
            except Exception as exc:  # noqa: BLE001 - include all transport errors
                errors.append(f"{candidate}/curl: {exc}")

    raise RuntimeError("; ".join(errors))


def download_with_transport_fallback(url: str, part: Path, timeout: int, insecure: bool = False) -> str:
    errors: list[str] = []
    try:
        payload, used_url = urlopen_with_http_fallback(url, timeout=timeout, insecure=insecure)
        if not payload:
            raise RuntimeError("empty response")
        with part.open("wb") as handle:
            handle.write(payload)
        return used_url
    except Exception as exc:  # noqa: BLE001 - fall through to direct curl download
        errors.append(f"Python/fetch: {exc}")

    if wget_available():
        for candidate in dict.fromkeys([url, downgrade_to_http(url)]):
            try:
                wget_download(candidate, part, timeout=timeout, insecure=insecure)
                if part.stat().st_size == 0:
                    raise RuntimeError("empty response")
                return candidate
            except Exception as exc:  # noqa: BLE001 - include all transport errors
                errors.append(f"{candidate}/wget-download: {exc}")

    if curl_available():
        for candidate in dict.fromkeys([url, downgrade_to_http(url)]):
            try:
                curl_download(candidate, part, timeout=timeout, insecure=insecure)
                if part.stat().st_size == 0:
                    raise RuntimeError("empty response")
                return candidate
            except Exception as exc:  # noqa: BLE001 - include all transport errors
                errors.append(f"{candidate}/curl-download: {exc}")

    raise RuntimeError("; ".join(errors))



def classify_filename(filename: str) -> str:
    lower = filename.lower()
    if lower.startswith("planttfdb_tf_binding_motifs_"):
        return "global_experimental_motifs"
    if lower.endswith("_tf_binding_motifs_information.txt") or lower.endswith("_information.txt"):
        return "motif_information"
    if lower.endswith(".meme.gz") or lower.endswith(".meme"):
        return "all_motifs_meme"
    if lower.endswith((".zip", ".tar.gz", ".tgz", ".tar")):
        return "individual_motifs"
    return "other"


def parse_motif_links(html: str, page_url: str) -> list[MotifLink]:
    parser = AnchorParser()
    parser.feed(html)

    links: dict[str, MotifLink] = {}
    current_species = ""
    for href, _text in parser.anchors:
        url = urllib.parse.urljoin(page_url, href)
        parsed = urllib.parse.urlparse(url)
        text = " ".join((_text or "").split())
        if text and text.lower() != "download" and "/download/motif/" not in parsed.path:
            current_species = text
        if "/download/motif/" not in parsed.path:
            continue
        filename = Path(urllib.parse.unquote(parsed.path)).name
        if not filename:
            continue
        category = classify_filename(filename)
        links[url] = MotifLink(url=url, filename=filename, category=category, species=current_species)

    return sorted(links.values(), key=lambda x: (x.species, x.category, x.filename, x.url))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def target_path(outdir: Path, link: MotifLink) -> Path:
    return outdir / link.category / link.filename


def download_one(
    link: MotifLink,
    outdir: Path,
    timeout: int,
    retries: int,
    force: bool,
    insecure: bool,
) -> dict:
    dest = target_path(outdir, link)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and dest.stat().st_size > 0 and not force:
        return {
            **asdict(link),
            "used_url": "",
            "path": str(dest),
            "status": "exists",
            "size": dest.stat().st_size,
            "sha256": sha256_file(dest),
            "error": "",
        }

    last_error = ""
    part = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, retries + 2):
        try:
            used_url = download_with_transport_fallback(
                link.url,
                part,
                timeout=timeout,
                insecure=insecure,
            )
            part.replace(dest)
            return {
                **asdict(link),
                "used_url": used_url,
                "path": str(dest),
                "status": "downloaded",
                "size": dest.stat().st_size,
                "sha256": sha256_file(dest),
                "error": "",
            }
        except Exception as exc:  # noqa: BLE001 - keep download report complete
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt <= retries:
                time.sleep(min(10, attempt * 2))

    try:
        part.unlink()
    except OSError:
        pass
    return {
        **asdict(link),
        "used_url": "",
        "path": str(dest),
        "status": "failed",
        "size": 0,
        "sha256": "",
        "error": last_error,
    }


def write_manifest(outdir: Path, rows: Iterable[dict], name: str) -> None:
    rows = list(rows)
    outdir.mkdir(parents=True, exist_ok=True)

    json_path = outdir / f"{name}.json"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    tsv_path = outdir / f"{name}.tsv"
    fields = ["species", "category", "filename", "url", "used_url", "path", "status", "size", "sha256", "error"]
    with tsv_path.open("w", encoding="utf-8") as handle:
        handle.write("\t".join(fields) + "\n")
        for row in rows:
            handle.write("\t".join(str(row.get(field, "")).replace("\t", " ") for field in fields) + "\n")


def summarize_links(links: list[MotifLink]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for link in links:
        summary[link.category] = summary.get(link.category, 0) + 1
    return dict(sorted(summary.items()))


def main() -> int:
    global PREFER_EXTERNAL_TRANSPORT

    parser = argparse.ArgumentParser(
        description="Download PlantTFDB Sets of TF binding motifs for 156 species.",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTDIR, help="Output directory.")
    parser.add_argument("--page-url", default=PLANTTFDB_DOWNLOAD_URL, help="PlantTFDB download page URL.")
    parser.add_argument("--jobs", type=int, default=4, help="Parallel downloads.")
    parser.add_argument("--timeout", type=int, default=120, help="Network timeout seconds.")
    parser.add_argument("--retries", type=int, default=2, help="Retries per file.")
    parser.add_argument("--force", action="store_true", help="Re-download existing files.")
    parser.add_argument("--dry-run", action="store_true", help="Only parse links and write link manifest.")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification.")
    parser.add_argument(
        "--prefer-external",
        action="store_true",
        help="Use system wget/curl first. Useful when Python TLS fails for PlantTFDB.",
    )
    parser.add_argument(
        "--species",
        default="",
        help="Download only species whose name contains this text, e.g. 'Arabidopsis thaliana'.",
    )
    parser.add_argument(
        "--category",
        choices=[
            "all",
            "motif_information",
            "all_motifs_meme",
            "individual_motifs",
            "global_experimental_motifs",
            "other",
        ],
        default="all",
        help="Download only one category.",
    )
    parser.add_argument(
        "--include-global-experimental",
        action="store_true",
        help="Include the global PlantTFDB experimental motif files in addition to species-projected sets.",
    )
    args = parser.parse_args()
    PREFER_EXTERNAL_TRANSPORT = args.prefer_external

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"[PlantTFDB] Fetching official download page: {args.page_url}", file=sys.stderr)
    page_bytes, used_page_url = urlopen_with_http_fallback(
        args.page_url,
        timeout=args.timeout,
        insecure=args.insecure,
    )
    html = page_bytes.decode("utf-8", errors="replace")
    links = parse_motif_links(html, used_page_url)
    if args.species:
        needle = args.species.lower()
        links = [link for link in links if needle in link.species.lower()]
    if args.category == "all" and not args.include_global_experimental:
        links = [link for link in links if link.category != "global_experimental_motifs"]
    if args.category != "all":
        links = [link for link in links if link.category == args.category]

    if not links:
        print("[PlantTFDB] No motif links found. The page structure may have changed.", file=sys.stderr)
        return 2

    link_rows = [
        {
            **asdict(link),
            "used_url": "",
            "path": str(target_path(args.out, link)),
            "status": "planned",
            "size": "",
            "sha256": "",
            "error": "",
        }
        for link in links
    ]
    write_manifest(args.out, link_rows, "planttfdb_motif_links")
    print(f"[PlantTFDB] Found {len(links)} motif files: {summarize_links(links)}", file=sys.stderr)
    print(f"[PlantTFDB] Link manifest: {args.out / 'planttfdb_motif_links.tsv'}", file=sys.stderr)

    if args.dry_run:
        print("[PlantTFDB] Dry run only; no files downloaded.", file=sys.stderr)
        return 0

    results: list[dict] = []
    max_workers = max(1, args.jobs)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_link = {
            executor.submit(download_one, link, args.out, args.timeout, args.retries, args.force, args.insecure): link
            for link in links
        }
        for i, future in enumerate(concurrent.futures.as_completed(future_to_link), start=1):
            result = future.result()
            results.append(result)
            print(
                f"[{i}/{len(links)}] {result['status']}: {result['category']}/{result['filename']}",
                file=sys.stderr,
            )

    results = sorted(results, key=lambda x: (x["species"], x["category"], x["filename"], x["url"]))
    write_manifest(args.out, results, "planttfdb_motif_download_manifest")
    failed = [row for row in results if row["status"] == "failed"]
    if failed:
        write_manifest(args.out, failed, "planttfdb_motif_failed")
        print(f"[PlantTFDB] Failed downloads: {len(failed)}. See planttfdb_motif_failed.tsv", file=sys.stderr)
        return 1

    print(f"[PlantTFDB] Complete. Manifest: {args.out / 'planttfdb_motif_download_manifest.tsv'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
