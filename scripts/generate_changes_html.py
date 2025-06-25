import json
import os
from collections import defaultdict

INPUT_JSON = os.getenv('INPUT_JSON', 'commits.json')
OUTPUT_HTML = os.getenv('OUTPUT_HTML', 'commits.html')

UPDATE_TYPES = ["repo", "user", "image", "tag", "sha"]
CHANGE_TYPES = ["created", "updated", "deleted"]

def generate_html(data):
    html = '<!DOCTYPE html>'
    html += '''
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
        .created { color: green; font-weight: bold; }
        .updated { color: blue; font-weight: bold; }
        .deleted { color: red; font-weight: bold; }
        .section-divider { border-top: 2px solid #000; margin-top: 30px; padding-top: 10px; }
        .project-divider { border-left: 4px solid #ccc; margin-left: 10px; padding-left: 10px; margin-bottom: 20px; }
    </style>
    <script>
        function applyFilters() {
            const selectedUpdateTypes = Array.from(document.querySelectorAll('input[name="updateType"]:checked')).map(cb => cb.value);
            const selectedChangeTypes = Array.from(document.querySelectorAll('input[name="changeType"]:checked')).map(cb => cb.value);

            document.querySelectorAll('.commit').forEach(commit => {
                let visibleProject = false;

                commit.querySelectorAll('.project').forEach(project => {
                    const projectChangeType = project.getAttribute('data-change-type');
                    const matchChange = selectedChangeTypes.includes(projectChangeType);

                    let visibleContainer = false;

                    if (matchChange) {
                        project.querySelectorAll('.container').forEach(container => {
                            const updateTypes = container.getAttribute('data-update-types').split(',');
                            const matchUpdate = selectedUpdateTypes.some(val => updateTypes.includes(val));

                            const visible = matchUpdate;
                            container.style.display = visible ? 'block' : 'none';
                            if (visible) visibleContainer = true;
                        });
                    }

                    project.style.display = (matchChange && visibleContainer) ? 'block' : 'none';
                    if (matchChange && visibleContainer) visibleProject = true;
                });

                commit.style.display = visibleProject ? 'block' : 'none';
            });
        }

        function toggleView() {
            const mode = document.getElementById('viewMode').value;
            document.getElementById('commitView').style.display = mode === 'commitView' ? 'block' : 'none';
            document.getElementById('sectionView').style.display = mode === 'sectionView' ? 'block' : 'none';
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
    <label for="viewMode">View mode:</label>
    <select id="viewMode" onchange="toggleView()">
        <option value="commitView">Chronologically</option>
        <option value="sectionView">Grouped by Section</option>
    </select>
</div>
<div>
    <fieldset>
        <legend>Filter by update_type:</legend>
''' + '\n'.join([f'<label><input type="checkbox" name="updateType" value="{t}" checked onchange="applyFilters()"> {t}</label><br>' for t in UPDATE_TYPES]) + '''
    </fieldset>
    <fieldset>
        <legend>Filter by change_type:</legend>
''' + '\n'.join([f'<label><input type="checkbox" name="changeType" value="{t}" checked onchange="applyFilters()"> {t}</label><br>' for t in CHANGE_TYPES]) + '''
    </fieldset>
</div>
<hr>
<div id="commitView">
'''

    for commit_entry in data:
        commit_html = f'<div class="commit"><strong>Commit:</strong> <code>{commit_entry["commit"]}</code>'
        project_htmls = []

        for project in commit_entry['projects']:
            containers_html = ''
            for container in project['containers']:
                update_types = ','.join(container['update_types'])
                containers_html += f'''<div class="container" data-update-types="{update_types}">
                    <strong>Container:</strong> <code>{container['container_name']}</code><br>
                    <strong>Old Image:</strong> <code>{container['old_image']}</code><br>
                    <strong>New Image:</strong> <code>{container['new_image']}</code><br>
                    <strong>Update Types:</strong> {', '.join(container['update_types'])}
                </div>'''

            if containers_html:
                project_html = f'<div class="project" data-change-type="{project["change_type"]}"><strong>Section:</strong> <code>{project["section"]}</code><br><strong>Project:</strong> <code>{project["project"]}</code><br><strong>Change Type:</strong> <span class="{project["change_type"]}">{project["change_type"]}</span>{containers_html}</div>'
                project_htmls.append(project_html)

        if project_htmls:
            commit_html += ''.join(project_htmls) + '</div>'
            html += commit_html

    html += '</div>'

    section_html = '<div id="sectionView" style="display:none">'
    section_map = defaultdict(list)

    for entry in data:
        for project in entry['projects']:
            section_map[project['section']].append({
                'commit': entry['commit'],
                'project': project
            })

    for section in sorted(section_map.keys()):
        section_html += f'<div class="section-divider"><h2>Section: <code>{section}</code></h2>'
        project_groups = defaultdict(list)
        for item in section_map[section]:
            project_groups[item['project']['project']].append(item)

        for project_name in sorted(project_groups.keys()):
            section_html += f'<div class="project-divider"><h3>Project: <code>{project_name}</code></h3>'
            for item in project_groups[project_name]:
                project = item['project']
                containers_html = ''
                for container in project['containers']:
                    update_types = ','.join(container['update_types'])
                    containers_html += f'''<div class="container" data-update-types="{update_types}">
                        <strong>Container:</strong> <code>{container['container_name']}</code><br>
                        <strong>Old Image:</strong> <code>{container['old_image']}</code><br>
                        <strong>New Image:</strong> <code>{container['new_image']}</code><br>
                        <strong>Update Types:</strong> {', '.join(container['update_types'])}
                    </div>'''
                if containers_html:
                    section_html += f'''<div class="project" data-change-type="{project['change_type']}">
                        <strong>Commit:</strong> <code>{item['commit']}</code><br>
                        <strong>Change Type:</strong> <span class="{project['change_type']}">{project['change_type']}</span>
                        {containers_html}
                    </div>'''
            section_html += '</div>'
        section_html += '</div>'

    section_html += '</div>'

    html += section_html
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
