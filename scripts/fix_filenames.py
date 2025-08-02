import os


def fix_encoding(path, dry_run=True, confirm=True):
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

                    # Confirm each rename if enabled
                    if confirm:
                        answer = input("Rename? [Y/n]: ").strip().lower()
                        if answer not in ("", "y", "yes"):
                            print("⏩ Skipped.")
                            continue

                    os.rename(old_path, new_path)
                    print("✅ Renamed.")

            except (UnicodeEncodeError, UnicodeDecodeError):
                continue


if __name__ == "__main__":
    target_path = os.environ.get("TARGET_PATH")
    dry_run_env = os.environ.get("DRY_RUN", "true").lower()
    confirm_env = os.environ.get("CONFIRM", "true").lower()

    dry_run = dry_run_env in ("1", "true", "yes")
    confirm = confirm_env in ("1", "true", "yes")

    if not target_path:
        print("❌ TARGET_PATH environment variable not set.")
        exit(1)

    fix_encoding(target_path, dry_run=dry_run, confirm=confirm)
