#!/usr/bin/env python3
"""Builds docs/data.json, the only data file of the dashboard, from results/**/*.result.json.

Every result must pass the public-export gate (strata_bench.validate); one that does not stops the build, so nothing unvalidated can reach the page.
Only whitelisted fields are copied. The output is deterministic (no timestamps), so CI can check that the committed file is current:

  python3 tools/build_site.py            write docs/data.json
  python3 tools/build_site.py --check    exit 1 if docs/data.json is not what the results produce
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from strata_bench import schema, validate  # noqa: E402

SIZE_ORDER = ("short", "medium", "long", "xlong", "chat", "probe", "gprobe150", "gprobe300", "gprobe450")


def gpu_name(name):
    """The GPU as the dashboard shows, groups and filters it: a leading vendor word ("NVIDIA GeForce RTX 5090" and "RTX 5090" are the same card), a note in parentheses and the PCIe suffix are dropped. The result file keeps what was declared."""
    if not isinstance(name, str):
        return name
    short = re.sub(r"\s*\([^)]*\)", "", name.strip())
    short = re.sub(r"[- ]PCIE(?:[- ]?\d+GB)?\b", "", short, flags=re.I)
    return re.sub(r"^(?:(?:NVIDIA|GeForce|AMD)\s+)+", "", short.strip(), flags=re.I).strip() or name


def model_name(name):
    """The model family as the dashboard shows it: a note in parentheses is dropped (the quantization has its own column)."""
    if not isinstance(name, str):
        return name
    return re.sub(r"\s*\([^)]*\)", "", name).strip() or name


def med(x):
    return x.get("median") if isinstance(x, dict) else None


def row(rel: str, doc: dict) -> dict:
    m, e, mo = doc.get("machine") or {}, doc.get("engine") or {}, doc.get("model") or {}
    cfg, val = doc.get("config") or {}, doc.get("validity") or {}
    gpus = [g for g in (m.get("gpus") or []) if isinstance(g, dict)]
    g0 = gpus[0] if gpus else {}
    sizes = {}
    for name in SIZE_ORDER:
        s = (doc.get("sizes") or {}).get(name)
        if isinstance(s, dict):
            sizes[name] = {k: med(s.get(k)) for k in ("prompt_tps", "decode_tps", "prompt_ms", "wall_ms", "ttft_ms")}
            sizes[name].update(read_median=s.get("read_median"), reused_median=s.get("reused_median"))
    proto, runner, agent = doc.get("protocol") or {}, doc.get("runner") or {}, doc.get("agent") or {}
    return {
        "id": rel,
        "protocol": proto.get("id"), "suite": proto.get("suite"), "host_relation": runner.get("host_relation"),
        "agent": {"hit_pct": med(agent.get("hit_pct")), "reread_tokens": med(agent.get("reread_tokens")),
                  "ttft_turn1_ms": med(((agent.get("turns") or {}).get("1") or {}).get("ttft_ms")),
                  "ttft_last_turn_ms": med(((agent.get("turns") or {}).get(str(len(agent.get("turns") or {}))) or {}).get("ttft_ms"))} if agent else None,
        "clients": [{k: c.get(k) for k in ("n", "total_tps", "ttft_p95_ms", "errors")} for c in doc.get("clients") or []] or None,
        "label": doc.get("label"),
        "date": (doc.get("submission") or {}).get("date"),
        "by": (doc.get("submission") or {}).get("by"), "machine_id": m.get("machine_id"),
        "status": val.get("status"),
        "warnings": len(val.get("warnings") or []),
        "failure": val.get("failure"),
        "gpu": gpu_name(g0.get("name")), "vram_gb": g0.get("vram_gb"), "gpu_count": len(m.get("gpus") or []) or None,
        "gpu_other": sum(1 for g in gpus[1:] if gpu_name(g.get("name")) != gpu_name(g0.get("name"))),      # cards of another kind beside the first (a display card, say)
        "vram_total_gb": vram_total(gpus),
        "gpu_full": ", ".join(str(g.get("name")) + (" " + str(g["vram_gb"]) + " GB" if g.get("vram_gb") else "") for g in gpus if g.get("name")) or None,
        "model_full": mo.get("family") or doc.get("model_path"),
        "cpu": m.get("cpu"), "ram_gb": m.get("ram_gb"), "os": m.get("os"), "driver": m.get("driver"),
        "engine": e.get("version") or doc.get("build_info"), "commit": e.get("commit"), "backend": e.get("backend"),
        "engine_source": e.get("source"), "binary": (e.get("binary_sha256") or "")[:8] or None,      # two builds with one version number (official and a fork) are different engines
        "model": model_name(mo.get("family") or doc.get("model_path")), "quant": mo.get("quant"), "n_ctx": doc.get("n_ctx"),
        "config_id": cfg.get("config_id"), "config_origin": cfg.get("config_origin"),
        "args": cfg.get("args"),
        "sizes": sizes,
    }


def vram_total(gpus: list):
    """The memory of a rig whose cards are all the same size (a few cards of one size are one pool of memory); None for one card,
    and for a rig with a small display card beside the compute card, where the total would claim memory the model never uses."""
    sizes = [g.get("vram_gb") for g in gpus]
    if len(sizes) < 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in sizes) or len(set(sizes)) != 1:
        return None
    total = sum(sizes)
    return int(total) if total == int(total) else total


def build(results_dir: Path = ROOT / "results", copies: dict = None) -> dict:
    """`copies`, when given, is filled with {path relative to the results folder: the validated file's text}: the detail
    page reads these copies, because GitHub Pages publishes only docs/."""
    rows, bad = [], []
    for path in sorted(results_dir.rglob("*.result.json")):
        rel = path.relative_to(results_dir.parent).as_posix()
        raw = path.read_bytes()
        try:
            doc = schema.strict_loads(raw.decode("utf-8"))
        except ValueError as e:
            bad.append(f"{rel}: not JSON ({e})")
            continue
        legacy = hashlib.sha256(raw).hexdigest() in validate.LEGACY       # published before the fixed profiles: still shown, and marked
        probs = validate.gate(doc, legacy=legacy)
        if probs:
            bad.extend(f"{rel}: {p}" for p in probs)
            continue
        rows.append({**row(rel, doc), **({"legacy": True} if legacy else {})})
        if copies is not None:
            copies[path.relative_to(results_dir).as_posix()] = path.read_text(encoding="utf-8")
    if bad:
        raise SystemExit("These results do not pass the public-export gate, so the page was not built:\n  " + "\n  ".join(bad))
    rows.sort(key=lambda r: (r["date"] or "", r["id"]), reverse=True)
    return {"protocols": sorted({r["protocol"] for r in rows}), "count": len(rows), "results": rows}


def dump(data: dict) -> str:
    return json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"


def sync_copies(copies: dict, dest: Path, check: bool) -> list:
    """Makes dest hold exactly the validated result files (and nothing else named *.result.json). Returns what was off."""
    off = []
    for rel, text in sorted(copies.items()):
        f = dest / rel
        if not f.exists() or f.read_text(encoding="utf-8") != text:
            off.append(f"{f} is missing or stale")
            if not check:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(text, encoding="utf-8")
    if dest.exists():
        for f in sorted(dest.rglob("*.result.json")):
            if f.relative_to(dest).as_posix() not in copies:
                off.append(f"{f} has no source in results/")
                if not check:
                    f.unlink()
    return off


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--results", type=Path, default=ROOT / "results")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "data.json")
    a = ap.parse_args(argv)
    copies = {}
    text = dump(build(a.results, copies))
    dest = a.out.parent / "results"                                              # docs/results/: the validated files the detail page reads
    if a.check:
        off = sync_copies(copies, dest, True)
        if not a.out.exists() or a.out.read_text(encoding="utf-8") != text:
            off.insert(0, f"{a.out} is out of date")
        if off:
            print("\n".join(off) + "\nrun `python3 tools/build_site.py` and commit the result")
            return 1
        print(f"{a.out} and {dest} are up to date")
        return 0
    a.out.write_text(text, encoding="utf-8")
    sync_copies(copies, dest, False)
    print(f"wrote {a.out} ({json.loads(text)['count']} results) and {len(copies)} file(s) in {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
