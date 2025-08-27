import json
import yaml
import sys
from pathlib import Path


def json_to_yaml(json_path, yaml_path):
    # Load the JSON file
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    output_list = []

    for key, values in data.items():
        # Join values with ", "
        joined_tags = ", ".join(values)
        # Construct the dictionary structure for YAML
        output_list.append({
            f"pover://{{{{ op://Docker/Apprise/Pushover/User-Key }}}}@{{{{ op://Docker/Apprise/Pushover/{key}-Key }}}}": [
                {"tag": joined_tags}
            ]
        })

    # Prepare the final YAML content with version at the top
    final_output = {
        "version": 1,
        "urls": output_list
    }

    # Write YAML to file
    with open(yaml_path, 'w', encoding='utf-8') as f:
        yaml.dump(final_output, f, sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: python {sys.argv[0]} <input.json> <output.yml>")
        sys.exit(1)

    json_path = Path(sys.argv[1])
    yaml_path = Path(sys.argv[2])

    json_to_yaml(json_path, yaml_path)
    print(f"YAML written to {yaml_path}")
