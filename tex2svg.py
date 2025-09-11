import re
import os
import glob
import subprocess
import threading
import sys
import shutil
import textwrap
import tempfile
from typing import List, Dict, Optional, Tuple


os.chdir(os.path.dirname(os.path.abspath(__file__)))
COMMENT_RE = re.compile(r'(^|[^\\])%.*$', re.MULTILINE)   # TeX comment lines
MACRO_RE = re.compile(r'\\([A-Za-z@]+)')   # skip ctrl-symbols like \&, \%, …
INCLUDE_RE = re.compile(r'\\(?:input|include)\{([^}]+)\}')
VERBATIM_ENVS = ("verbatim", "lstlisting", "minted", "Verbatim")
TIMER_TIMEOUT = 20
AUTO_LABEL_PREFIX = "__ext_eq"



def _clean_tag_for_filename(raw: Optional[str]) -> str:
    if not raw:
        return "unnumbered"
    t = raw.strip()
    if t.startswith('(') and t.endswith(')'):
        t = t[1:-1].strip()
    t = re.sub(r'\s+', '_', t)
    t = re.sub(r'[^\w\.\-]', '_', t)
    if not t:
        return "unnumbered"
    return t

def make_filename_with_tag(idx: int, display_name: Optional[str], width: int = 3, prefix: str = "Eq") -> str:
    num = f"{idx:0{width}d}"
    tag = _clean_tag_for_filename(display_name)
    return f"{prefix}_{num}_({tag}).tex"


# ----------------------- label-injection & aux parsing -----------------------
def should_label_for_numbering(env_name: str) -> bool:
    if env_name.endswith('*'):
        return False
    base = env_name.rstrip('*')
    return base in ('equation', 'align', 'gather', 'multline')

def _normalize_printed_name_for_prechapter(printed: Optional[str], eq_meta: Dict) -> Optional[str]:
    if not printed:
        return None
    # If equation appears before the first \chapter, drop a leading "0."
    if eq_meta.get("chapter_index") is None:
        m = re.match(r'^0\.(.+)$', printed)
        if m:
            return m.group(1)
    return printed

def inject_labels_into_temp_source(clean_text: str, eqs: List[Dict]) -> Tuple[str, List[Optional[str]]]:
    """
    Insert \label{__ext_eqN} for equations that can number and don't already
    have \label or \tag. Works left-to-right using substring search so it’s
    robust to earlier edits.
    """
    out = clean_text
    auto_labels = [None] * len(eqs)
    cursor = 0

    for i, e in enumerate(eqs):
        env = e["env"]
        if e.get("is_starred") or not should_label_for_numbering(env):
            continue
        if e.get("has_label") or e.get("has_tag"):
            # Respect explicit \label or \tag
            continue

        body = e["clean_body"]
        j = out.find(body, cursor)
        if j == -1:
            # fallback: try from the beginning (handles duplicates OK-ish)
            j = out.find(body)
            if j == -1:
                continue

        base = env.rstrip('*')

        # Find where to place the label (before \end{<env>} or the closing \])
        if env == 'brackets' or base == 'displaymath':
            m_end = re.search(r'\\\]\s*', out[j:j + len(body) + 2000])
        else:
            m_end = re.search(r'\\end\{\s*' + re.escape(base) + r'\s*\}', out[j:j + len(body) + 2000])

        end_pos = j + len(body) if not m_end else j + m_end.start()
        region = out[j:end_pos]

        # If region already has a label/tag, skip
        if re.search(r'\\label\{', region) or re.search(r'\\tag\{', region):
            cursor = end_pos
            continue

        lbl = f"{AUTO_LABEL_PREFIX}{i+1}"
        insertion = f"\\label{{{lbl}}}"
        out = out[:end_pos] + insertion + out[end_pos:]
        auto_labels[i] = lbl
        cursor = end_pos + len(insertion)

    return out, auto_labels

def compile_temp_and_parse_aux(temp_src: str, timeout: int = 30) -> Optional[Dict[str,str]]:
    tex_engine = shutil.which("pdflatex") or "pdflatex"
    with tempfile.TemporaryDirectory() as tmpdir:
        tex_name = "temp_for_labels.tex"
        tex_path = os.path.join(tmpdir, tex_name)
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(temp_src)
        # Run inside tmpdir and compile the basename
        cmd = [tex_engine, '-interaction=nonstopmode', '-halt-on-error', tex_name]
        try:
            proc = subprocess.run(cmd, cwd=tmpdir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return None
        aux_path = os.path.join(tmpdir, "temp_for_labels.aux")
        if not os.path.exists(aux_path):
            return None
        mapping = {}
        newlabel_re = re.compile(r'\\newlabel\{(?P<label>[^\}]+)\}\{\{(?P<printed>[^}]*)\}')
        with open(aux_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = newlabel_re.search(line)
                if m:
                    mapping[m.group("label")] = m.group("printed")
        return mapping

def _drop_unresolved_inputs(src: str) -> str:
    return re.sub(r'\\(?:input|include)\{[^}]+\}', '% dropped missing input\n', src)

def _sanitize_for_temp_compile(s: str) -> str:
    # Drop unresolved inputs/includes (avoid missing files in tmp)
    s = re.sub(r'\\(?:input|include)\{[^}]+\}', '%<removed-in-temp>', s)

    # Keep only the first \documentclass and \begin{document}
    def keep_first(pattern, text):
        hits = list(re.finditer(pattern, text))
        if not hits:
            return text
        first = hits[0]
        # comment out all but the first occurrence
        parts = []
        last = 0
        for i, m in enumerate(hits):
            parts.append(text[last:m.start()])
            parts.append(text[m.start():m.end()] if i == 0 else '%<removed-in-temp>')
            last = m.end()
        parts.append(text[last:])
        return ''.join(parts)

    s = keep_first(r'\\documentclass(?:\[[^\]]*\])?\{[^\}]+\}', s)
    s = keep_first(r'\\begin\{document\}', s)

    # Remove all \end{document} tokens; add one at end
    s = re.sub(r'\\end\{document\}', '%<removed-in-temp>', s)
    if not s.rstrip().endswith(r'\end{document}'):
        s = s.rstrip() + '\n\\end{document}\n'
    return s

def map_equations_to_display_names(clean_src: str, eq_dicts: List[Dict]) -> List[Optional[str]]:
    temp_src, auto_labels = inject_labels_into_temp_source(clean_src, eq_dicts)
    temp_src = _sanitize_for_temp_compile(temp_src)  # <— important
    label_map = compile_temp_and_parse_aux(temp_src)
    if label_map is None:
        return [None] * len(eq_dicts)

    results = []
    for i, e in enumerate(eq_dicts):
        if e["has_tag"]:
            results.append(e["tag_text"])
            continue

        # Only trust \label if the env can number (equation/align/gather/multline)
        if should_label_for_numbering(e["env"]) and e["has_label"] and e["label_name"] in label_map:
            results.append(label_map[e["label_name"]])
            continue

        auto = auto_labels[i]
        if auto and auto in label_map:
            results.append(label_map[auto])
            continue

        results.append(None)
    return results

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

def _strip_comments_by_line(text: str) -> str:
    """Remove unescaped % and the rest of the line.
    - Treat % as escaped when preceded by an odd number of backslashes.
    - If the line is only a comment (or whitespace + comment), remove the whole line
      (no blank line left).
    - If there's code before %, keep that code and preserve the newline.
    """
    out_lines = []
    for line in text.splitlines(keepends=True):
        i = 0
        removed = False
        while True:
            p = line.find('%', i)
            if p == -1:
                break
            # count consecutive backslashes immediately before p
            j = p - 1
            bs = 0
            while j >= 0 and line[j] == '\\':
                bs += 1
                j -= 1
            if bs % 2 == 0:
                # unescaped % -> decide whether line is comment-only
                prefix = line[:p]
                # if prefix contains only whitespace, drop the whole line
                if prefix.strip() == '':
                    # drop the entire line (no newline), i.e. remove the blank line
                    line = ''
                else:
                    # keep code before %, strip trailing whitespace, preserve newline
                    newline = '\n' if line.endswith('\n') else ''
                    line = prefix.rstrip() + newline
                removed = True
                break
            else:
                # escaped %, continue search after this %
                i = p + 1
        # If there was no % at all and the line is just whitespace and you want to
        # remove blank lines globally, you can optionally strip it here.
        out_lines.append(line)
    return ''.join(out_lines)


def strip_comments_preserve_verbatim(text: str) -> str:
    """Remove comments outside of verbatim-like environments, preserving verbatim blocks."""
    # build a pattern to find verbatim blocks
    envs = "|".join(re.escape(e) for e in VERBATIM_ENVS)
    if envs:
        verbpat = re.compile(r'(?s)\\begin\{(' + envs + r')\}.*?\\end\{\1\}')
    else:
        verbpat = None

    out = []
    pos = 0
    if verbpat:
        for m in verbpat.finditer(text):
            # strip comments from the chunk before this verbatim block
            out.append(_strip_comments_by_line(text[pos:m.start()]))
            # append verbatim block unchanged
            out.append(m.group(0))
            pos = m.end()
    out.append(_strip_comments_by_line(text[pos:]))
    return ''.join(out)


# ─────────────────────────────────────────────
#  FIND EQUATIONS (returns (env, body) tuples)
# ─────────────────────────────────────────────
def find_equations(clean_tex_text: str):
    """
    Find equations in *cleaned* TeX text (comments removed, verbatim preserved).
    Return list of dicts with keys:
      - env: 'equation', 'align', 'brackets', ...
      - raw_body: the exact captured body (before we strip labels/tags)
      - clean_body: body with labels removed and normalized (same as previous behavior)
      - start,end: slice indices into the provided clean_tex_text (so callers can inject)
      - has_label, label_name  (label that was present originally, if any)
      - has_tag, tag_text      (\tag{...} present in the original body, if any)
      - is_starred             True if env endswith '*'
      - chapter_index          1-based chapter number appearing earlier in the doc, or None
    """
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

    eqs = []
    # Precompute cumulative chapter indices by scanning for \chapter commands
    chapter_positions = []
    for m in re.finditer(r'\\chapter\*?\s*\{', clean_tex_text):
        # count as a new chapter; position = m.start()
        chapter_positions.append(m.start())

    for m in pattern.finditer(clean_tex_text):
        if m.group("env"):
            env = m.group("env")
            raw_body = m.group("body")
            start = m.start("body")
            end = m.end("body")
        else:
            env = "brackets"
            raw_body = m.group("bracket_body")
            start = m.start("bracket_body")
            end = m.end("bracket_body")

        # detect starred env
        is_starred = env.endswith('*')

        # Detect \tag{...} or \label{...} in the raw body (they may be anywhere)
        m_tag = re.search(r'\\tag\{([^}]*)\}', raw_body)
        has_tag = bool(m_tag)
        tag_text = m_tag.group(1) if m_tag else None

        m_label = re.search(r'\\label\{([^}]*)\}', raw_body)
        has_label = bool(m_label)
        label_name = m_label.group(1) if m_label else None

        # Build clean_body (remove labels and strip comments again locally)
        body_no_labels = re.sub(r'\\label\{[^\}]*\}', '', raw_body)
        body_no_labels_tags = re.sub(r'\\tag\*?\s*\{[^{}]*\}', '', body_no_labels)
        body_clean = normalize_equation_body(body_no_labels_tags)
        body_clean = strip_trailing_punctuation(body_clean)

        if not body_clean:
            continue

        # Determine chapter index: largest chapter_position < start
        chap_idx = None
        for i, pos in enumerate(chapter_positions, start=1):
            if pos < start:
                chap_idx = i
            else:
                break

        eqs.append({
            "env": env,
            "raw_body": raw_body,
            "clean_body": body_clean,
            "start": start,
            "end": end,
            "has_label": has_label,
            "label_name": label_name,
            "has_tag": has_tag,
            "tag_text": tag_text,
            "is_starred": is_starred,
            "chapter_index": chap_idx,
        })

    return eqs


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
def create_equation_file(eq_tuple, output_path, relevant_content):
    """
    Write a standalone, numberless .tex at output_path for eq_tuple=(env, body).
    No \tag is inserted; star-envs are used so the output shows no numbers.
    """
    env, body = eq_tuple
    base = env.rstrip('*')
    body = re.sub(r'\\tag\*?\s*\{[^{}]*\}', '', body)
    
    parts = []
    parts.append('\\documentclass[preview,varwidth]{standalone}\n')
    parts.append('\\usepackage{amsmath,amssymb,amsfonts,mathtools,amsthm}\n')
    if relevant_content:
        parts.append(relevant_content + '\n')
    parts.append('\\begin{document}\n')

    if env == 'brackets' or base == 'displaymath':
        # render bracket/displaymath uniformly as \[ ... \]
        parts.append('\\[\n' + body + '\n\\]\n')
    else:
        # use the *starred* environment to suppress numbering
        STAR_CAPABLE = {'equation', 'align', 'gather', 'multline'}
        star_env = (base + '*') if base in STAR_CAPABLE else base
        parts.append(f'\\begin{{{star_env}}}\n{body}\n\\end{{{star_env}}}\n')

    parts.append('\\end{document}\n')

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(''.join(parts))
    return output_path

def compile_equation(equation_file):
    equation_basename = os.path.splitext(os.path.basename(equation_file))[0]
    output_dir = os.path.dirname(equation_file)
    pdf_file = os.path.join(output_dir, f'{equation_basename}.pdf')

    if os.path.isfile(pdf_file):
        print(f'Skipping compilation for {equation_basename}.pdf. PDF file already exists.')
        return equation_basename

    tex_engine = shutil.which("pdflatex") or "pdflatex"
    # Run in the output dir and pass only the basename → no backslashes in TeX argument
    cmd = [tex_engine, '-interaction=nonstopmode', '-halt-on-error', f'{equation_basename}.tex']

    process = None
    timed_out = False

    def on_timeout():
        nonlocal timed_out, process
        timed_out = True
        if process is not None and process.poll() is None:
            process.kill()

    timer = threading.Timer(TIMER_TIMEOUT, on_timeout)
    try:
        timer.start()
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=output_dir)
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

        tex_source_clean = strip_comments_preserve_verbatim(tex_source)
        equation_dicts = find_equations(tex_source_clean)  # dicts with start/end

        # macros:
        needed_macros = set().union(*(macros_in_equation(e["clean_body"]) for e in equation_dicts)) if equation_dicts else set()

        # printed names:
        display_names = map_equations_to_display_names(tex_source_clean, equation_dicts)

        # Optional: drop "0." for pre-chapter equations
        def _normalize_printed_name_for_prechapter(printed: Optional[str], e: Dict) -> Optional[str]:
            if printed and e.get("chapter_index") is None:
                m = re.match(r'^0\.(.+)$', printed)
                if m:
                    return m.group(1)
            return printed

        display_names = [_normalize_printed_name_for_prechapter(n, e)
                        for e, n in zip(equation_dicts, display_names)]
        
        m = re.search(r'\\begin{document}', tex_source)
        preamble = tex_source[: m.start()] if m else ""

        relevant_content = collect_definitions(needed_macros, preamble)
        # write/compile numberless files, name with printed/tag
        for idx, (e, dname) in enumerate(zip(equation_dicts, display_names), start=1):
            fname = make_filename_with_tag(idx, dname)  # e.g., 024_(End).tex or 015_(Var).tex or 010_(unnumbered).tex
            out_path = os.path.join(out_dir, fname)
            create_equation_file((e["env"], e["clean_body"]), out_path, relevant_content)  # numberless output
            compile_equation(out_path)
            
        # ------ PDF → SVG conversion (skip if Inkscape missing) ----------
        inkscape_exe = find_inkscape()
        if not inkscape_exe:
            print("Inkscape not found; set $INKSCAPE or add to PATH. Skipping PDF→SVG.")
        else:
            for pdf in glob.glob(os.path.join(out_dir, "*.pdf")):
                svg = pdf[:-4] + ".svg"
                convert_pdf_to_svg(pdf, svg, inkscape_exe)
