import json
import yaml
import argparse
from pathlib import Path
from typing import Any

DEFAULT_PRIORITIES = [-2, -1, 0, 1, 2]


def app_to_yaml_entry(app: dict[str, Any]) -> dict[str, Any]:
    """
    Convert a single App definition to a YAML URL entry.
    Priorities are normalized but currently ignored in YAML output.
    """
    name: str = app["name"]
    tags: list[str] = app.get("tags", [])
    priorities: list[int] = app.get("priorities") or DEFAULT_PRIORITIES

    # Normalize priorities to always be a non-empty list
    if not priorities:
        priorities = DEFAULT_PRIORITIES

    joined_tags = ", ".join(tags)

    url = (
        f"pover://{{{{ op://Docker/Apprise/Pushover/User-Key }}}}"
        f"@{{{{ op://Docker/Apprise/Pushover/{name}-Key }}}}"
    )

    # Currently only tags are included in YAML, priorities ignored
    return {url: [{"tag": joined_tags}]}


def json_to_yaml(json_path: Path, yaml_path: Path) -> None:
    """
    Convert a JSON array of apps into a YAML file with a custom format.
    """
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            apps: list[dict[str, Any]] = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON file: {json_path}\n{e}")

    output_list = [app_to_yaml_entry(app) for app in apps]

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
