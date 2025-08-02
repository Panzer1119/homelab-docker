import os
import shutil
import subprocess


def safe_path(p):
    try:
        return repr(p)
    except Exception:
        return str(p).encode("utf-8", "backslashreplace").decode("utf-8")


def run_list_command(cmd_template, old_path, new_path):
    if not cmd_template.strip():
        return  # No command provided — skip listing
    try:
        full_cmd = f"{cmd_template} '{old_path}' '{new_path}'"
        print(f"📁 Listing both paths: {full_cmd}")
        subprocess.run(full_cmd, shell=True)
    except Exception as e:
        print(f"⚠️ Failed to run list command: {e}")


def fix_encoding(path, dry_run=True, confirm_rename=True, confirm_overwrite=True, list_command=None):
    for root, dirs, files in os.walk(path, topdown=False):  # bottom-up walk
        for name in dirs + files:
            try:
                fixed_name = name.encode('latin1').decode('utf-8')
                if fixed_name != name:
                    old_path = os.path.join(root, name)
                    new_path = os.path.join(root, fixed_name)
                    print(f"\n{'DRY RUN:' if dry_run else 'Found:'}")
                    print(f"  From: {safe_path(old_path)}")
                    print(f"    To: {safe_path(new_path)}")

                    # Dry run — just preview
                    if dry_run:
                        continue

                    # Confirm rename (if enabled)
                    if confirm_rename:
                        answer = input("Rename? [Y/n]: ").strip().lower()
                        if answer not in ("", "y", "yes"):
                            print("⏩ Skipped.")
                            continue

                    # Check for conflict
                    if os.path.exists(new_path):
                        if os.path.isdir(old_path) and os.path.isdir(new_path):
                            old_contents = os.listdir(old_path)
                            new_contents = os.listdir(new_path)

                            # 🧠 Auto-merge if new dir is empty
                            if not new_contents:
                                print("📂 Target dir exists but is empty — auto-merging contents.")
                                for item in old_contents:
                                    shutil.move(os.path.join(old_path, item), os.path.join(new_path, item))
                                os.rmdir(old_path)
                                print("✅ Merged and removed old directory.")
                                continue

                            # 🧪 Run list command if both dirs contain files
                            if old_contents and new_contents and list_command:
                                run_list_command(list_command, old_path, new_path)

                        if confirm_overwrite:
                            conflict_ans = input("⚠️  Target already exists. Overwrite/merge? [y/N]: ").strip().lower()
                            if conflict_ans not in ("y", "yes"):
                                print("⏩ Skipped due to existing path.")
                                continue
                        else:
                            print(f"⏩ Skipped: target already exists → {safe_path(new_path)}")
                            continue

                    os.rename(old_path, new_path)
                    print("✅ Renamed.")

            except (UnicodeEncodeError, UnicodeDecodeError):
                continue


def parse_env_bool(var_name, default=True):
    val = os.environ.get(var_name, str(default)).strip().lower()
    return val in ("1", "true", "yes")


if __name__ == "__main__":
    target_path = os.environ.get("TARGET_PATH")
    dry_run = parse_env_bool("DRY_RUN", default=True)
    confirm_rename = parse_env_bool("CONFIRM_RENAME", default=True)
    confirm_overwrite = parse_env_bool("CONFIRM_OVERWRITE", default=True)
    list_command = os.environ.get("LIST_COMMAND", "find")

    if not target_path:
        print("❌ TARGET_PATH environment variable not set.")
        exit(1)

    fix_encoding(
        path=target_path,
        dry_run=dry_run,
        confirm_rename=confirm_rename,
        confirm_overwrite=confirm_overwrite,
        list_command=list_command
    )
