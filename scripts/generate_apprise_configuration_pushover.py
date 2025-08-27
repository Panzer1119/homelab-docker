import json
import yaml
import argparse
from pathlib import Path
from typing import Any


def json_to_yaml(json_path: Path, yaml_path: Path) -> None:
    """
    Convert a JSON array of apps into a YAML file with a custom format.

    JSON structure:
    [
      {
        "name": "AppName",
        "tags": ["tag1", "tag2"],
        "priorities": [1, 2]   # optional
      },
      ...
    ]
    """
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            apps: list[dict[str, Any]] = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON file: {json_path}\n{e}")

    output_list = []

    for app in apps:
        name = app["name"]
        tags = app.get("tags", [])
        priorities = app.get("priorities", [])

        joined_tags = ", ".join(tags)
        joined_priorities = ", ".join(str(p) for p in priorities) if priorities else None

        url = (
            f"pover://{{{{ op://Docker/Apprise/Pushover/User-Key }}}}"
            f"@{{{{ op://Docker/Apprise/Pushover/{name}-Key }}}}"
        )

        entry = {url: [{"tag": joined_tags}]}

        if joined_priorities:
            entry[url].append({"priority": joined_priorities})

        output_list.append(entry)

    final_output = {"version": 1, "urls": output_list}

    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            final_output,
            f,
            explicit_start=True,  # adds ---
            default_flow_style=False,
            indent=2,
            allow_unicode=True,
            sort_keys=False,
        )


def main():
    parser = argparse.ArgumentParser(
        description="Convert JSON of apps into formatted YAML for Apprise."
    )
    parser.add_argument("json_path", type=Path, help="Path to input JSON file")
    parser.add_argument("yaml_path", type=Path, help="Path to output YAML file")

    args = parser.parse_args()

    json_to_yaml(args.json_path, args.yaml_path)
    print(f"✅ YAML written to {args.yaml_path}")


if __name__ == "__main__":
    main()
