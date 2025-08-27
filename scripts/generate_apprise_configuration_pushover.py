import json
import yaml
import argparse
from pathlib import Path


def json_to_yaml(json_path: Path, yaml_path: Path) -> None:
    """
    Convert a JSON file of the form { "Key": ["tag1", "tag2"], ... }
    into a YAML file with a custom format.
    """
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON file: {json_path}\n{e}")

    output_list = []

    for key, values in data.items():
        joined_tags = ", ".join(values)
        url = (
            f"pover://{{{{ op://Docker/Apprise/Pushover/User-Key }}}}"
            f"@{{{{ op://Docker/Apprise/Pushover/{key}-Key }}}}"
        )
        output_list.append({url: [{"tag": joined_tags}]})

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
        description="Convert JSON of tags into formatted YAML for Apprise."
    )
    parser.add_argument("json_path", type=Path, help="Path to input JSON file")
    parser.add_argument("yaml_path", type=Path, help="Path to output YAML file")

    args = parser.parse_args()

    json_to_yaml(args.json_path, args.yaml_path)
    print(f"✅ YAML written to {args.yaml_path}")


if __name__ == "__main__":
    main()
