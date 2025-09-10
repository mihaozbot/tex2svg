#!/usr/bin/env python3
"""
pdf2svg_recursive_debug.py
Recursively convert PDFs to SVGs (same name, same folder) if missing,
starting ONLY from the directory where this script resides.

- Prints detailed progress messages.
- Exports page 1 only (same basename: foo.pdf -> foo.svg).
"""

import os
import sys
import shutil
import subprocess
import time

# ---- Settings (tweak if needed) -----------------------------------
TIMEOUT_SEC = 120
USE_TEXT_TO_PATH = False   # True => convert text to paths
CROP = "page"              # "page" or "drawing" (tight crop)
# -------------------------------------------------------------------

def script_root() -> str:
    """Return the absolute directory where this script lives."""
    return os.path.dirname(os.path.abspath(__file__))

def find_inkscape() -> str | None:
    """Find inkscape CLI (prefer inkscape.com on Windows)."""
    env_path = os.environ.get("INKSCAPE")
    if env_path and shutil.which(env_path):
        return env_path
    for name in ("inkscape.com", "inkscape"):
        p = shutil.which(name)
        if p:
            return p
    candidates = [
        r"C:\Program Files\Inkscape\bin\inkscape.com",
        r"C:\Program Files\Inkscape\bin\inkscape.exe",
        r"C:\Program Files\Inkscape\inkscape.com",
        r"C:\Program Files\Inkscape\inkscape.exe",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None

def export_one_page(inkscape: str, pdf_path: str, svg_out: str, page: int = 1) -> None:
    """Run Inkscape to export a single page to SVG."""
    args = [
        inkscape,
        "--pdf-poppler",
        f"--pdf-page={page}",
        "--export-type=svg",
        f"--export-filename={svg_out}",
        "--export-overwrite",
    ]
    if CROP == "drawing":
        args.append("--export-area-drawing")
    if USE_TEXT_TO_PATH:
        args.append("--export-text-to-path")

    print(f"      ↳ Inkscape cmd: {' '.join(args)}")
    res = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         timeout=TIMEOUT_SEC, check=False)
    if res.returncode != 0:
        print("      ! Inkscape ERROR")
        if res.stdout:
            print("        STDOUT:\n" + res.stdout.decode(errors='ignore'))
        if res.stderr:
            print("        STDERR:\n" + res.stderr.decode(errors='ignore'))
        raise RuntimeError(f"Inkscape returned code {res.returncode}")
    if not os.path.exists(svg_out):
        raise RuntimeError("No SVG produced (check input and page number).")

def process_pdf(inkscape: str, pdf_path: str, root: str) -> tuple[int, int, int]:
    """
    Convert foo.pdf -> foo.svg if missing.
    Returns (created, skipped, failed) counts for this file (0/1 each).
    """
    rel = os.path.relpath(pdf_path, root)
    base, _ = os.path.splitext(pdf_path)
    svg_out = base + ".svg"

    print(f"    • Found PDF: {rel}")
    if os.path.exists(svg_out):
        print(f"      - Skip (exists): {os.path.relpath(svg_out, root)}")
        return (0, 1, 0)

    print(f"      - Creating: {os.path.relpath(svg_out, root)}")
    try:
        t0 = time.time()
        export_one_page(inkscape, pdf_path, svg_out, page=1)
        dt = time.time() - t0
        print(f"      ✓ Done in {dt:.2f}s")
        return (1, 0, 0)
    except subprocess.TimeoutExpired:
        print(f"      ✗ FAIL (timeout {TIMEOUT_SEC}s)")
        return (0, 0, 1)
    except Exception as e:
        print(f"      ✗ FAIL: {e}")
        return (0, 0, 1)

def main():
    root = script_root()
    print("=" * 70)
    print("PDF → SVG (recursive)".center(70))
    print("=" * 70)
    print(f"Script directory (root): {root}")
    print("Note: Only scanning this folder and its subfolders.\n")

    inkscape = find_inkscape()
    if not inkscape:
        print("ERROR: Inkscape CLI not found. Install Inkscape 1.x or set INKSCAPE env var.")
        sys.exit(1)
    print(f"Inkscape found: {inkscape}\n")

    total_pdfs = 0
    total_created = 0
    total_skipped = 0
    total_failed = 0
    dir_count = 0

    for dirpath, dirnames, filenames in os.walk(root):
        dir_count += 1
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            rel_dir_disp = "(root)"
        else:
            rel_dir_disp = rel_dir
        print(f"[{dir_count}] Scanning folder: {rel_dir_disp}")

        # sort for stable output
        filenames_sorted = sorted(filenames, key=str.lower)

        # list PDFs in this folder
        pdfs_here = [fn for fn in filenames_sorted if fn.lower().endswith(".pdf")]
        if not pdfs_here:
            print("    (no PDFs)")
            continue

        for fn in pdfs_here:
            pdf_path = os.path.join(dirpath, fn)
            c, s, f = process_pdf(inkscape, pdf_path, root)
            total_pdfs += 1
            total_created += c
            total_skipped += s
            total_failed += f

        print("")  # spacer after each folder

    print("=" * 70)
    print("Summary".center(70))
    print("=" * 70)
    print(f"Folders scanned : {dir_count}")
    print(f"PDFs found      : {total_pdfs}")
    print(f"SVGs created    : {total_created}")
    print(f"Already existed : {total_skipped}")
    print(f"Failures        : {total_failed}")
    print("=" * 70)

if __name__ == "__main__":
    main()
