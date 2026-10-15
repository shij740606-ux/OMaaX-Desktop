#!/home/xia-lab/miniconda3/envs/test4py311/bin/python
"""Plot weighted LD-decay curves from PopLDdecay .stat.gz outputs."""

from __future__ import annotations

import argparse
import gzip
from collections import defaultdict
from pathlib import Path


def _curve(path: Path, bin1: int, bin2: int) -> tuple[list[float], list[float]]:
    sums: dict[tuple[int, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for raw in handle:
            if raw.startswith("#") or not raw.strip():
                continue
            fields = raw.split()
            if len(fields) < 6 or fields[1] == "NA":
                continue
            distance, sum_r2, pairs = int(fields[0]), float(fields[3]), int(fields[5])
            width = bin1 if distance <= 10_000 else bin2
            key = (width, (distance - 1) // width)
            sums[key][0] += sum_r2
            sums[key][1] += pairs
    points = sorted(
        (((index + 0.5) * width / 1000.0, total / pairs) for (width, index), (total, pairs) in sums.items() if pairs > 0),
        key=lambda item: item[0],
    )
    if not points:
        raise ValueError(f"No finite LD points in {path}")
    return [item[0] for item in points], [item[1] for item in points]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("-inFile", type=Path)
    source.add_argument("-inList", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("-bin1", default=10, type=int)
    parser.add_argument("-bin2", default=500, type=int)
    args = parser.parse_args(argv)
    if args.bin1 <= 0 or args.bin2 <= 0:
        raise ValueError("LD plot bin widths must be positive")
    curves: list[tuple[str, Path]] = []
    if args.inFile:
        curves = [("All samples", args.inFile)]
    else:
        for raw in args.inList.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                path, label = raw.split(maxsplit=1)
                curves.append((label, Path(path)))
    if not curves:
        raise ValueError("No LD curves requested")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7, 5))
    for label, path in curves:
        x, y = _curve(path, args.bin1, args.bin2)
        ax.plot(x, y, label=label, linewidth=1.4)
    ax.set_xlabel("Physical distance (kb)")
    ax.set_ylabel("Weighted mean $r^2$")
    if len(curves) > 1:
        ax.legend(frameon=False)
    fig.tight_layout()
    for suffix in (".png", ".pdf"):
        fig.savefig(Path(str(args.output) + suffix), dpi=180)
    plt.close(fig)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
