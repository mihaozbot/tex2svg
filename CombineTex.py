#!/usr/bin/env python3
import sys
import glob
import os
import re

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# --- comment stripping that respects escaped \% ---
COMMENT_RE = re.compile(r'(^|[^\\])%.*$', re.MULTILINE)

# match \input{...} or \include{...}
INCLUDE_RE = re.compile(r'\\(?:input|include)\{([^}]+)\}')

def strip_comments(text: str) -> str:
    """Remove TeX comments (lines or tails after unescaped %)."""
    return re.sub(COMMENT_RE, r'\1', text)

def resolve_include(parent_file: str, include_name: str) -> str:
    """Resolve include path relative to the parent file dir, ensure .tex extension."""
    name = include_name.strip()
    if not name.endswith('.tex'):
        name += '.tex'
    if os.path.isabs(name):
        return os.path.normpath(name)
    base = os.path.dirname(os.path.abspath(parent_file))
    return os.path.normpath(os.path.join(base, name))

def find_included_files(tex_files):
    """Return set of absolute paths that are included by any file in tex_files."""
    included = set()
    for tf in tex_files:
        try:
            with open(tf, 'r', encoding='utf-8', errors='replace') as f:
                content = strip_comments(f.read())
            for inc in INCLUDE_RE.findall(content):
                path = resolve_include(tf, inc)
                included.add(path)
        except Exception:
            continue
    return included

def find_main_tex_file(tex_files):
    r"""
    Heuristic:
      1) Candidates with \begin{document} and not included by others -> pick largest.
      2) Else candidates with \documentclass and not included by others -> pick largest.
    """
    # Canonical absolute paths of all provided files
    tex_files_abs = [os.path.abspath(tf) for tf in tex_files]
    included_files = find_included_files(tex_files_abs)

    candidates_begin = []
    candidates_class = []

    for tf in tex_files_abs:
        try:
            with open(tf, 'r', encoding='utf-8', errors='replace') as f:
                content = strip_comments(f.read())
        except Exception:
            continue

        # Skip if this file is included by another
        if tf in included_files:
            continue

        if r'\begin{document}' in content:
            candidates_begin.append(tf)
        elif r'\documentclass' in content:
            candidates_class.append(tf)

    def pick_largest(paths):
        if not paths:
            return None
        return max(paths, key=lambda p: os.path.getsize(p) if os.path.exists(p) else -1)

    main = pick_largest(candidates_begin) or pick_largest(candidates_class)
    if main:
        if main in candidates_begin:
            print(f"Main LaTeX file (has \\begin{{document}}): {os.path.basename(main)}")
        else:
            print(f"Main LaTeX file (has \\documentclass): {os.path.basename(main)}")
    return main

def combine_tex_files(main_file, output_file):
    r"""
    Combine the main file and its \input/\include files into a single .tex,
    replacing the commands with the actual file contents (recursively).
    """
    visited = set()

    def expand(tex_path: str) -> str:
        apath = os.path.abspath(tex_path)
        if apath in visited:
            return ''  # prevent cycles
        visited.add(apath)

        try:
            with open(apath, 'r', encoding='utf-8', errors='replace') as f:
                original = f.read()
        except Exception as e:
            print(f"Error reading {tex_path}: {e}")
            return ''

        # Remove comments for reliable include detection, but inject original (with comments)
        # except at include sites; that keeps the combined file close to source.
        content_no_comments = strip_comments(original)

        # Replace all includes, resolving relative to this file
        def repl(m):
            inc_name = m.group(1)
            inc_path = resolve_include(apath, inc_name)
            if os.path.exists(inc_path):
                print(f"Including file: {os.path.relpath(inc_path, os.path.dirname(os.path.abspath(main_file)))}")
                return expand(inc_path)
            else:
                print(f"File not found for include: {inc_path}")
                return m.group(0)  # keep the original command if missing

        # Do the replacement on the original text so we preserve non-include content verbatim
        combined = re.sub(INCLUDE_RE, repl, original)
        return combined

    combined_tex_content = expand(main_file)

    os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as out_f:
        out_f.write(combined_tex_content)

    print(f"Combined LaTeX file created: {output_file}")

from pathlib import Path

if __name__ == "__main__":
    tex_file = sys.argv[1] if len(sys.argv) > 1 else None
    output_folder = sys.argv[2] if len(sys.argv) > 2 else None

    # Find all .tex files
    if tex_file is None:
        tex_files = glob.glob('*.tex')
        print(f"Found {len(tex_files)} .tex files in the current directory.")
    else:
        tex_files = [tex_file if tex_file.endswith('.tex') else tex_file + '.tex']

    if not tex_files:
        print("No .tex files found!")
        sys.exit(1)

    main_file = find_main_tex_file(tex_files)

    if main_file:
        # Build output path: combined_<basename>.tex
        base = Path(main_file).stem  # e.g., 'main_megatest'
        out_dir = Path(output_folder or ".")
        out_dir.mkdir(parents=True, exist_ok=True)
        output_file = out_dir / f"tmp_combined_{base}.tex"

        print(f"[tex2svg] Main: {Path(main_file).name}")
        print(f"[tex2svg] Output: {output_file}")

        combine_tex_files(main_file, str(output_file))
    else:
        print("No main LaTeX file found (no \\begin{document} or \\documentclass outside included files).")
        sys.exit(1)
