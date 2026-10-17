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
    col = {"label": 0, "tokens": find("prompt tokens"), "reused": find("reused"), "prompt_tps": median_of(find("prompt tok/s", "prompt t/s")), "decode_tps": median_of(find("decode tok/s", "decode t/s")), "ttft_s": median_of(find("ttft"))}
    if col["prompt_tps"] is None and col["decode_tps"] is None:
        return []
    rows = []
    for line in lines[2:]:
        c = cells(line)
        get = lambda k: number(c[col[k]]) if col[k] is not None and col[k] < len(c) else None
        row = {"label": clean(c[0], 60), "prompt_tokens": get("tokens"), "reused": get("reused"), "prompt_tps": get("prompt_tps"), "decode_tps": get("decode_tps"), "ttft_s": get("ttft_s")}
        if row["prompt_tps"] is not None or row["decode_tps"] is not None:
            rows.append(row)
    return rows[:MAX_ROWS]


SIZES = {"4k": (3500, 4700), "16k": (14000, 17500), "32k": (30000, 36000), "128k": (115000, 135000)}      # the prompt sizes the catalogue shows, as ranges of the real token count
CPU_RE = re.compile(r"Ryzen|Xeon|EPYC|Threadripper|Core\s*(?:Ultra|i\d)|\bi\d-\d|Intel|Apple|\bM\d\b|Opteron|Core Ultra", re.I)
GPU_RE = re.compile(r"RTX|GTX|Radeon|\bRX\b|Tesla|TITAN|Quadro|Arc|Instinct|\bMI\d+|\bP40\b|\bV100\b|A4000|A5000|Pro\s*\d{4}|gfx\d+|8060S|780M|Strix", re.I)
RAM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:GB|GiB)\s*(?:of\s*)?(?:DDR\d\S*\s*)?(?:RAM|unified|system memory|memory)", re.I)


def pick_row(rows, size, quant):
    """The row of a prompt size (4K, 16K, 32K) that reports a prompt speed, a fresh prompt (nothing reused) first, the report's own quantisation first."""
    lo, hi = SIZES[size]
    cand = [r for r in rows if r["prompt_tokens"] and lo <= r["prompt_tokens"] <= hi and r["prompt_tps"] is not None]
    if not cand:
        return None
    fresh = [r for r in cand if not r.get("reused")] or cand
    own = [r for r in fresh if quant and quant.replace("_", "").lower() in (r["label"] or "").replace("_", "").lower()]
    return (own or fresh)[0]


def chat_decode(rows):
    for r in rows:
        if re.search(r"chat", r["label"] or "", re.I) and r["decode_tps"] is not None:
            return r["decode_tps"]
    return None


def humaneval(text):
    """'HumanEval (164, greedy, ...): **158/164**' or 'HumanEval pass@1 87.5%': the score as the README writes it, on the line that names HumanEval.
    A line that only describes the test (the ids it ran, the tasks it counted) has no score and gives None."""
    for line in text.replace("*", "").splitlines():
        if re.search(r"human-?\s?eval", line, re.I):
            m = re.search(r"human-?\s?eval[^\n]{0,120}?:\s*(\d{1,3}\s*/\s*\d{1,3}|\d{1,3}(?:\.\d+)?\s?%)", line, re.I) or re.search(r"human-?\s?eval[^\n|]{0,50}?(\d{1,3}\s*/\s*\d{1,3}|\d{1,3}(?:\.\d+)?\s?%)", line, re.I)
            if m:
                return re.sub(r"\s+", "", m.group(1))
    return None


def hardware(headline, bullet):
    """CPU, RAM, GPU and its VRAM, from the comma-separated headline ('RTX 5090, Core Ultra 9 285K, 64 GB RAM') and the README's bullets."""
    headline = re.split(r"community report,?\s*", headline or "", flags=re.I)[-1]
    headline = re.sub(r"^\([^)]*\):\s*", "", headline)                                   # '(rerun): 2x RTX 5060 Ti' -> '2x RTX 5060 Ti'
    headline = re.sub(r"\s*\(\s*\)", "", headline)                                       # an empty pair of parentheses left by a stripped note
    headline = re.sub(r"^(?:Community benchmark(?: on)?|Benchmark)\s*:?\s*", "", headline, flags=re.I)
    parts = [p.strip() for p in re.split(r",(?![^()]*\))|\s\+\s|\s/\s", headline) if p.strip()]
    parts = [re.sub(r"\s*\(\s*\)", "", re.sub(r"\s*\((?:Windows|Linux|WSL)[^)]*\)", "", p)).strip() for p in parts]
    short = lambda t: re.split(r"[;.](?:\s|$)", t or "")[0]
    cpu = next((p for p in parts if CPU_RE.search(p) and not GPU_RE.search(p)), None) or short(bullet.get("cpu"))
    ram = next((p for p in parts if RAM_RE.search(p)), None) or None
    if ram:
        ram = RAM_RE.search(ram).group(0)                                              # '128 GB RAM - Strata 0.1.41: one card' -> '128 GB RAM'
    gpu = next((p for p in parts if GPU_RE.search(p) and not RAM_RE.search(p)), None)
    if not ram and bullet.get("ram"):
        ram = short(bullet["ram"])
    if not gpu and bullet.get("gpu"):
        gpu = re.sub(r"\s*\(\s*\)", "", short(bullet["gpu"]))
    gpu = re.split(r"\s+with\s+", gpu or "")[0] or None                                   # 'Tesla V100-PCIE-32GB with 16 GB of system RAM' -> the card
    vram = re.search(r"(\d+)\s*(?:GB|GiB)", gpu or "")
    gpu_name = re.sub(r"\s*\d+\s*(?:GB|GiB)\b", "", gpu or "").strip() or None
    return {"cpu": clean(cpu, 80), "ram": clean(ram, 60), "gpu": clean(gpu_name, 80), "vram_gb": int(vram.group(1)) if vram else None}


def strata_version(*texts):
    """The Strata version the report says it ran: after 'Strata' or 'engine', then a 'v0.1.x' written alone, then the folder name."""
    for t in texts:
        m = re.search(r"(?:Strata|engine)[^\n]{0,40}?\bv?(0\.\d+\.\d+(?:\.\d+)?)", t or "")
        if m:
            return m.group(1)
    for t in texts:
        m = re.search(r"\bv(0\.1\.\d+(?:\.\d+)?)\b", t or "")
        if m:
            return m.group(1)
    return None


def entry(folder: Path, idx: dict, commit: str):
    name = folder.name
    text = (folder / "README.md").read_text(encoding="utf-8", errors="replace") if (folder / "README.md").exists() else ""
    info = idx.get(name, {})
    b = bullets(text)
    rows = results_rows(text)
    quant_m = QUANT_RE.search(" ".join(filter(None, [b.get("model"), b.get("model and quantization"), info.get("summary")])) or text[:2000])
    quant = quant_m.group(1) if quant_m else None
    ctx = re.search(r"([\d,]{4,9})[- ]token context|context[: ]+([\d,]{4,9})", (info.get("summary") or "") + " " + text[:6000], re.I)
    date = info.get("date") or (re.match(r"\d{4}-\d{2}-\d{2}", name) or [None])[0]
    headline = info.get("headline") or (clean(text.splitlines()[0].lstrip("# ")) if text else None)
    hw = hardware(headline, b)
    board = next((v for k, v in b.items() if k in ("board", "motherboard", "mainboard")), None)
    sizes = {k: pick_row(rows, k, quant) for k in SIZES}
    four = sizes["4k"]
    return {
        "id": name,
        "date": date,
        "headline": headline,
        "summary": info.get("summary"),
        **hw,
        "board": clean(board, 80),
        "strata": strata_version(b.get("strata"), info.get("summary"), headline, text[:4000], name),
        "quant": quant,
        "context": int((ctx.group(1) or ctx.group(2)).replace(",", "")) if ctx else None,
        "chat_decode_tps": chat_decode(rows),
        "prompt_tps": {k: (r["prompt_tps"] if r else None) for k, r in sizes.items()},
        "decode_tps": four["decode_tps"] if four else None,
        "humaneval": humaneval(text),
        "rows": rows,
        "source": {"url": f"{REPO}/tree/{commit}/bench/results/{name}", "readme_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None},
    }


# A report is "full" when it states all four speeds the page is about: the prompt speed at 4K, 32K and 128K, and the decode speed.
# Every report is kept; the page lets the reader show all of them, only the full ones, or only the ones with at least one speed.
def is_full(e):
    return all(v is not None for v in (e["prompt_tps"]["4k"], e["prompt_tps"]["32k"], e["prompt_tps"]["128k"], e["decode_tps"]))


def has_speed(e):
    return any(v is not None for v in (e["prompt_tps"]["4k"], e["prompt_tps"]["32k"], e["prompt_tps"]["128k"], e["decode_tps"]))


def build(source: Path):
    commit = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() or None
    guide_path = source / "docs" / "COMMUNITY_BENCHMARKS.md"
    idx = index_entries(guide_path.read_text(encoding="utf-8", errors="replace")) if guide_path.exists() else {}
    folders = sorted(p for p in (source / "bench" / "results").iterdir() if p.is_dir() and "community" in p.name)
    entries = [entry(f, idx, commit or "main") for f in folders]
    for e in entries:
        e["full"] = is_full(e)
    entries.sort(key=lambda e: (e["date"] or "", e["id"]), reverse=True)
    return {"note": "Reports from the Strata repository, measured with other prompts than strata-bench's. Not comparable with the results.",
            "source": {"repository": REPO, "commit": commit, "license": "MIT"},
            "full_means": "states the prompt speed at 4K, 32K and 128K and the decode speed",
            "count": len(entries), "full": sum(1 for e in entries if e["full"]), "with_speed": sum(1 for e in entries if has_speed(e)), "reports": entries}


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
    got = lambda f: sum(1 for e in data["reports"] if f(e))
    print(f"wrote {a.out}: {data['count']} reports, {data['full']} full, {data['with_speed']} with a speed; with a 4K / 16K / 32K prompt speed: {got(lambda e: e['prompt_tps']['4k'] is not None)} / {got(lambda e: e['prompt_tps']['16k'] is not None)} / {got(lambda e: e['prompt_tps']['32k'] is not None)}; chat decode {got(lambda e: e['chat_decode_tps'] is not None)}; HumanEval {got(lambda e: e['humaneval'])}; board {got(lambda e: e['board'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
