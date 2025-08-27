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

    # Write YAML with pretty formatting and --- at the top
    with open(yaml_path, 'w', encoding='utf-8') as f:
        yaml.dump(
            final_output,
            f,
            # default_style='"', # Quotes everything
            default_flow_style=False,  # Makes it block style (pretty)
            # encoding='utf-8',
            explicit_start=True,
            # explicit_end=True, # Adds three dots at the end of the file
            indent=2,
            allow_unicode=True,
            sort_keys=False,
        )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: python {sys.argv[0]} <input.json> <output.yml>")
        sys.exit(1)

    json_path = Path(sys.argv[1])
    yaml_path = Path(sys.argv[2])

    json_to_yaml(json_path, yaml_path)
    print(f"YAML written to {yaml_path}")
