import json

# Input: JSON file path and output HTML file path
INPUT_JSON = 'commits.json'
OUTPUT_HTML = 'commits.html'

def generate_html(data):
    html = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Commit Container Updates</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        code { background-color: #f4f4f4; padding: 2px 5px; border-radius: 4px; }
        .commit { margin-bottom: 20px; }
        .project { margin-left: 20px; margin-bottom: 10px; }
        .container { margin-left: 40px; }
        .hidden { display: none; }
    </style>
    <script>
        function applyFilters() {
            const updateType = document.getElementById('updateTypeFilter').value;
            const changeType = document.getElementById('changeTypeFilter').value;
            const containers = document.querySelectorAll('.container');

            containers.forEach(container => {
                const updateTypes = container.getAttribute('data-update-types').split(',');
                const changeTypeValue = container.getAttribute('data-change-type');

                const matchesUpdate = !updateType || updateTypes.includes(updateType);
                const matchesChange = !changeType || changeTypeValue === changeType;

                container.style.display = (matchesUpdate && matchesChange) ? 'block' : 'none';
            });
        }
    </script>
</head>
<body>
<h1>Commit Container Updates</h1>
<div>
    <label for="updateTypeFilter">Filter by update_type:</label>
    <select id="updateTypeFilter" onchange="applyFilters()">
        <option value="">All</option>
        <option value="repo">repo</option>
        <option value="user">user</option>
        <option value="image">image</option>
        <option value="tag">tag</option>
        <option value="sha">sha</option>
    </select>
    <label for="changeTypeFilter">Filter by change_type:</label>
    <select id="changeTypeFilter" onchange="applyFilters()">
        <option value="">All</option>
        <option value="created">created</option>
        <option value="updated">updated</option>
    </select>
</div>
<hr>
'''

    for commit_entry in data:
        html += f'<div class="commit"><strong>Commit:</strong> <code>{commit_entry["commit"]}</code>'
        for project in commit_entry['projects']:
            html += f'<div class="project"><strong>Project:</strong> {project["project"]} <em>({project["section"]})</em><br><strong>Change Type:</strong> {project["change_type"]}'
            for container in project['containers']:
                update_types = ','.join(container['update_types'])
                html += f'''<div class="container" data-update-types="{update_types}" data-change-type="{project['change_type']}">
                    <strong>Container:</strong> {container['container_name']}<br>
                    <strong>Old Image:</strong> {container['old_image']}<br>
                    <strong>New Image:</strong> {container['new_image']}<br>
                    <strong>Update Types:</strong> {', '.join(container['update_types'])}
                </div>'''
            html += '</div>'  # close project div
        html += '</div>'  # close commit div

    html += '</body>\n</html>'
    return html

def main():
    with open(INPUT_JSON, 'r') as f:
        data = json.load(f)

    html_content = generate_html(data)

    with open(OUTPUT_HTML, 'w') as f:
        f.write(html_content)

    print(f"HTML output written to {OUTPUT_HTML}")

if __name__ == '__main__':
    main()
