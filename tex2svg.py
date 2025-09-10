import re
import os
import glob
import subprocess
import threading
import sys
import shutil
import textwrap
    
os.chdir(os.path.dirname(os.path.abspath(__file__)))
COMMENT_RE = re.compile(r'(^|[^\\])%.*$', re.MULTILINE)   # TeX comment lines
MACRO_RE = re.compile(r'\\([A-Za-z@]+)')   # skip ctrl-symbols like \&, \%, …
INCLUDE_RE = re.compile(r'\\(?:input|include)\{([^}]+)\}')

def strip_comments(text: str) -> str:
    """Remove TeX comments (unescaped %)."""
    return re.sub(COMMENT_RE, r'\1', text)

def resolve_include(parent_file: str, include_name: str) -> str:
    """Resolve include path relative to the parent file; ensure .tex extension."""
    name = include_name.strip()
    if not name.endswith('.tex'):
        name += '.tex'
    if os.path.isabs(name):
        return os.path.normpath(name)
    base = os.path.dirname(os.path.abspath(parent_file))
    return os.path.normpath(os.path.join(base, name))

def expand_inputs(original_text: str, parent_file: str, visited=None) -> str:
    """Recursively inline \input/\include contents."""
    if visited is None:
        visited = set()

    def repl(m):
        inc_name = m.group(1)
        inc_path = resolve_include(parent_file, inc_name)
        if inc_path in visited:
            return ''  # break cycles
        if not os.path.exists(inc_path):
            print(f"[combine] Missing include: {inc_path}")
            return m.group(0)  # keep original command
        visited.add(inc_path)
        try:
            with open(inc_path, 'r', encoding='utf-8', errors='replace') as f:
                sub = f.read()
        except Exception as e:
            print(f"[combine] Error reading {inc_path}: {e}")
            return m.group(0)

        # Recurse relative to the included file
        return expand_inputs(sub, inc_path, visited)

    # Do replacement on the original text so we preserve non-include content verbatim
    return re.sub(INCLUDE_RE, repl, original_text)

def find_included_files(tex_files):
    """Return set of absolute paths that are included by any file in tex_files."""
    included = set()
    for tf in tex_files:
        try:
            with open(tf, 'r', encoding='utf-8', errors='replace') as f:
                content_nc = strip_comments(f.read())
            for inc in INCLUDE_RE.findall(content_nc):
                included.add(resolve_include(tf, inc))
        except Exception:
            continue
    return included

def looks_full_document(tex_text: str) -> bool:
    return (r'\documentclass' in tex_text) or (r'\begin{document}' in tex_text)

def macros_in_equation(body):
    """Return a set of macro names (no backslash) found in the eq body."""
    return {m.group(1) for m in MACRO_RE.finditer(body)}

DEFINE_PATTERNS = [
    r'\\newcommand\*?\s*{\s*\\%s\b',
    r'\\renewcommand\*?\s*{\s*\\%s\b',
    r'\\providecommand\*?\s*{\s*\\%s\b',
    r'\\DeclareMathOperator\*?\s*{\s*\\%s\b',
]

def collect_definitions(needed, preamble):
    """Return only the definition lines that define one of *needed*."""
    lines = preamble.splitlines()
    keeps = []
    for line in lines:
        for macro in needed:
            for pat in DEFINE_PATTERNS:
                if re.search(pat % re.escape(macro), line):
                    keeps.append(line)
                    break
    return '\n'.join(keeps)

# ─────────────────────────────────────────────
#  FIND EQUATIONS (returns (env, body) tuples)
# ─────────────────────────────────────────────

def find_equations(tex_file):
    try:
        with open(tex_file, "r", encoding="utf-8", errors="ignore") as f:
            tex_content = f.read()
    except (UnicodeDecodeError, IOError) as e:
        print(f"Error reading {tex_file}: {e}")
        return []

    pattern = re.compile(
        r"""
        \\begin\{(?P<env>equation\*?|align\*?|multline\*?|gather\*?|displaymath)\}
        (?P<body>[\s\S]*?)
        \\end\{\1\}
        |
        \\\[(?P<bracket_body>[\s\S]*?)\\]
        """,
        re.VERBOSE,
    )

    equations = []
    for m in pattern.finditer(tex_content):
        if m.group("env"):
            env, body = m.group("env"), m.group("body")
        else:
            env, body = "brackets", m.group("bracket_body")

        # 1 strip TeX comments
        body_clean = re.sub(COMMENT_RE, "", body).strip()

        # (recommended) also strip labels everywhere
        body_clean = re.sub(r"\\label\{[^\}]*\}", "", body_clean)

        # Normalize blank lines to avoid paragraph breaks inside math
        body_clean = normalize_equation_body(body_clean)

        #  quick exit if nothing but comments
        if not body_clean:
            continue

        #  drop trailing punctuation
        body_clean = strip_trailing_punctuation(body_clean)
        
        # If the ONLY thing left is a \label{…}, treat as empty
        if not re.sub(r"\\label\{[^\}]*\}", "", body_clean).strip():
            continue

        equations.append((env, body_clean))

    return equations

def normalize_equation_body(s: str) -> str:
    """Trim empty lines at the start/end and collapse internal blank runs."""
    # strip leading/trailing blank lines
    lines = s.splitlines()
    while lines and lines[0].strip() == '':
        lines.pop(0)
    while lines and lines[-1].strip() == '':
        lines.pop()
    # collapse consecutive internal blank lines to a single blank
    out = []
    prev_blank = False
    for ln in lines:
        is_blank = (ln.strip() == '')
        if is_blank and prev_blank:
            continue
        out.append(ln)
        prev_blank = is_blank
    return '\n'.join(out)


def strip_trailing_punctuation(equation):
    return re.sub(r'[.,;:](\s*\\label\{[^\}]*\})?\s*$', '', equation.strip())

def has_balanced_brackets(command):
    return command.count('{') == command.count('}')


def extract_command_with_content(command_start, preamble):
    start_index = command_start
    end_index = start_index
    count_open_brackets = 0
    for i, char in enumerate(preamble[start_index:]):
        if char == '{':
            count_open_brackets += 1
        elif char == '}':
            count_open_brackets -= 1
        if count_open_brackets == 0:
            end_index = start_index + i
            break
    else:
        return None
    return preamble[start_index:end_index + 1]


def extract_relevant_commands(preamble):
    relevant_content = ''
    usepackage_matches = [match for match in re.findall(
        r'(?<!^%)\\usepackage.*?\n', preamble, re.MULTILINE) if not match.strip().startswith('%')]
    relevant_content += '\n'.join(usepackage_matches) + '\n'

    cmd_patterns = [r'\\newcommand\*?', r'\\renewcommand\*?',
                    r'\\providecommand\*?', r'\\let']
    for pattern in cmd_patterns:
        for match_start in [m.start() for m in re.finditer(pattern, preamble)]:
            full_command = extract_command_with_content(match_start, preamble)
            if full_command.strip() != '\\':
                relevant_content += full_command + '\n'

    math_ops = [match for match in re.findall(
        r'(?<!^%)\\DeclareMathOperator.*?\n', preamble, re.MULTILINE) if not match.strip().startswith('%')]
    relevant_content += '\n'.join(math_ops) + '\n'
    return relevant_content.strip()


# ─────────────────────────────────────────────
#  CREATE STANDALONE TEX (starred envs)
# ─────────────────────────────────────────────
def create_equation_file(eq_tuple, output_dir, equation_index, relevant_content):
    env, body = eq_tuple
    eq_tex = '\\documentclass[preview,varwidth]{standalone}\n'
    # Load packages BEFORE macros so \DeclareMathOperator exists
    eq_tex += '\\usepackage{amsmath,amssymb,amsfonts,mathtools,amsthm}\n'
    if relevant_content:
        eq_tex += relevant_content + '\n'
    eq_tex += '\\begin{document}\n'

    if env == 'brackets':  # \[ ... \]
        eq_tex += '\\[\n' + body + '\n\\]\n'
    else:
        STAR_CAPABLE = {'equation', 'align', 'gather', 'multline'}  # NOT displaymath
        base = env.rstrip('*')
        if base in STAR_CAPABLE:
            star_env = env if env.endswith('*') else env + '*'
        else:
            star_env = env  # keep displaymath unstarred

        if not env.endswith('*'):
            body = re.sub(r'\\label\{.*?\}', '', body)

        # Optional: render displaymath using \[ ... \] instead of the environment
        if base == 'displaymath':
            eq_tex += '\\[\n' + body + '\n\\]\n'
        else:
            eq_tex += f'\\begin{{{star_env}}}\n{body}\n\\end{{{star_env}}}\n'

    eq_tex += '\\end{document}\n'

    tex_path = os.path.abspath(os.path.join(output_dir, f'{equation_index}.tex'))
    with open(tex_path, 'w', encoding='utf-8') as f:
        f.write(eq_tex)
    return tex_path


def compile_equation(equation_file):
    equation_basename = os.path.splitext(os.path.basename(equation_file))[0]
    output_dir = os.path.dirname(equation_file)
    pdf_file = os.path.join(output_dir, f'{equation_basename}.pdf')

    if os.path.isfile(pdf_file):
        print(f'Skipping compilation for {equation_basename}.pdf. PDF file already exists.')
        return equation_basename

    # Detect TeX engine
    tex_engine = shutil.which("pdflatex") or "pdflatex"

    cmd = [tex_engine, '-interaction=nonstopmode', '-halt-on-error',
           '-output-directory', output_dir, equation_file]

    process = None
    timed_out = False

    def on_timeout():
        nonlocal timed_out, process
        timed_out = True
        if process is not None and process.poll() is None:
            process.kill()

    timer = threading.Timer(60, on_timeout)
    try:
        timer.start()
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = process.communicate()
    finally:
        timer.cancel()

    if timed_out:
        print(f'Equation {equation_basename} TIMED OUT (killed).')
        return None

    if process.returncode != 0:
        print(f'Equation {equation_basename} FAILED with return code {process.returncode}.')
        if out: print(out.decode(errors='ignore'))
        if err: print(err.decode(errors='ignore'))
        return None

    if not os.path.exists(pdf_file):
        print(f'Equation {equation_basename} FAILED: no PDF produced.')
        if out: print(out.decode(errors='ignore'))
        if err: print(err.decode(errors='ignore'))
        return None

    print(f'Equation {equation_basename} compiled successfully.')
    return equation_basename

def find_inkscape():
    # 1) explicit env var wins
    if os.getenv("INKSCAPE"):
        path = os.getenv("INKSCAPE")
        print(f"Inkscape path from $INKSCAPE: {path}")
        return path

    # 2) PATH
    p = shutil.which("inkscape")
    if p:
        print(f"Inkscape found in PATH: {p}")
        return p

    # 3) OS-specific fallbacks
    fallbacks = []
    if sys.platform.startswith("win"):
        fallbacks = [
            r"C:\Program Files\Inkscape\bin\inkscape.exe",
            r"C:\Program Files\Inkscape\inkscape.exe",
        ]
    elif sys.platform == "darwin":
        fallbacks = ["/Applications/Inkscape.app/Contents/MacOS/inkscape"]
    else:  # linux/bsd
        fallbacks = ["/usr/bin/inkscape", "/usr/local/bin/inkscape"]

    for f in fallbacks:
        if os.path.exists(f):
            print(f"Inkscape found in fallback location: {f}")
            return f

    print("Inkscape not found in PATH, $INKSCAPE, or fallback locations.")
    return None

def convert_pdf_to_svg(pdf_file, svg_file, inkscape_path):
    if os.path.exists(svg_file):
        print(f"SVG file {svg_file} already exists. Skipping conversion.")
        return
    try:
        subprocess.run([inkscape_path, '--pdf-poppler', '--export-type=svg',
                        '--export-filename=' + svg_file, pdf_file], check=True)
        print(f"Successfully converted {pdf_file} → SVG.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to convert {pdf_file} to SVG. Error: {e}")
    except FileNotFoundError:
        print(f"Inkscape executable not found at path {inkscape_path}.")

if __name__ == "__main__":
    # ----------- CLI args -------------------------------------------------
    tex_file_arg   = sys.argv[1] if len(sys.argv) > 1 else None
    output_folder  = sys.argv[2] if len(sys.argv) > 2 else None

    # Search set
    tex_files = glob.glob("*.tex") if tex_file_arg is None else [tex_file_arg]
    print(f"Processing input files: {tex_files}")
    if not tex_files:
        print("No .tex files found.")
        sys.exit(1)

    # Determine which files are included by others → we will skip those
    tex_files_abs = [os.path.abspath(p) for p in tex_files]
    included_by_others = find_included_files(tex_files_abs)

    # Detect TeX engine ONCE
    tex_engine = shutil.which("pdflatex") or "pdflatex"
    if tex_engine:
        print(f"Using TeX engine: {tex_engine}")
    else:
        print("Warning: No TeX engine (pdflatex) found in PATH.")

    # ----------- loop over root .tex files only ---------------------------
    for tex_path in tex_files:
        abs_tex_path = os.path.abspath(tex_path)
        if abs_tex_path in included_by_others:
            print(f"\n=== {tex_path} (skipping: included by another file) ===")
            continue

        print(f"\n=== {tex_path} ===")

        # choose / create output directory (one per root file)
        out_dir = (os.path.splitext(tex_path)[0] if output_folder is None else output_folder)
        os.makedirs(out_dir, exist_ok=True)
        print(f"Output directory: {out_dir}")

        # read source
        try:
            with open(tex_path, encoding="utf-8", errors="ignore") as f:
                original_src = f.read()
        except Exception as e:
            print(f"Error reading {tex_path}: {e}")
            continue

        # check if merging is required (any \input/\include present)
        has_includes = bool(INCLUDE_RE.search(strip_comments(original_src)))

        # expand includes (always produce a working text; optionally write tmp if merged)
        combined_src = expand_inputs(original_src, tex_path)

        # decide if this is a "full" document (preamble/content marker present)
        is_full = looks_full_document(combined_src)
        print(f"Has includes: {has_includes} | Looks full: {is_full}")

        # if merging required, write a tmp combined file into output folder
        work_tex_path = tex_path
        if has_includes:
            base = os.path.splitext(os.path.basename(tex_path))[0]
            work_tex_path = os.path.abspath(os.path.join(out_dir, f"combined_{base}.tex"))
            with open(work_tex_path, 'w', encoding='utf-8') as out_f:
                out_f.write(combined_src)
            print(f"[combine] Wrote: {work_tex_path}")

        # If not a full doc, skip extracting/compiling (per your requirement)
        if not is_full:
            print("[combine] Skipping (not a full document: no \\documentclass or \\begin{document}).")
            continue

        # Split preamble/body from the *working* tex
        try:
            with open(work_tex_path, encoding="utf-8", errors="ignore") as f:
                tex_source = f.read()
        except Exception as e:
            print(f"Error reading working file {work_tex_path}: {e}")
            continue

        m = re.search(r'\\begin{document}', tex_source)
        preamble = tex_source[: m.start()] if m else ""

        # ------ find all equations & which macros they use ----------------
        equations = find_equations(work_tex_path)
        print(f"Found {len(equations)} equation environment(s).")

        needed_macros = set().union(*(macros_in_equation(b) for _, b in equations)) if equations else set()
        print("User-defined macros referenced:", sorted(needed_macros))

        relevant_content = collect_definitions(needed_macros, preamble)
        if relevant_content:
            print("Copied macro definitions:")
            print(textwrap.indent(relevant_content, "    "))
        else:
            print("No user-defined macro definitions needed.")

        # ------ build every equation --------------------------------------
        for idx, eq in enumerate(equations, start=1):
            tex_file = create_equation_file(eq, out_dir, idx, relevant_content)
            compile_equation(tex_file)

        # ------ set sane perms on *nix (optional) -------------------------
        if os.name != "nt":
            for pdf in glob.glob(os.path.join(out_dir, "*.pdf")):
                try:
                    os.chmod(pdf, 0o644)
                except Exception as e:
                    print("chmod failed:", e)

        # ------ PDF → SVG conversion (skip if Inkscape missing) ----------
        inkscape_exe = find_inkscape()
        if not inkscape_exe:
            print("Inkscape not found; set $INKSCAPE or add to PATH. Skipping PDF→SVG.")
        else:
            for pdf in glob.glob(os.path.join(out_dir, "*.pdf")):
                svg = pdf[:-4] + ".svg"
                convert_pdf_to_svg(pdf, svg, inkscape_exe)
