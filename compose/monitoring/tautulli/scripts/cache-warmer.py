#!/usr/bin/env python3

import argparse
import hashlib
import os
import time


def check_path(path):
    """Check whether the file exists and report the first missing path component."""
    path = os.path.abspath(path)

    if os.path.isfile(path):
        return True

    current = path
    while current != os.path.dirname(current):
        if not os.path.exists(current):
            print(f"Missing path: {current!r}", flush=True)
            return False
        current = os.path.dirname(current)

    print(f"File does not exist: {path!r}", flush=True)
    return False


def format_iec(value):
    """Format byte values using IEC units (KiB, MiB, GiB, ...)."""
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB", "ZiB", "YiB", "RiB", "QiB"]
    size = float(value)

    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return "N/A"


def process_file(path):
    if not check_path(path):
        return

    file_size = os.path.getsize(path)
    print(f"Starting: {path!r} (size: {format_iec(file_size)})", flush=True)
    start = time.monotonic()

    try:
        h = hashlib.sha256()
        total_read = 0

        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                total_read += len(chunk)
                h.update(chunk)

        elapsed = time.monotonic() - start
        avg_read_speed = (total_read / elapsed) if elapsed > 0 else 0.0
        print(
            f"Finished: {path!r} "
            f"{h.hexdigest()} "
            f"(size: {format_iec(file_size)}, avg: {format_iec(avg_read_speed)}/s, {elapsed:.2f}s)",
            flush=True,
        )

    except Exception as e:
        elapsed = time.monotonic() - start
        print(
            f"Error: {path!r} "
            f"(size: {format_iec(file_size)}, {elapsed:.2f}s): {e}",
            flush=True,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    args = parser.parse_args()

    process_file(args.file)


if __name__ == "__main__":
    main()
