#!/usr/bin/env python3
"""Builds docs/data.json, the only data file of the dashboard, from results/**/*.result.json.

Every result must pass the public-export gate (strata_bench.validate); one that does not stops the build, so nothing unvalidated can reach the page.
Only whitelisted fields are copied. The output is deterministic (no timestamps), so CI can check that the committed file is current:

  python3 tools/build_site.py            write docs/data.json
  python3 tools/build_site.py --check    exit 1 if docs/data.json is not what the results produce
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from strata_bench import validate  # noqa: E402

SIZE_ORDER = ("short", "medium", "long", "xlong", "probe", "gprobe150", "gprobe300", "gprobe450")


def med(x):
    return x.get("median") if isinstance(x, dict) else None


def row(rel: str, doc: dict) -> dict:
    m, e, mo = doc.get("machine") or {}, doc.get("engine") or {}, doc.get("model") or {}
    cfg, val = doc.get("config") or {}, doc.get("validity") or {}
    g0 = (m.get("gpus") or [{}])[0] or {}
    sizes = {}
    for name in SIZE_ORDER:
        s = (doc.get("sizes") or {}).get(name)
        if isinstance(s, dict):
            sizes[name] = {k: med(s.get(k)) for k in ("prompt_tps", "decode_tps", "prompt_ms", "wall_ms")}
            sizes[name].update(read_median=s.get("read_median"), reused_median=s.get("reused_median"))
    return {
        "id": rel,
        "label": doc.get("label"),
        "date": (doc.get("submission") or {}).get("date"),
        "by": (doc.get("submission") or {}).get("by"), "machine_id": m.get("machine_id"),
        "status": val.get("status"),
        "warnings": len(val.get("warnings") or []),
        "failure": val.get("failure"),
        "gpu": g0.get("name"), "vram_gb": g0.get("vram_gb"), "gpu_count": len(m.get("gpus") or []) or None,
        "cpu": m.get("cpu"), "ram_gb": m.get("ram_gb"), "os": m.get("os"), "driver": m.get("driver"),
        "engine": e.get("version") or doc.get("build_info"), "commit": e.get("commit"), "backend": e.get("backend"),
        "model": mo.get("family") or doc.get("model_path"), "quant": mo.get("quant"), "n_ctx": doc.get("n_ctx"),
        "config_id": cfg.get("config_id"), "config_origin": cfg.get("config_origin"),
        "args": cfg.get("args"),
        "sizes": sizes,
    }


def build(results_dir: Path = ROOT / "results") -> dict:
    rows, bad = [], []
    for path in sorted(results_dir.rglob("*.result.json")):
        rel = path.relative_to(results_dir.parent).as_posix()
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            bad.append(f"{rel}: not JSON ({e})")
            continue
        probs = validate.problems(doc)
        if probs:
            bad.extend(f"{rel}: {p}" for p in probs)
            continue
        rows.append(row(rel, doc))
    if bad:
        raise SystemExit("These results do not pass the public-export gate, so the page was not built:\n  " + "\n  ".join(bad))
    rows.sort(key=lambda r: (r["date"] or "", r["id"]), reverse=True)
    return {"protocol": "standard-v1", "count": len(rows), "results": rows}


def dump(data: dict) -> str:
    return json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--results", type=Path, default=ROOT / "results")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "data.json")
    a = ap.parse_args(argv)
    text = dump(build(a.results))
    if a.check:
        if not a.out.exists() or a.out.read_text(encoding="utf-8") != text:
            print(f"{a.out} is out of date: run `python3 tools/build_site.py` and commit it")
            return 1
        print(f"{a.out} is up to date")
        return 0
    a.out.write_text(text, encoding="utf-8")
    print(f"wrote {a.out} ({json.loads(text)['count']} results)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
