import os


def fix_encoding(path, dry_run=True):
    for root, dirs, files in os.walk(path, topdown=False):
        for name in dirs + files:
            try:
                fixed_name = name.encode('latin1').decode('utf-8')
                if fixed_name != name:
                    old_path = os.path.join(root, name)
                    new_path = os.path.join(root, fixed_name)
                    print(f"{'DRY RUN:' if dry_run else 'Renaming:'}\n  From: {old_path}\n    To: {new_path}")
                    if not dry_run:
                        os.rename(old_path, new_path)
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue


if __name__ == "__main__":
    target_path = os.environ.get("TARGET_PATH")
    dry_run_env = os.environ.get("DRY_RUN", "true").lower()
    dry_run = dry_run_env in ("1", "true", "yes")

    if not target_path:
        print("❌ TARGET_PATH environment variable not set.")
        exit(1)

    fix_encoding(target_path, dry_run=dry_run)
