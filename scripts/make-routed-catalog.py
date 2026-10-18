#!/usr/bin/env python3
"""Build an isolated catalog for explicit routed aliases; preserve every other model."""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True, help="Pinned upstream models-manager/models.json")
parser.add_argument("--output", required=True)
parser.add_argument("--model", action="append", help="Repeat for each mapped V2 alias; defaults to gpt-5.6-sol")
parser.add_argument("--workbuddy-model", action="append", default=[],
                    help="Also mark this alias as WorkBuddy CN DeepSeek (low/high/max)")
args = parser.parse_args()
catalog = json.loads(Path(args.source).read_text())
aliases = list(dict.fromkeys([*(args.model or ["gpt-5.6-sol"]), *args.workbuddy_model]))
for alias in aliases:
    matches = [model for model in catalog["models"] if model["slug"] == alias]
    if len(matches) != 1:
        raise SystemExit(f"Expected exactly one catalog entry for {alias}")
    if matches[0].get("tool_mode") not in {"code_mode", "code_mode_only"}:
        raise SystemExit(f"Expected a Code Mode entry for {alias}; direct mode hides desktop exec")
    matches[0].update(
        supports_search_tool=False,
        use_responses_lite=False,
        multi_agent_version="v2",
    )
    if alias in args.workbuddy_model:
        levels = [level for level in matches[0].get("supported_reasoning_levels", [])
                  if level.get("effort") in {"low", "high", "max"}]
        if {level.get("effort") for level in levels} != {"low", "high", "max"}:
            raise SystemExit(f"Catalog lacks the WorkBuddy low/high/max levels for {alias}")
        matches[0].update(
            display_name=matches[0].get("display_name", alias) + " (WorkBuddy DeepSeek)",
            description="WorkBuddy CN DeepSeek via the local V2 relay and CCS aggregation.",
            default_reasoning_level="high",
            supported_reasoning_levels=levels,
            multi_agent_reasoning_effort="max",
            prefer_websockets=False,
            default_service_tier=None,
            service_tiers=[],
            additional_speed_tiers=[],
            upgrade=None,
        )
output = Path(args.output)
if output.exists():
    raise SystemExit("Output already exists; choose a new isolated path")
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
print(f"Wrote {output}; routed aliases: {', '.join(aliases)}")
