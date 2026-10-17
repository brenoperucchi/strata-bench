#!/usr/bin/env python3
"""Read the community reports of the Strata repository into docs/community.json, a catalogue shown on its own page.

These reports were measured with other prompts than strata-bench's, so they are never turned into strata-bench results:
nothing here goes to results/, the validator or the scoreboard. Each entry says where it came from (the folder, the commit of
the Strata checkout and the SHA-256 of its README) and every figure is read from the README as written; a field the README
does not state stays null.

    python3 tools/import_strata_reports.py --source /path/to/Strata-checkout [--out docs/community.json]

The checkout needs bench/results/ and docs/COMMUNITY_BENCHMARKS.md. Only the folders whose name contains "community" are read.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = "https://github.com/Niko1221/Strata"
QUANT_RE = re.compile(r"\b(UD-IQ\d_[A-Z]+|UD-Q\d_[A-Z0-9_]+|IQ\d_[A-Z]+|Q\d_[A-Z0-9_]+)\b")
NUM_RE = re.compile(r"^\s*(\d[\d,]*\.?\d*|\.\d+)")
MAX_ROWS = 14


def number(cell):
    """The first number of a table cell ('615.8 (614.4-616.7)' is 615.8, '1,945' is 1945); None for anything else.
    A cell that lists the individual runs ('886.8 / 922.6 / 922.8') has no median in it, so it is None rather than the first run."""
    if re.search(r"\d\s*/\s*\d", cell or "") and not re.search(r"\d\s*/\s*\d[\d,.]*\s*(%|GiB|MiB)", cell or ""):
        return None
    m = NUM_RE.match(cell or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def clean(text, limit=300):
    text = re.sub(r"\s+", " ", re.sub(r"[`*]", "", text or "")).strip()
    return text[:limit] if text else None


def index_entries(guide: str):
    """The curated list in docs/COMMUNITY_BENCHMARKS.md: folder -> (date, headline, summary)."""
    out = {}
    for m in re.finditer(r"^- \[(\d{4}-\d{2}-\d{2}): (.+?)\]\(\.\./bench/results/([^/)]+)/README\.md\):?\s*\n((?:[ \t]+.+\n?)*)", guide, re.M):
        out[m.group(3)] = {"date": m.group(1), "headline": clean(m.group(2), 200), "summary": clean(m.group(4), 400)}
    return out


def bullets(readme: str):
    """'- Key: value' lines of the hardware and software section, by lower-case key."""
    section = re.search(r"##\s*Hardware and software\s*\n(.*?)(\n##\s|\Z)", readme, re.S)
    out = {}
    for line in (section.group(1) if section else "").splitlines():
        m = re.match(r"^\s*[-*]\s*([A-Za-z][A-Za-z /&()+-]{1,40}?):\s*(.+)$", line)
        if m:
            out.setdefault(m.group(1).strip().lower(), clean(m.group(2), 200))
    return out


def results_rows(readme: str):
    """Rows of the first table under '## Results' whose header names a prompt or decode speed; the cells as numbers."""
    section = re.search(r"##\s*Results\s*\n(.*?)(\n##\s|\Z)", readme, re.S)
    if not section:
        return []
    lines = [l for l in section.group(1).splitlines() if l.strip().startswith("|")]
    if len(lines) < 3:
        return []
    cells = lambda l: [c.strip() for c in l.strip().strip("|").split("|")]
    head = [h.lower() for h in cells(lines[0])]
    find = lambda *keys: next((i for i, h in enumerate(head) if any(k in h for k in keys)), None)
    def median_of(i):
        """A header such as 'Prompt t/s (3 runs)' is followed by a 'Median' column that holds the figure."""
        if i is not None and i + 1 < len(head) and head[i + 1].startswith("median") and "median" not in head[i]:
            return i + 1
        return i
    col = {"label": 0, "tokens": find("prompt tokens"), "prompt_tps": median_of(find("prompt tok/s", "prompt t/s")), "decode_tps": median_of(find("decode tok/s", "decode t/s")), "ttft_s": median_of(find("ttft"))}
    if col["prompt_tps"] is None and col["decode_tps"] is None:
        return []
    rows = []
    for line in lines[2:]:
        c = cells(line)
        get = lambda k: number(c[col[k]]) if col[k] is not None and col[k] < len(c) else None
        row = {"label": clean(c[0], 60), "prompt_tokens": get("tokens"), "prompt_tps": get("prompt_tps"), "decode_tps": get("decode_tps"), "ttft_s": get("ttft_s")}
        if row["prompt_tps"] is not None or row["decode_tps"] is not None:
            rows.append(row)
    return rows[:MAX_ROWS]


def headline_row(rows):
    """The row nearest a 4K prompt that reports a prompt speed: what the catalogue's table shows."""
    cand = [r for r in rows if r["prompt_tps"] is not None and r["prompt_tokens"]]
    if not cand:
        return None
    return min(cand, key=lambda r: abs(r["prompt_tokens"] - 4096))


def entry(folder: Path, idx: dict, commit: str):
    name = folder.name
    text = (folder / "README.md").read_text(encoding="utf-8", errors="replace") if (folder / "README.md").exists() else ""
    info = idx.get(name, {})
    b = bullets(text)
    rows = results_rows(text)
    head = headline_row(rows)
    quant = QUANT_RE.search(" ".join(filter(None, [b.get("model"), b.get("model and quantization"), (idx.get(name) or {}).get("summary")])) or text[:2000])
    ctx = re.search(r"([\d,]{4,9})[- ]token context|context[: ]+([\d,]{4,9})", (info.get("summary") or "") + " " + text[:6000], re.I)
    engine = b.get("strata") or b.get("engine")
    date = info.get("date") or (re.match(r"\d{4}-\d{2}-\d{2}", name) or [None])[0]
    return {
        "id": name,
        "date": date,
        "headline": info.get("headline") or clean(text.splitlines()[0].lstrip("# ")) if text else info.get("headline"),
        "summary": info.get("summary"),
        "gpu": b.get("gpu") or b.get("gpu and vram") or b.get("gpu memory"),
        "cpu": b.get("cpu"),
        "ram": b.get("ram") or b.get("memory"),
        "os": b.get("os"),
        "engine": engine,
        "quant": quant.group(1) if quant else None,
        "context": int((ctx.group(1) or ctx.group(2)).replace(",", "")) if ctx else None,
        "rows": rows,
        "headline_row": head,
        "source": {"url": f"{REPO}/tree/{commit}/bench/results/{name}", "readme_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None},
    }


def build(source: Path):
    commit = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() or None
    guide_path = source / "docs" / "COMMUNITY_BENCHMARKS.md"
    idx = index_entries(guide_path.read_text(encoding="utf-8", errors="replace")) if guide_path.exists() else {}
    folders = sorted(p for p in (source / "bench" / "results").iterdir() if p.is_dir() and "community" in p.name)
    entries = [entry(f, idx, commit or "main") for f in folders]
    entries.sort(key=lambda e: (e["date"] or "", e["id"]), reverse=True)
    return {"note": "Reports from the Strata repository, measured with other prompts than strata-bench's. Not comparable with the results.",
            "source": {"repository": REPO, "commit": commit, "license": "MIT"}, "count": len(entries), "reports": entries}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", required=True, help="a checkout of the Strata repository (bench/results and docs/COMMUNITY_BENCHMARKS.md)")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "docs" / "community.json"))
    a = ap.parse_args(argv)
    src = Path(a.source)
    if not (src / "bench" / "results").is_dir():
        print(f"{src}: no bench/results folder", file=sys.stderr)
        return 2
    data = build(src)
    Path(a.out).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    withrow = sum(1 for e in data["reports"] if e["headline_row"])
    print(f"wrote {a.out}: {data['count']} reports, {withrow} with a parsed prompt speed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
