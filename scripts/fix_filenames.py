import os


def fix_encoding(path, dry_run=True, confirm_rename=True, confirm_overwrite=True):
    for root, dirs, files in os.walk(path, topdown=False):  # bottom-up walk
        for name in dirs + files:
            try:
                fixed_name = name.encode('latin1').decode('utf-8')
                if fixed_name != name:
                    old_path = os.path.join(root, name)
                    new_path = os.path.join(root, fixed_name)
                    print(f"\n{'DRY RUN:' if dry_run else 'Found:'}\n  From: {old_path}\n    To: {new_path}")

                    # Dry run — just preview
                    if dry_run:
                        continue

                    # Confirm the rename itself
                    if confirm_rename:
                        answer = input("Rename? [Y/n]: ").strip().lower()
                        if answer not in ("", "y", "yes"):
                            print("⏩ Skipped.")
                            continue

                    # Check for conflict
                    if os.path.exists(new_path):
                        if confirm_overwrite:
                            conflict_ans = input("⚠️  Target already exists. Overwrite/merge? [y/N]: ").strip().lower()
                            if conflict_ans not in ("y", "yes"):
                                print("⏩ Skipped due to existing path.")
                                continue
                        else:
                            print(f"⏩ Skipped: target already exists → {new_path}")
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

    if not target_path:
        print("❌ TARGET_PATH environment variable not set.")
        exit(1)

    fix_encoding(
        path=target_path,
        dry_run=dry_run,
        confirm_rename=confirm_rename,
        confirm_overwrite=confirm_overwrite
    )
