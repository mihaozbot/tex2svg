import re
import os
import glob
import subprocess
import threading
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
COMMENT_RE = re.compile(r'(^|[^\\])%.*$', re.MULTILINE)   # TeX comment lines

MACRO_RE = re.compile(r'\\([A-Za-z@]+)')   # skip ctrl-symbols like \&, \%, …

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
# ─────────────────────────────────────────────
#  FIND EQUATIONS  (returns (env, body) tuples)
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
    if relevant_content:
        eq_tex += relevant_content + '\n'
    eq_tex += '\\usepackage{amsmath,amssymb,amsfonts,mathtools,amsthm}\n'
    eq_tex += '\\begin{document}\n'

    if env == 'brackets':                              #  \[ ... \]
        eq_tex += '\\[\n' + body + '\n\\]\n'
    else:
        star_env = env if env.endswith('*') else env + '*'
        if not env.endswith('*'):                      # we just added the *
            body = re.sub(r'\\label\{.*?\}', '', body) # ← strip labels
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

    cmd = ['pdflatex', '-interaction=nonstopmode', '-halt-on-error',
           '-output-directory', output_dir, equation_file]

    process = None
    timed_out = False

    def on_timeout():
        nonlocal timed_out, process
        timed_out = True
        if process is not None and process.poll() is None:
            process.kill()

    timer = threading.Timer(60, on_timeout)  # was 10s; give MiKTeX time to load/install
    try:
        timer.start()
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = process.communicate()
    finally:
        timer.cancel()

    # Diagnose outcomes
    if timed_out:
        print(f'Equation {equation_basename} TIMED OUT (killed).')
        return None

    if process.returncode != 0:
        print(f'Equation {equation_basename} FAILED with return code {process.returncode}.')
        # surface log to help debugging
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
    import shutil, textwrap

    # ----------- CLI args -------------------------------------------------
    tex_file_arg   = sys.argv[1] if len(sys.argv) > 1 else None
    output_folder  = sys.argv[2] if len(sys.argv) > 2 else None

    tex_files = glob.glob("*.tex") if tex_file_arg is None else [tex_file_arg]
    print(f"Processing input files: {tex_files}")

    # ----------- loop over .tex files ------------------------------------
    for tex_path in tex_files:
        print(f"\n=== {tex_path} ===")

        # read full source once
        with open(tex_path, encoding="utf-8", errors="ignore") as f:
            tex_source = f.read()

        # split preamble / body (first \begin{document})
        m = re.search(r'\\begin{document}', tex_source)
        preamble = tex_source[: m.start()] if m else ""

        # ------ find all equations & which macros they use ----------------
        equations = find_equations(tex_path)
        print(f"Found {len(equations)} equation environment(s).")

        needed_macros = set().union(*(macros_in_equation(b) for _, b in equations))
        print("User-defined macros referenced:", sorted(needed_macros))

        relevant_content = collect_definitions(needed_macros, preamble)
        if relevant_content:
            print("Copied macro definitions:")
            print(textwrap.indent(relevant_content, "    "))
        else:
            print("No user-defined macro definitions needed.")

        # ------ choose / create output directory --------------------------
        out_dir = (
            os.path.splitext(tex_path)[0] if output_folder is None else output_folder
        )
        os.makedirs(out_dir, exist_ok=True)
        print(f"Output directory: {out_dir}")

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
        inkscape_exe = shutil.which("inkscape") or r"C:\Program Files\Inkscape\bin\inkscape.exe"
        if not shutil.which(inkscape_exe):
            print("Inkscape not found; skipping PDF→SVG conversion.")
            continue

        for pdf in glob.glob(os.path.join(out_dir, "*.pdf")):
            svg = pdf[:-4] + ".svg"
            convert_pdf_to_svg(pdf, svg, inkscape_exe)
            print("Created", svg)