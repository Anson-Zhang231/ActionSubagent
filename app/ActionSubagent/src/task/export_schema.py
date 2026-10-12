"""Export the delegated data-part contract directly from the Python input model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import DelegatedTaskInput


def build_schema() -> dict:
    schema = DelegatedTaskInput.model_json_schema(mode="validation")
    versions = {
        definition["properties"]["contract_version"]["const"]
        for definition in schema["$defs"].values()
        if "contract_version" in definition.get("properties", {})
    }
    if len(versions) != 1:
        raise ValueError("The exported input model must declare exactly one contract version")
    version = versions.pop()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:action-subagent:a2a:delegated-task-input:{version}",
        **schema,
    }


def main() -> int:
    schema = build_schema()
    version = schema["$id"].rsplit(":", 1)[1]
    default = (
        Path(__file__).resolve().parents[1]
        / "schemas"
        / f"a2a-delegated-task-input.v{version}.schema.json"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=default)
    parser.add_argument(
        "--check", action="store_true", help="Fail on missing/stale output without writing"
    )
    args = parser.parse_args()
    if args.check:
        try:
            current = json.loads(args.output.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = None
        if current != schema:
            parser.exit(
                1, f"Schema is missing or stale: {args.output}\nRegenerate from the input model.\n"
            )
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
