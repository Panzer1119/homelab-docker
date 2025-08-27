import json
import yaml
import argparse
from pathlib import Path
from typing import Any

DEFAULT_PRIORITIES = [-2, -1, 0, 1, 2]
DEFAULT_PRIORITY = 0
PRIORITY_NAMES = {
    -2: "low",
    -1: "moderate",
    0: "normal",
    1: "high",
    2: "emergency",
}


def app_to_yaml_entries(app: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Convert a single App definition to multiple YAML URL entries,
    one for each priority level.
    If the current priority equals the app's defaultPriority,
    include the raw tags as well as the suffixed tags.
    """
    name: str = app["name"]
    tags: list[str] = app.get("tags", [])
    priorities: list[int] = app.get("priorities") or DEFAULT_PRIORITIES
    default_priority: int = app.get("defaultPriority", DEFAULT_PRIORITY)

    if not priorities:
        priorities = DEFAULT_PRIORITIES

    url = (
        f"pover://{{{{ op://Docker/Apprise/Pushover/User-Key }}}}"
        f"@{{{{ op://Docker/Apprise/Pushover/{name}-Key }}}}"
    )

    entries = []
    for prio in priorities:
        prio_name = PRIORITY_NAMES.get(prio, str(prio))  # fallback to str if unknown

        if prio == default_priority:
            # include both raw tags and suffixed tags
            tagged = tags + [f"{tag}-{prio_name}" for tag in tags]
        else:
            tagged = [f"{tag}-{prio_name}" for tag in tags]

        joined_tags = ", ".join(tagged)
        entries.append({url: [{"tag": joined_tags}]})

    return entries


def json_to_yaml(json_path: Path, yaml_path: Path) -> None:
    """
    Convert a JSON array of apps into a YAML file with a custom format.
    """
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            apps: list[dict[str, Any]] = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON file: {json_path}\n{e}")

    output_list = []
    for app in apps:
        output_list.extend(app_to_yaml_entries(app))

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
            width=200
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
