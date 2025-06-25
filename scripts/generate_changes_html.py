import json

INPUT_JSON = 'commits.json'
OUTPUT_HTML = 'commits.html'

UPDATE_TYPES = ["repo", "user", "image", "tag", "sha"]
CHANGE_TYPES = ["created", "updated"]

def generate_html(data):
    html = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Commit Container Updates</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        code { background-color: #f4f4f4; padding: 2px 5px; border-radius: 4px; cursor: pointer; }
        .commit { margin-bottom: 20px; }
        .project { margin-left: 20px; margin-bottom: 10px; }
        .container { margin-left: 40px; }
    </style>
    <script>
        function applyFilters() {
            const updateSelect = document.getElementById('updateTypeFilter');
            const changeSelect = document.getElementById('changeTypeFilter');
            const selectedUpdateTypes = Array.from(updateSelect.selectedOptions).map(o => o.value);
            const selectedChangeTypes = Array.from(changeSelect.selectedOptions).map(o => o.value);

            document.querySelectorAll('.commit').forEach(commit => {
                let visibleProject = false;

                commit.querySelectorAll('.project').forEach(project => {
                    let visibleContainer = false;

                    project.querySelectorAll('.container').forEach(container => {
                        const updateTypes = container.getAttribute('data-update-types').split(',');
                        const changeType = container.getAttribute('data-change-type');

                        const matchUpdate = selectedUpdateTypes.length === 0 || selectedUpdateTypes.some(val => updateTypes.includes(val));
                        const matchChange = selectedChangeTypes.length === 0 || selectedChangeTypes.includes(changeType);

                        const visible = matchUpdate && matchChange;
                        container.style.display = visible ? 'block' : 'none';

                        if (visible) visibleContainer = true;
                    });

                    project.style.display = visibleContainer ? 'block' : 'none';
                    if (visibleContainer) visibleProject = true;
                });

                commit.style.display = visibleProject ? 'block' : 'none';
            });
        }

        function copyToClipboard(text) {
            navigator.clipboard.writeText(text);
        }

        document.addEventListener('DOMContentLoaded', () => {
            document.querySelectorAll('code').forEach(code => {
                code.addEventListener('click', () => copyToClipboard(code.textContent));
            });
        });
    </script>
</head>
<body>
<h1>Commit Container Updates</h1>
<div>
    <label for="updateTypeFilter">Filter by update_type:</label>
    <select id="updateTypeFilter" multiple onchange="applyFilters()">
''' + '\n'.join([f'<option value="{t}">{t}</option>' for t in UPDATE_TYPES]) + '''
    </select>
    <label for="changeTypeFilter">Filter by change_type:</label>
    <select id="changeTypeFilter" multiple onchange="applyFilters()">
''' + '\n'.join([f'<option value="{t}">{t}</option>' for t in CHANGE_TYPES]) + '''
    </select>
</div>
<hr>
'''

    for commit_entry in data:
        commit_html = f'<div class="commit"><strong>Commit:</strong> <code>{commit_entry["commit"]}</code>'
        project_htmls = []

        for project in commit_entry['projects']:
            containers_html = ''
            for container in project['containers']:
                update_types = ','.join(container['update_types'])
                containers_html += f'''<div class="container" data-update-types="{update_types}" data-change-type="{project['change_type']}">
                    <strong>Container:</strong> <code>{container['container_name']}</code><br>
                    <strong>Old Image:</strong> <code>{container['old_image']}</code><br>
                    <strong>New Image:</strong> <code>{container['new_image']}</code><br>
                    <strong>Update Types:</strong> {', '.join(container['update_types'])}
                </div>'''

            if containers_html:
                project_html = f'<div class="project"><strong>Project:</strong> <code>{project["project"]}</code> <em>({project["section"]})</em><br><strong>Change Type:</strong> {project["change_type"]}{containers_html}</div>'
                project_htmls.append(project_html)

        if project_htmls:
            commit_html += ''.join(project_htmls) + '</div>'
            html += commit_html

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
