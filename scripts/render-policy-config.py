#!/usr/bin/env python3
"""render-policy-config.py: render the Omnigent server policy config from the pack's parameters.

  --params FILE   an overlay parameter file merged over policy/params.default.json
  --out FILE      write the rendering there (default: policy/config.yaml)
  --check         exit non-zero when --out differs from the rendering (nothing is written)

The output is JSON, which YAML accepts. A parameter file that does not validate is refused.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "policy"))
from forge_policies import params  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--params")
    ap.add_argument("--out", default=os.path.join(ROOT, "policy", "config.yaml"))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    override = None
    if args.params:
        try:
            override = params.load_json(args.params)
        except (OSError, ValueError) as exc:
            print(f"render-policy-config: cannot read {args.params}: {exc}", file=sys.stderr)
            return 2
        if not isinstance(override, dict):
            print("render-policy-config: parameters: must be a JSON object", file=sys.stderr)
            return 2
    merged = params.merge(params.load_defaults(), override)
    errors = params.validate(merged)
    if errors:
        for err in errors:
            print(f"render-policy-config: {err}", file=sys.stderr)
        return 2
    text = params.render_text(merged)
    if args.check:
        try:
            with open(args.out, encoding="utf-8") as fh:
                current = fh.read()
        except OSError:
            current = None
        if current != text:
            print(f"render-policy-config: {args.out} is stale; re-run without --check", file=sys.stderr)
            return 1
        print(f"render-policy-config: {args.out} is current")
        return 0
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"render-policy-config: wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
