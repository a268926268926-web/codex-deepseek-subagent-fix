#!/usr/bin/env python3
"""Build an isolated catalog for one routed alias; preserve every other model."""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True, help="Pinned upstream models-manager/models.json")
parser.add_argument("--output", required=True)
parser.add_argument("--model", default="gpt-5.6-sol")
args = parser.parse_args()
catalog = json.loads(Path(args.source).read_text())
matches = [model for model in catalog["models"] if model["slug"] == args.model]
if len(matches) != 1:
    raise SystemExit(f"Expected exactly one catalog entry for {args.model}")
matches[0].update(
    tool_mode="direct",
    supports_search_tool=False,
    use_responses_lite=False,
    multi_agent_version="v2",
)
output = Path(args.output)
if output.exists():
    raise SystemExit("Output already exists; choose a new isolated path")
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
print(f"Wrote {output}; only {args.model} tool compatibility fields changed")
