import json
import os
from collections import defaultdict

INPUT_JSON = os.getenv('INPUT_JSON', 'commits.json')
OUTPUT_HTML = os.getenv('OUTPUT_HTML', 'commits.html')

UPDATE_TYPES = ["repo", "user", "image", "tag", "sha"]
CHANGE_TYPES = ["created", "updated", "deleted"]

def generate_html(data):
    def render_commit_view():
        html = '<div id="view-chronological">'
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
        return html

    def render_section_view():
        html = '<div id="view-section" style="display:none">'
        sections = defaultdict(list)
        for commit_entry in data:
            for project in commit_entry['projects']:
                sections[project['section']].append((commit_entry['commit'], project))

        for section, items in sections.items():
            html += f'<h2>Section: <code>{section}</code></h2>'
            for commit_hash, project in items:
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
                    html += f'<div class="project" data-change-type="{project["change_type"]}"><strong>Commit:</strong> <code>{commit_hash}</code><br><strong>Project:</strong> <code>{project["project"]}</code><br><strong>Change Type:</strong> <span class="{project["change_type"]}">{project["change_type"]}</span>{containers_html}</div>'
        html += '</div>'
        return html

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
        .created { color: green; font-weight: bold; }
        .updated { color: blue; font-weight: bold; }
        .deleted { color: red; font-weight: bold; }
    </style>
    <script>
        function applyFilters() {
            const selectedUpdateTypes = Array.from(document.querySelectorAll('input[name="updateType"]:checked')).map(cb => cb.value);
            const selectedChangeTypes = Array.from(document.querySelectorAll('input[name="changeType"]:checked')).map(cb => cb.value);
            document.querySelectorAll('.commit, .project').forEach(el => el.style.display = 'none');

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
                            container.style.display = matchUpdate ? 'block' : 'none';
                            if (matchUpdate) visibleContainer = true;
                        });
                    }
                    project.style.display = (matchChange && visibleContainer) ? 'block' : 'none';
                    if (matchChange && visibleContainer) visibleProject = true;
                });
                commit.style.display = visibleProject ? 'block' : 'none';
            });

            document.querySelectorAll('#view-section .project').forEach(project => {
                const projectChangeType = project.getAttribute('data-change-type');
                const matchChange = selectedChangeTypes.includes(projectChangeType);
                let visibleContainer = false;
                if (matchChange) {
                    project.querySelectorAll('.container').forEach(container => {
                        const updateTypes = container.getAttribute('data-update-types').split(',');
                        const matchUpdate = selectedUpdateTypes.some(val => updateTypes.includes(val));
                        container.style.display = matchUpdate ? 'block' : 'none';
                        if (matchUpdate) visibleContainer = true;
                    });
                }
                project.style.display = (matchChange && visibleContainer) ? 'block' : 'none';
            });
        }

        function switchView(view) {
            document.getElementById('view-chronological').style.display = (view === 'chronological') ? 'block' : 'none';
            document.getElementById('view-section').style.display = (view === 'section') ? 'block' : 'none';
            applyFilters();
        }

        function copyToClipboard(text) {
            navigator.clipboard.writeText(text);
        }

        document.addEventListener('DOMContentLoaded', () => {
            document.querySelectorAll('code').forEach(code => {
                code.addEventListener('click', () => copyToClipboard(code.textContent));
            });
            switchView('chronological');
        });
    </script>
</head>
<body>
<h1>Commit Container Updates</h1>
<div>
    <label for="viewSelector">View:</label>
    <select id="viewSelector" onchange="switchView(this.value)">
        <option value="chronological" selected>Chronologically</option>
        <option value="section">Group by Section</option>
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
'''
    html += render_commit_view()
    html += render_section_view()
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
