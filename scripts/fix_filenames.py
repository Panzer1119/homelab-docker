import os
import shutil
import subprocess
import hashlib


def safe_path(p):
    try:
        return repr(p)
    except Exception:
        return str(p).encode("utf-8", "backslashreplace").decode("utf-8")


def parse_env_bool(var_name, default=True):
    val = os.environ.get(var_name, str(default)).strip().lower()
    return val in ("1", "true", "yes")


def run_list_command(cmd_template, old_path, new_path):
    if not cmd_template.strip():
        return
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


def hash_file(filepath, block_size=65536):
    hasher = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(block_size), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def dirs_are_identical(dir1, dir2):
    files1 = sorted(os.listdir(dir1))
    files2 = sorted(os.listdir(dir2))

    if files1 != files2:
        return False

    for name in files1:
        path1 = os.path.join(dir1, name)
        path2 = os.path.join(dir2, name)

        if os.path.isdir(path1) or os.path.isdir(path2):
            return False  # Nested dir comparison not implemented

        try:
            if hash_file(path1) != hash_file(path2):
                return False
        except Exception as e:
            print(f"⚠️ Failed to hash {safe_path(path1)} or {safe_path(path2)}: {e}")
            return False

    return True


def fix_encoding(path, dry_run=True, confirm_rename=True, confirm_overwrite=True, list_command=None):
    for root, dirs, files in os.walk(path, topdown=False):
        for name in dirs + files:
            try:
                fixed_name = name.encode('latin1').decode('utf-8')
                if fixed_name == name:
                    continue

                old_path = os.path.join(root, name)
                new_path = os.path.join(root, fixed_name)

                print(f"\n{'DRY RUN:' if dry_run else 'Found:'}")
                print(f"  From: {safe_path(old_path)}")
                print(f"    To: {safe_path(new_path)}")

                if dry_run:
                    continue

                # 🧹 If old is empty directory, delete it
                if os.path.isdir(old_path) and not os.listdir(old_path):
                    print(f"📭 Old directory is empty — removing: {safe_path(old_path)}")
                    os.rmdir(old_path)
                    continue

                # 🚀 If file and new path doesn't exist, rename without asking
                if os.path.isfile(old_path) and not os.path.exists(new_path):
                    os.rename(old_path, new_path)
                    print("✅ Renamed (file, no conflict).")
                    continue

                # 📁 Directory → Directory handling
                if os.path.isdir(old_path) and os.path.isdir(new_path):
                    old_contents = os.listdir(old_path)
                    new_contents = os.listdir(new_path)

                    if not new_contents:
                        print("📂 Target dir is empty — auto-merging.")
                        move_dir_contents(old_path, new_path)
                        print("✅ Merged and removed old directory.")
                        continue

                    if old_contents and new_contents and list_command:
                        run_list_command(list_command, old_path, new_path)

                    if dirs_are_identical(old_path, new_path):
                        print("📎 Directories have same files with matching content — skipping move.")
                        os.rmdir(old_path)
                        print("🗑️  Deleted old directory.")
                        continue

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

                # 🧪 File → File conflict
                if os.path.isfile(old_path) and os.path.isfile(new_path):
                    try:
                        if hash_file(old_path) == hash_file(new_path):
                            print("🟰 Files have identical content — skipping.")
                            os.remove(old_path)
                            print("🗑️  Deleted duplicate old file.")
                            continue
                    except Exception as e:
                        print(f"⚠️ Hashing failed: {e}")

                    if confirm_overwrite:
                        ow = input("⚠️  Target file exists. Overwrite? [y/N]: ").strip().lower()
                        if ow not in ("y", "yes"):
                            print("⏩ Skipped.")
                            continue
                    else:
                        print("⏩ Skipped: file exists.")
                        continue

                    try:
                        os.remove(new_path)
                        print("🗑️  Deleted existing file before renaming.")
                    except Exception as e:
                        print(f"❌ Failed to delete existing file: {safe_path(new_path)} → {e}")
                        continue

                    os.rename(old_path, new_path)
                    print("✅ Renamed.")
                    continue

                # ❓ For everything else, confirm rename
                if confirm_rename:
                    answer = input("Rename? [Y/n]: ").strip().lower()
                    if answer not in ("", "y", "yes"):
                        print("⏩ Skipped.")
                        continue

                os.rename(old_path, new_path)
                print("✅ Renamed.")

            except (UnicodeEncodeError, UnicodeDecodeError):
                # print(f"⚠️ Skipping invalid name: {safe_path(name)}")
                continue
            except Exception as e:
                print(f"❌ Error processing {safe_path(name)}: {e}")
                continue


if __name__ == "__main__":
    target_path = os.environ.get("TARGET_PATH")
    if not target_path:
        print("❌ TARGET_PATH environment variable not set.")
        exit(1)

    dry_run = parse_env_bool("DRY_RUN", True)
    confirm_rename = parse_env_bool("CONFIRM_RENAME", True)
    confirm_overwrite = parse_env_bool("CONFIRM_OVERWRITE", True)
    list_command = os.environ.get("LIST_COMMAND", "find")

    fix_encoding(
        path=target_path,
        dry_run=dry_run,
        confirm_rename=confirm_rename,
        confirm_overwrite=confirm_overwrite,
        list_command=list_command
    )
