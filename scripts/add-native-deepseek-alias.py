#!/usr/bin/env python3
"""Replace the temporary WorkBuddy alias entry with its native DeepSeek slug.

The source alias is used only as a catalog template. It is removed by default so
the client model picker publishes one DeepSeek entry.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import tempfile


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--catalog", type=Path, required=True)
parser.add_argument("--source-alias", default="gpt-5.6-luna")
parser.add_argument("--native-slug", default="cn:deepseek-v4.1-flash")
parser.add_argument("--keep-source", action="store_true",
                    help="Keep the source alias in addition to the native slug")
args = parser.parse_args()

catalog = json.loads(args.catalog.read_text())
models = catalog.get("models")
if not isinstance(models, list):
    raise SystemExit("Catalog does not contain a models array")
sources = [model for model in models if model.get("slug") == args.source_alias]
targets = [model for model in models if model.get("slug") == args.native_slug]
if len(targets) > 1:
    raise SystemExit("Native catalog alias is ambiguous")

changed = False
if not targets:
    if len(sources) != 1:
        raise SystemExit("Catalog source alias is missing or ambiguous")
    native = copy.deepcopy(sources[0])
    native.update(
        slug=args.native_slug,
        display_name="DeepSeek V4.1 Flash (WorkBuddy CN)",
        description="Native deepseek-v4.1-flash name forwarded directly through CCS.",
        visibility="list",
    )
    models.append(native)
    changed = True

if not args.keep_source and sources:
    catalog["models"] = [model for model in models if model.get("slug") != args.source_alias]
    changed = True

preferred = ["gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol", args.source_alias,
             args.native_slug]
order = {slug: index for index, slug in enumerate(preferred)}
catalog["models"].sort(key=lambda model: (
    order.get(model.get("slug"), len(order)), model.get("slug", "")))

with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=args.catalog.parent,
        prefix=".routed-models-", delete=False) as stream:
    temporary = Path(stream.name)
    os.fchmod(stream.fileno(), 0o600)
    stream.write(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
    stream.flush()
    os.fsync(stream.fileno())
os.replace(temporary, args.catalog)
print(json.dumps({
    "status": "PRESENT",
    "native_slug": args.native_slug,
    "source_alias_retained": args.keep_source,
    "changed": changed,
}, ensure_ascii=False))
