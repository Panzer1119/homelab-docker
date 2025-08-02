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


def move_dir_contents(src_dir, dst_dir):
    for item in os.listdir(src_dir):
        src = os.path.join(src_dir, item)
        dst = os.path.join(dst_dir, item)
        shutil.move(src, dst)
    os.rmdir(src_dir)


def fix_encoding(path, dry_run=True, confirm_rename=True, confirm_overwrite=True, list_command=None):
    for root, dirs, files in os.walk(path, topdown=False):  # bottom-up to handle nested paths
        for name in dirs + files:
            try:
                fixed_name = name.encode('latin1').decode('utf-8')
                if fixed_name == name:
                    continue  # nothing to fix

                old_path = os.path.join(root, name)
                new_path = os.path.join(root, fixed_name)

                print(f"\n{'DRY RUN:' if dry_run else 'Found:'}")
                print(f"  From: {safe_path(old_path)}")
                print(f"    To: {safe_path(new_path)}")

                if dry_run:
                    continue

                # Confirm rename
                if confirm_rename:
                    answer = input("Rename? [Y/n]: ").strip().lower()
                    if answer not in ("", "y", "yes"):
                        print("⏩ Skipped.")
                        continue

                if os.path.exists(new_path):
                    # List paths if both non-empty dirs
                    if os.path.isdir(old_path) and os.path.isdir(new_path):
                        old_contents = os.listdir(old_path)
                        new_contents = os.listdir(new_path)

                        if old_contents and new_contents and list_command:
                            run_list_command(list_command, old_path, new_path)

                        # Auto-merge into empty target
                        if not new_contents:
                            print("📂 Target dir is empty — auto-merging.")
                            move_dir_contents(old_path, new_path)
                            print("✅ Merged and removed old directory.")
                            continue

                        # Confirm overwrite/merge
                        if confirm_overwrite:
                            ow = input("⚠️  Target dir has contents. Merge? [y/N]: ").strip().lower()
                            if ow not in ("y", "yes"):
                                print("⏩ Skipped.")
                                continue
                        else:
                            print("⏩ Skipped: overwrite not allowed.")
                            continue

                        print("🔁 Merging directory contents...")
                        move_dir_contents(old_path, new_path)
                        print("✅ Merged and removed old directory.")
                        continue

                    else:
                        # Handle file conflicts
                        if confirm_overwrite:
                            ow = input("⚠️  Target file exists. Overwrite? [y/N]: ").strip().lower()
                            if ow not in ("y", "yes"):
                                print("⏩ Skipped.")
                                continue
                        else:
                            print("⏩ Skipped: file exists.")
                            continue

                # Perform rename
                os.rename(old_path, new_path)
                print("✅ Renamed.")

            except (UnicodeEncodeError, UnicodeDecodeError):
                print(f"⚠️ Skipping invalid name: {safe_path(name)}")
                continue
            except Exception as e:
                print(f"❌ Error processing {safe_path(name)}: {e}")
                continue


def parse_env_bool(var_name, default=True):
    val = os.environ.get(var_name, str(default)).strip().lower()
    return val in ("1", "true", "yes")


if __name__ == "__main__":
    target_path = os.environ.get("TARGET_PATH")
    dry_run = parse_env_bool("DRY_RUN", True)
    confirm_rename = parse_env_bool("CONFIRM_RENAME", True)
    confirm_overwrite = parse_env_bool("CONFIRM_OVERWRITE", True)
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
