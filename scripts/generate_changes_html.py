import json
import sys
import argparse
from collections import defaultdict
from string import Template

UPDATE_TYPES = ["registry", "namespace", "repository", "tag", "digest"]
CHANGE_TYPES = ["created", "updated", "deleted"]

UPDATE_TYPE_CLASSES = {
    "separator": "ut-separator",
    "none": "ut-none",
    "registry": "ut-registry",
    "namespace": "ut-namespace",
    "repository": "ut-repository",
    "tag": "ut-tag",
    "digest": "ut-digest",
}

COMMAND_TEMPLATE = Template(
    "sudo python3 \"${repo}/scripts/snapshot_docker_compose_stack.py\" "
    "-v -D -u "
    "-d \"${repo}/compose/${section}/${project}\" "
    "-c ${container} "
    "-C ${commit}"
)


def format_command(section, project, container, commit, repo):
    return COMMAND_TEMPLATE.substitute(repo=repo, section=section, project=project, container=container, commit=commit)


def generate_html(data, repo):
    # Collect distinct sections and projects for filters
    sections_set = set()
    projects_set = set()
    for commit_entry in data:
        #commit_entry['timestamp_formatted'] = commit_entry['timestamp'].replace('T', ' ').replace('Z', ' UTC')
        #commit_entry['timestamp_formatted'] = commit_entry['timestamp'].replace('T', ' ')
        commit_entry['timestamp_formatted'] = commit_entry['timestamp']
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

        .ut-none { }
        .ut-separator { color: gray; }
        .ut-registry { color: red; font-weight: bold; }
        .ut-namespace { color: red; font-weight: bold; }
        .ut-repository { color: orange; font-weight: bold; }
        .ut-tag { color: green; font-weight: bold; }
        .ut-digest { color: dodgerblue; font-weight: bold; }

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
        '' if t == 'digest' else 'checked') + f' onchange="applyFilters()"> {t}</label><br>' for t in UPDATE_TYPES]) + '''
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
        commit_html += f'<br><strong>Timestamp:</strong> <code><time datetime="{commit_entry['timestamp']}">{commit_entry['timestamp_formatted']}</time></code>'
        project_htmls = []

        for project in commit_entry['projects']:
            containers_html = ''
            for container in project['containers']:
                update_types = ','.join(container['update_types'])
                command = format_command(project['section'], project['project'], container['container_name'],
                                         commit_entry['commit'], repo)
                styled_updates = ' '.join([
                    f'<span class="{UPDATE_TYPE_CLASSES.get(t, "")}">{t}</span>' for t in container['update_types']
                ])
                old_image_html, new_image_html = image_diff_to_html(container['image']['old'],
                                                                    container['image']['new'])
                containers_html += f'''<div class="container" data-update-types="{update_types}">
                    <strong>Container:</strong> <code>{container['container_name']}</code><br>
                    <div class="image-info">
                        <strong>Old Image:</strong> <code>{old_image_html}</code><br>
                        <strong>New Image:</strong> <code>{new_image_html}</code><br>
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
                'timestamp': entry['timestamp'],
                'timestamp_formatted': entry['timestamp_formatted'],
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
                                             item['commit'], repo)
                    styled_updates = ' '.join([
                        f'<span class="{UPDATE_TYPE_CLASSES.get(t, "")}">{t}</span>' for t in container['update_types']
                    ])
                    old_image_html, new_image_html = image_diff_to_html(container['image']['old'],
                                                                        container['image']['new'])
                    containers_html += f'''<div class="container" data-update-types="{update_types}">
                        <strong>Container:</strong> <code>{container['container_name']}</code><br>
                        <div class="image-info">
                            <strong>Old Image:</strong> <code>{old_image_html}</code><br>
                            <strong>New Image:</strong> <code>{new_image_html}</code><br>
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
                        <strong>Timestamp:</strong> <code><time datetime="{item['timestamp']}">{item['timestamp_formatted']}</time></code><br>
                        <strong>Change Type:</strong> <span class="{project['change_type']}">{project['change_type']}</span>
                        {containers_html}
                    </div>'''
            section_html += '</div>'
        section_html += '</div>'

    section_html += '</div>'
    html += section_html
    html += '</body>\n</html>'
    return html


def image_diff_to_html(old_image_json: dict, new_image_json: dict, only_exact: bool = True) -> tuple[str, str]:
    # Old image
    old_registry: str = old_image_json['registry']
    old_namespace: str = old_image_json['namespace']
    old_repository: str = old_image_json['repository']
    old_tag: str = old_image_json['tag']
    old_digest: str = old_image_json['digest']
    # New image
    new_registry: str = new_image_json['registry']
    new_namespace: str = new_image_json['namespace']
    new_repository: str = new_image_json['repository']
    new_tag: str = new_image_json['tag']
    new_digest: str = new_image_json['digest']
    if only_exact:
        # Color only the characters that changed, using difflib.SequenceMatcher
        from difflib import SequenceMatcher
        def color_diff(update_type: str, old: str, new: str) -> tuple[str, str]:
            matcher = SequenceMatcher(None, old, new)
            old_colored = ''
            new_colored = ''
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                old_part = old[i1:i2]
                new_part = new[j1:j2]
                if tag == 'equal' or old_part == new_part:
                    old_colored += old_part
                    new_colored += new_part
                elif tag == 'delete':
                    old_colored += f'<span class="{UPDATE_TYPE_CLASSES[update_type]}">{old_part}</span>'
                elif tag == 'insert':
                    new_colored += f'<span class="{UPDATE_TYPE_CLASSES[update_type]}">{new_part}</span>'
                else:
                    old_colored += f'<span class="{UPDATE_TYPE_CLASSES[update_type]}">{old_part}</span>'
                    new_colored += f'<span class="{UPDATE_TYPE_CLASSES[update_type]}">{new_part}</span>'
            return old_colored, new_colored

        old_registry_html, new_registry_html = color_diff("registry", old_registry, new_registry)
        old_namespace_html, new_namespace_html = color_diff("namespace", old_namespace, new_namespace)
        old_repository_html, new_repository_html = color_diff("repository", old_repository, new_repository)
        old_tag_html, new_tag_html = color_diff("tag", old_tag, new_tag)
        # old_digest_html, new_digest_html = color_diff("digest", old_digest, new_digest)
        # old_digest_html, new_digest_html = color_diff("none", old_digest, new_digest)
        if old_digest == new_digest:
            old_digest_html = old_digest
            new_digest_html = new_digest
        else:
            old_digest_html = f'<span class="{UPDATE_TYPE_CLASSES["digest"]}">{old_digest}</span>'
            new_digest_html = f'<span class="{UPDATE_TYPE_CLASSES["digest"]}">{new_digest}</span>'
        old_image_html = f'{old_registry_html}<span class="ut-separator">/</span>{old_namespace_html}<span class="ut-separator">/</span>{old_repository_html}<span class="ut-separator">:</span>{old_tag_html}<span class="ut-separator">@</span>{old_digest_html}'
        new_image_html = f'{new_registry_html}<span class="ut-separator">/</span>{new_namespace_html}<span class="ut-separator">/</span>{new_repository_html}<span class="ut-separator">:</span>{new_tag_html}<span class="ut-separator">@</span>{new_digest_html}'
    else:
        # Diffs
        is_registry_updated = old_registry != new_registry
        is_namespace_updated = old_namespace != new_namespace
        is_repository_updated = old_repository != new_repository
        is_tag_updated = old_tag != new_tag
        is_digest_updated = old_digest != new_digest
        # Color changed parts in old image red and in new image green
        old_image_html = f'<span class="{is_registry_updated and UPDATE_TYPE_CLASSES["registry"]}">{old_registry}</span><span class="ut-separator">/</span>' \
                         f'<span class="{is_namespace_updated and UPDATE_TYPE_CLASSES["namespace"]}">{old_namespace}</span><span class="ut-separator">/</span>' \
                         f'<span class="{is_repository_updated and UPDATE_TYPE_CLASSES["repository"]}">{old_repository}</span><span class="ut-separator">:</span>' \
                         f'<span class="{is_tag_updated and UPDATE_TYPE_CLASSES["tag"]}">{old_tag}</span><span class="ut-separator">@</span>' \
                         f'<span class="{is_digest_updated and UPDATE_TYPE_CLASSES["digest"]}">{old_digest}</span>'
        new_image_html = f'<span class="{is_registry_updated and UPDATE_TYPE_CLASSES["registry"]}">{new_registry}</span><span class="ut-separator">/</span>' \
                         f'<span class="{is_namespace_updated and UPDATE_TYPE_CLASSES["namespace"]}">{new_namespace}</span><span class="ut-separator">/</span>' \
                         f'<span class="{is_repository_updated and UPDATE_TYPE_CLASSES["repository"]}">{new_repository}</span><span class="ut-separator">:</span>' \
                         f'<span class="{is_tag_updated and UPDATE_TYPE_CLASSES["tag"]}">{new_tag}</span><span class="ut-separator">@</span>' \
                         f'<span class="{is_digest_updated and UPDATE_TYPE_CLASSES["digest"]}">{new_digest}</span>'
    return old_image_html, new_image_html


def main():
    parser = argparse.ArgumentParser(
        description='Generate an HTML report from commit container updates JSON.'
    )
    parser.add_argument(
        'input_json',
        nargs='?',
        default='commits.json',
        help='Path to input JSON file (default: commits.json). Use "-" to read from stdin.',
        metavar='INPUT',
    )
    parser.add_argument(
        '-o', '--output',
        default='commits.html',
        help='Path to output HTML file (default: commits.html)',
        metavar='OUTPUT',
    )
    parser.add_argument(
        '--repo',
        default='/home/panzer1119/repositories/git/homelab-docker',
        help='Repository root path for command generation (default: /home/panzer1119/repositories/git/homelab-docker)',
        metavar='REPO',
    )

    args = parser.parse_args()

    # Read JSON input
    try:
        if args.input_json == '-':
            data = json.load(sys.stdin)
        else:
            with open(args.input_json, 'r') as f:
                data = json.load(f)
    except FileNotFoundError:
        print(f"Error: Input file '{args.input_json}' not found", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in '{args.input_json}': {e}", file=sys.stderr)
        sys.exit(1)

    html_content = generate_html(data, args.repo)

    try:
        with open(args.output, 'w') as f:
            f.write(html_content)
        print(f"HTML output written to {args.output}")
    except IOError as e:
        print(f"Error: Could not write to '{args.output}': {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
