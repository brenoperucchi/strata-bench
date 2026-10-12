"""strata-bench command line.

  python3 -m strata_bench run --base URL --config RUN-CONFIG.json --origin setup-default|custom-N
                              --label LABEL --out DIR [--sizes probe,medium,long,xlong[,gprobe][,chat][,agent][,clients]] [--identity IDENTITY.json]
  python3 -m strata_bench validate RESULT.json [...]

Exit codes of run: 0 done, 2 preflight refused (nothing measured), 3 incomplete (partial result kept).
"""
import argparse
import json
import os
import sys
from pathlib import Path

from . import VERSION, core, schema, validate


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="strata_bench", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="measure a running server")
    r.add_argument("--base", required=True)
    r.add_argument("--config", required=True, type=Path)
    r.add_argument("--origin", required=True)
    r.add_argument("--label", required=True)
    r.add_argument("--out", required=True, type=Path)
    r.add_argument("--protocol", default=core.PROTOCOL, choices=[core.PROTOCOL, core.PROTOCOL_V2],
                   help="standard-v1 (default) or standard-v2: streamed requests, adds the time to the first token")
    r.add_argument("--host-relation", choices=list(core.HOST_RELATIONS),
                   help="standard-v2: where this machine runs relative to the server (default same-host for a loopback --base)")
    r.add_argument("--sizes", help="what to measure (default probe,medium,long,xlong; with standard-v2 and no --suite, the standard suite)")
    r.add_argument("--suite", choices=list(schema.SUITE_SIZES),
                   help="standard-v2 profile: quick (chat, medium, long), standard (adds xlong and the agent block) or full (adds clients); replaces --sizes")
    r.add_argument("--identity", type=Path)
    r.add_argument("--by", help="optional public name to list the result under (for example your GitHub name); declared, not verified")
    r.add_argument("--probe-offset", type=int, default=0)
    r.add_argument("--api-key", default=os.environ.get("STRATA_API_KEY"), help="default $STRATA_API_KEY; never written")
    v = sub.add_parser("validate", help="check result files for public export")
    v.add_argument("files", nargs="+", type=Path)
    a = p.parse_args(argv)
    if a.cmd == "run":
        if a.suite and a.sizes:
            p.error("--suite and --sizes cannot be used together: a suite is its own list of blocks")
        if a.suite and a.protocol != core.PROTOCOL_V2:
            p.error("--suite needs --protocol standard-v2")
        suite = a.suite or ("standard" if a.protocol == core.PROTOCOL_V2 and not a.sizes else "custom")
        sizes = [s for s in (a.sizes or "probe,medium,long,xlong").split(",") if s]
        bad = [s for s in sizes if s not in ("probe", "gprobe") and s not in core.MEASURED]
        if bad:
            p.error(f"unknown sizes: {bad}")
        cfg = schema.strict_loads(a.config.read_text(encoding="utf-8-sig"))
        ident = schema.strict_loads(a.identity.read_text(encoding="utf-8")) if a.identity else None
        return core.run(a.base.rstrip("/"), cfg, a.origin, sizes, a.label, a.out, ident,
                        probe_offset=a.probe_offset, api_key=a.api_key, by=a.by, protocol=a.protocol,
                        host_relation=a.host_relation, suite=suite)
    rc = 0
    for f in a.files:
        try:
            probs = validate.problems(schema.strict_loads(f.read_text(encoding="utf-8")))
        except ValueError as e:
            probs = [f"not valid JSON: {e}"]
        print(f"{f}: {'OK' if not probs else 'REJECTED'}")
        for x in probs:
            print("  -", x)
        rc |= bool(probs)
    return rc


if __name__ == "__main__":
    sys.exit(main())
