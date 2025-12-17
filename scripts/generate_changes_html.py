import json
import os
from collections import defaultdict

INPUT_JSON = os.getenv('INPUT_JSON', 'commits.json')
OUTPUT_HTML = os.getenv('OUTPUT_HTML', 'commits.html')

UPDATE_TYPES = ["repo", "user", "image", "tag", "sha"]
CHANGE_TYPES = ["created", "updated", "deleted"]

UPDATE_TYPE_CLASSES = {
    "repo": "ut-repo",
    "user": "ut-repo",
    "image": "ut-image",
    "tag": "ut-tag",
    "sha": "ut-sha",
}

COMMAND_TEMPLATE = (
    "cd /home/panzer1119/repositories/git/homelab-docker/compose/{section}/{project} && "
    "CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD) && "
    "git stash && git checkout {commit} && "
    "sudo bash ../../../scripts/snapshot_docker_compose_stack.sh -v -c {container} -u -D -C {commit}; "
    "git checkout $CURRENT_BRANCH && git stash pop && cd -"
)


def format_command(section, project, container, commit):
    return COMMAND_TEMPLATE.format(section=section, project=project, container=container, commit=commit)


def generate_html(data):
    # Collect distinct sections and projects for filters
    sections_set = set()
    projects_set = set()
    for commit_entry in data:
        for project in commit_entry['projects']:
            sections_set.add(project['section'])
            projects_set.add(project['project'])
    sections = sorted(sections_set)
    projects = sorted(projects_set)

    html = '<!DOCTYPE html>'
    html += '''
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Commit Container Updates</title>
    <style>
        /* ---------- Theme variables ---------- */
        :root {
            --bg: #ffffff;
            --fg: #000000;
            --muted: #666;
            --code-bg: #f4f4f4;
            --border: #ccc;
            --section: #444;
            --btn-bg: #f7f7f7;
            --btn-hover: #eee;
        }

        body.dark {
            --bg: #121212;
            --fg: #e6e6e6;
            --muted: #aaa;
            --code-bg: #1e1e1e;
            --border: #555;
            --section: #888;
            --btn-bg: #2a2a2a;
            --btn-hover: #3a3a3a;
        }

        /* ---------- Base styles ---------- */
        body {
            font-family: Arial, sans-serif;
            margin: 20px;
            background: var(--bg);
            color: var(--fg);
            transition: background 0.2s ease, color 0.2s ease;
        }

        code {
            background-color: var(--code-bg);
            padding: 2px 5px;
            border-radius: 4px;
            cursor: pointer;
        }

        .commit { margin-bottom: 20px; }

        .project {
            margin-left: 20px;
            margin-bottom: 10px;
            padding-left: 10px;
            border-left: 2px solid var(--border);
        }

        .container { margin-left: 40px; }

        .created { color: green; font-weight: bold; }
        .updated { color: dodgerblue; font-weight: bold; }
        .deleted { color: red; font-weight: bold; }

        .section-divider {
            border-top: 3px solid var(--section);
            margin-top: 20px;
            padding-top: 10px;
        }

        .project-divider {
            border-top: 2px dashed var(--border);
            margin-top: 15px;
            padding-top: 5px;
        }

        .ut-repo { color: red; font-weight: bold; }
        .ut-image { color: orange; font-weight: bold; }
        .ut-tag { color: green; font-weight: bold; }
        .ut-sha { color: dodgerblue; font-weight: bold; }

        .image-info {
            font-family: "Lucida Console", "Menlo", "Monaco", "Courier", monospace;
        }

        fieldset {
            display: inline-block;
            margin-right: 20px;
            vertical-align: top;
            border-color: var(--border);
        }

        legend { font-weight: bold; }

        .filters { margin: 10px 0 20px; }

        /* ---------- Buttons ---------- */
        .project-controls {
            margin: 6px 0 8px;
            display: inline-block;
        }

        .btn {
            border: 1px solid #888;
            background: var(--btn-bg);
            color: var(--fg);
            padding: 3px 8px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 12px;
        }

        .btn:hover { background: var(--btn-hover); }

        .section-header {
            display: flex;
            align-items: center;
            gap: 8px;
        }

        /* ---------- Dark mode toggle ---------- */
        .theme-toggle {
            position: fixed;
            top: 12px;
            right: 12px;
            font-size: 12px;
        }
    </style>
    <script>
        // Dark mode toggle
        document.addEventListener("DOMContentLoaded", () => {
            const body = document.body;
            const toggle = document.getElementById("themeToggle");

            // Load saved preference
            if (localStorage.getItem("theme") === "dark") {
                body.classList.add("dark");
            }

            toggle.addEventListener("click", () => {
                body.classList.toggle("dark");
                localStorage.setItem(
                    "theme",
                    body.classList.contains("dark") ? "dark" : "light"
                );
            });
        });
    
        // Safer checkbox lookup helpers
        function getCheckboxByNameValue(name, value) {
            // Try CSS.escape if available
            if (window.CSS && CSS.escape) {
                const sel = 'input[name="' + name + '"][value="' + CSS.escape(value) + '"]';
                const bySelector = document.querySelector(sel);
                if (bySelector) return bySelector;
            }
            // Fallback: linear scan
            return Array.from(document.querySelectorAll('input[name="' + name + '"]')).find(cb => cb.value === value) || null;
        }

        function getProjectCheckbox(projectName) {
            return getCheckboxByNameValue('projectFilter', projectName);
        }
        function getSectionCheckbox(sectionName) {
            return getCheckboxByNameValue('sectionFilter', sectionName);
        }

        function toggleProject(projectName) {
            const cb = getProjectCheckbox(projectName);
            if (!cb) return;
            cb.checked = !cb.checked;
            applyFilters();
            updateProjectButtons();
        }

        function toggleSection(sectionName) {
            const cb = getSectionCheckbox(sectionName);
            if (!cb) return;
            cb.checked = !cb.checked;
            applyFilters();
            updateSectionButtons();
        }

        function updateProjectButtons() {
            const buttons = document.querySelectorAll('.toggle-project-btn');
            buttons.forEach(btn => {
                const proj = btn.getAttribute('data-project');
                const cb = getProjectCheckbox(proj);
                const isChecked = cb ? cb.checked : true;
                btn.textContent = isChecked ? 'Hide project' : 'Show project';
                btn.setAttribute('aria-pressed', (!isChecked).toString());
                btn.title = (isChecked ? 'Disable' : 'Enable') + ' "' + proj + '" in the Projects filter';
            });
        }

        function updateSectionButtons() {
            const buttons = document.querySelectorAll('.toggle-section-btn');
            buttons.forEach(btn => {
                const sec = btn.getAttribute('data-section');
                const cb = getSectionCheckbox(sec);
                const isChecked = cb ? cb.checked : true;
                btn.textContent = isChecked ? 'Hide section' : 'Show section';
                btn.setAttribute('aria-pressed', (!isChecked).toString());
                btn.title = (isChecked ? 'Disable' : 'Enable') + ' "' + sec + '" in the Sections filter';
            });
        }

        function applyFilters() {
            const selectedUpdateTypes = Array.from(document.querySelectorAll('input[name="updateType"]:checked')).map(cb => cb.value);
            const selectedChangeTypes = Array.from(document.querySelectorAll('input[name="changeType"]:checked')).map(cb => cb.value);
            const selectedSections   = Array.from(document.querySelectorAll('input[name="sectionFilter"]:checked')).map(cb => cb.value);
            const selectedProjects   = Array.from(document.querySelectorAll('input[name="projectFilter"]:checked')).map(cb => cb.value);

            const allProjects = document.querySelectorAll('.project');
            const allContainers = document.querySelectorAll('.container');
            const allCommits = document.querySelectorAll('.commit');
            const allProjectDividers = document.querySelectorAll('.project-divider');
            const allSectionDividers = document.querySelectorAll('.section-divider');

            // Container-level filter: update types
            allContainers.forEach(container => {
                const updateTypes = container.getAttribute('data-update-types').split(',');
                const matchUpdate = selectedUpdateTypes.some(val => updateTypes.includes(val));
                container.style.display = matchUpdate ? 'block' : 'none';
            });

            // Project-level filter: change type + section + project + has visible container
            allProjects.forEach(project => {
                const changeType = project.getAttribute('data-change-type');
                const section = project.getAttribute('data-section');
                const projName = project.getAttribute('data-project');

                const matchChange = selectedChangeTypes.includes(changeType);
                const matchSection = selectedSections.includes(section);
                const matchProject = selectedProjects.includes(projName);

                const visibleContainers = Array.from(project.querySelectorAll('.container')).some(c => c.style.display !== 'none');
                project.style.display = (matchChange && matchSection && matchProject && visibleContainers) ? 'block' : 'none';
            });

            // Commit-level visibility: hide commits with no visible projects
            allCommits.forEach(commit => {
                const visibleProjects = Array.from(commit.querySelectorAll('.project')).some(p => p.style.display !== 'none');
                commit.style.display = visibleProjects ? 'block' : 'none';
            });

            // Project-divider visibility (only in section view)
            allProjectDividers.forEach(divider => {
                const visibleProjects = Array.from(divider.querySelectorAll('.project')).some(p => p.style.display !== 'none');
                divider.style.display = visibleProjects ? 'block' : 'none';
            });

            // Section-divider visibility (only in section view)
            allSectionDividers.forEach(divider => {
                const section = divider.getAttribute('data-section');
                const sectionSelected = selectedSections.includes(section);
                const visibleProjects = Array.from(divider.querySelectorAll('.project')).some(p => p.style.display !== 'none');
                divider.style.display = (sectionSelected && visibleProjects) ? 'block' : 'none';
            });

            // Keep toggle buttons in sync
            updateProjectButtons();
            updateSectionButtons();
        }

        function toggleView() {
            const mode = document.getElementById('viewMode').value;
            document.getElementById('commitView').style.display = mode === 'commitView' ? 'block' : 'none';
            document.getElementById('sectionView').style.display = mode === 'sectionView' ? 'block' : 'none';
            applyFilters();
        }

        function copyToClipboard(text) {
            navigator.clipboard.writeText(text);
        }

        document.addEventListener('DOMContentLoaded', () => {
            // Click-to-copy for code blocks
            document.querySelectorAll('code').forEach(code => {
                code.addEventListener('click', () => copyToClipboard(code.textContent));
            });

            // Toggle buttons (projects + sections)
            document.addEventListener('click', (e) => {
                const pbtn = e.target.closest('.toggle-project-btn');
                if (pbtn) {
                    toggleProject(pbtn.getAttribute('data-project'));
                    return;
                }
                const sbtn = e.target.closest('.toggle-section-btn');
                if (sbtn) {
                    toggleSection(sbtn.getAttribute('data-section'));
                    return;
                }
            });

            // Keep button labels synced when user changes filters manually
            document.querySelectorAll('input[name="projectFilter"]').forEach(cb => {
                cb.addEventListener('change', updateProjectButtons);
            });
            document.querySelectorAll('input[name="sectionFilter"]').forEach(cb => {
                cb.addEventListener('change', updateSectionButtons);
            });

            toggleView(); // also calls applyFilters -> syncs button labels
        });
    </script>
</head>
<body>
<h1>Commit Container Updates</h1>
<div>
    <div>
        <label for="viewMode">View mode:</label>
        <select id="viewMode" onchange="toggleView()">
            <option value="commitView">Chronologically</option>
            <option value="sectionView" selected>Grouped by Section</option>
        </select>
    </div>
    <div class="theme-toggle">
        <button id="themeToggle" class="btn theme-toggle" title="Toggle dark/light theme">Toggle Dark Mode</button>
    </div>
</div>
<div class="filters">
    <fieldset>
        <legend>Filter by update_type:</legend>
''' + '\n'.join([f'<label><input type="checkbox" name="updateType" value="{t}" ' + (
        '' if t == 'sha' else 'checked') + f' onchange="applyFilters()"> {t}</label><br>' for t in UPDATE_TYPES]) + '''
    </fieldset>
    <fieldset>
        <legend>Filter by change_type:</legend>
''' + '\n'.join([
                                                                                                                                        f'<label><input type="checkbox" name="changeType" value="{t}" checked onchange="applyFilters()"> {t}</label><br>'
                                                                                                                                        for
                                                                                                                                        t
                                                                                                                                        in
                                                                                                                                        CHANGE_TYPES]) + '''
    </fieldset>
    <fieldset>
        <legend>Filter by section:</legend>
''' + '\n'.join([
                                                                                                                                                                             f'<label><input type="checkbox" name="sectionFilter" value="{s}" checked onchange="applyFilters()"> {s}</label><br>'
                                                                                                                                                                             for
                                                                                                                                                                             s
                                                                                                                                                                             in
                                                                                                                                                                             sections]) + '''
    </fieldset>
    <fieldset>
        <legend>Filter by project:</legend>
''' + '\n'.join([
                                                                                                                                                                                                              f'<label><input type="checkbox" name="projectFilter" value="{p}" checked onchange="applyFilters()"> {p}</label><br>'
                                                                                                                                                                                                              for
                                                                                                                                                                                                              p
                                                                                                                                                                                                              in
                                                                                                                                                                                                              projects]) + '''
    </fieldset>
</div>
<hr>
<div id="commitView" style="display:none">
'''

    for commit_entry in data:
        commit_html = f'<div class="commit"><strong>Commit:</strong> <code>{commit_entry["commit"]}</code>'
        project_htmls = []

        for project in commit_entry['projects']:
            containers_html = ''
            for container in project['containers']:
                update_types = ','.join(container['update_types'])
                command = format_command(project['section'], project['project'], container['container_name'],
                                         commit_entry['commit'])
                styled_updates = ' '.join([
                    f'<span class="{UPDATE_TYPE_CLASSES.get(t, "")}">{t}</span>' for t in container['update_types']
                ])
                containers_html += f'''<div class="container" data-update-types="{update_types}">
                    <strong>Container:</strong> <code>{container['container_name']}</code><br>
                    <div class="image-info">
                        <strong>Old Image:</strong> <code>{container['old_image']}</code><br>
                        <strong>New Image:</strong> <code>{container['new_image']}</code><br>
                    </div>
                    <strong>Update Types:</strong> {styled_updates}<br>
                    <strong>Command:</strong> <code>{command}</code>
                </div>'''

            if containers_html:
                project_html = (
                    f'<div class="project" '
                    f'data-change-type="{project["change_type"]}" '
                    f'data-section="{project["section"]}" '
                    f'data-project="{project["project"]}">'
                    f'<strong>Section:</strong> <code>{project["section"]}</code> '
                    f'<span class="project-controls"><button class="btn toggle-section-btn" data-section="{project["section"]}" title="Disable this section in the filter">Hide section</button></span><br>'
                    f'<strong>Project:</strong> <code>{project["project"]}</code> '
                    f'<span class="project-controls"><button class="btn toggle-project-btn" data-project="{project["project"]}" title="Disable this project in the filter">Hide project</button></span><br>'
                    f'<strong>Change Type:</strong> <span class="{project["change_type"]}">{project["change_type"]}</span>'
                    f'{containers_html}'
                    f'</div>'
                )
                project_htmls.append(project_html)

        if project_htmls:
            commit_html += ''.join(project_htmls) + '</div>'
            html += commit_html

    html += '</div>'

    section_html = '<div id="sectionView">'
    section_map = defaultdict(list)

    for entry in data:
        for project in entry['projects']:
            section_map[project['section']].append({
                'commit': entry['commit'],
                'project': project
            })

    for section in sorted(section_map.keys()):
        section_html += f'<div class="section-divider" data-section="{section}"><h2 class="section-header">Section: <code>{section}</code> <button class="btn toggle-section-btn" data-section="{section}" title="Disable this section in the filter">Hide section</button></h2>'
        project_groups = defaultdict(list)
        for item in section_map[section]:
            project_groups[item['project']['project']].append(item)

        for project_name in sorted(project_groups.keys()):
            section_html += f'<div class="project-divider" data-project="{project_name}"><h3>Project: <code>{project_name}</code></h3>'
            for item in project_groups[project_name]:
                project = item['project']
                containers_html = ''
                for container in project['containers']:
                    update_types = ','.join(container['update_types'])
                    command = format_command(project['section'], project['project'], container['container_name'],
                                             item['commit'])
                    styled_updates = ' '.join([
                        f'<span class="{UPDATE_TYPE_CLASSES.get(t, "")}">{t}</span>' for t in container['update_types']
                    ])
                    containers_html += f'''<div class="container" data-update-types="{update_types}">
                        <strong>Container:</strong> <code>{container['container_name']}</code><br>
                        <div class="image-info">
                            <strong>Old Image:</strong> <code>{container['old_image']}</code><br>
                            <strong>New Image:</strong> <code>{container['new_image']}</code><br>
                        </div>
                        <strong>Update Types:</strong> {styled_updates}<br>
                        <strong>Command:</strong> <code>{command}</code>
                    </div>'''
                if containers_html:
                    section_html += f'''<div class="project"
                        data-change-type="{project['change_type']}"
                        data-section="{section}"
                        data-project="{project_name}">
                        <div class="project-controls" style="float:right; margin-top:-24px;">
                            <button class="btn toggle-project-btn" data-project="{project_name}" title="Disable this project in the filter">Hide project</button>
                        </div>
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
