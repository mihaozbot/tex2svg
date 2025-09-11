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
TIMER_TIMEOUT = 30
AUTO_LABEL_PREFIX = "__ext_eq"
DEBUG_MODE = True


def extract_chapters(clean_text: str):
    """Return list of (pos, title) of \\chapter{...} or \\chapter*{...} in cleaned text."""
    chap_re = re.compile(r'\\chapter\*?\s*\{([^}]*)\}')
    return [(m.start(), m.group(1)) for m in chap_re.finditer(clean_text)]

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

# Replace or add these helper functions in your script

def _extract_usepackage_names(src: str) -> List[str]:
    """Return a flat list of package names referenced by \\usepackage{...} in src."""
    names = []
    usepat = re.compile(r'\\usepackage(?:\s*\[[^\]]*\])?\s*\{([^}]*)\}', re.MULTILINE)
    for m in usepat.finditer(src):
        pkgs = [p.strip() for p in m.group(1).split(',') if p.strip()]
        names.extend(pkgs)
    return names

def _package_exists(pkg_name: str) -> bool:
    """Return True if kpsewhich finds pkg_name.sty or a local search finds it."""
    if not pkg_name:
        return False

    # 1) kpsewhich check
    kpsewhich = shutil.which("kpsewhich")
    if kpsewhich:
        try:
            p = subprocess.run([kpsewhich, f"{pkg_name}.sty"],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, timeout=5)
            if p.stdout and p.stdout.strip():
                return True
        except Exception:
            # fall through to local search
            pass

    # 2) search in TEXINPUTS (if set) and working dir, and upward parents — covers common setups
    search_paths = []

    # TEXINPUTS environment variable (colon/semicolon separated)
    texinputs = os.environ.get("TEXINPUTS")
    if texinputs:
        search_paths.extend([p for p in texinputs.split(os.pathsep) if p])

    # current working directory and parent chain up to filesystem root
    cwd = os.getcwd()
    search_paths.append(cwd)
    # also include the script dir
    try:
        search_paths.append(os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        pass

    # Walk each path looking for "<pkg_name>.sty"
    target = f"{pkg_name}.sty"
    for base in search_paths:
        if not base:
            continue
        # if the path is a single file path, check directly
        candidate = os.path.join(base, target)
        if os.path.exists(candidate):
            return True
        # otherwise do a shallow walk (avoid huge recursion)
        for root, dirs, files in os.walk(base):
            if target in files:
                return True
            # limit depth: avoid extremely deep scans
            # stop walking deeper than a few levels
            # (we rely on kpsewhich for system-wide, local repos are often shallow)
            # No explicit break here — os.walk won't tell depth; rely on typical directory layouts.

    # not found
    return False


def compile_temp_and_parse_aux(temp_src: str, timeout: int = 30, debug_dir: Optional[str] = None) -> Optional[Dict[str,str]]:
    """
    Compile temp_src with pdflatex and parse the .aux for \\newlabel mappings.
    If missing packages are detected, create minimal stub .sty files in the temp dir
    so pdflatex will not error out on missing package.
    """
    tex_engine = shutil.which("pdflatex") or "pdflatex"

    # Choose where to write files
    if debug_dir:
        os.makedirs(debug_dir, exist_ok=True)
        tmpdir_cm = None
        tmpdir = debug_dir
    else:
        tmpdir_cm = tempfile.TemporaryDirectory()
        tmpdir = tmpdir_cm.__enter__()

    try:
        # Create temp tex file
        tex_name = "temp_for_labels.tex"
        tex_path = os.path.join(tmpdir, tex_name)

        # detect usepackage names and missing packages BEFORE writing file,
        # so we can add stubs into tmpdir
        pkgs = _extract_usepackage_names(temp_src)
        missing = [p for p in pkgs if not _package_exists(p)]

        if missing and debug_dir:
            print(f"[debug] Missing packages (will create stubs): {missing}")
        elif missing:
            print(f"[debug] Missing packages detected: {missing} (creating stubs in tmpdir)")

        # create tiny stubs for missing packages in tmpdir so pdflatex will find them
        for p in missing:
            try:
                stub_path = os.path.join(tmpdir, f"{p}.sty")
                # only create if doesn't already exist
                if not os.path.exists(stub_path):
                    with open(stub_path, "w", encoding="utf-8") as sf:
                        sf.write(f"%% stub created by tex2svg for missing package {p}\n")
                        sf.write(f"\\ProvidesPackage{{{p}}}[2025/01/01 stub]\n")
                        # minimal safe defaults: don't override user macros, just stop processing
                        sf.write("% Add minimal safe definitions here if needed by your equations\n")
                        sf.write("\\endinput\n")
                    if debug_dir:
                        print(f"[debug] Stub written: {stub_path}")
            except Exception as ex:
                if debug_dir:
                    print(f"[debug] Failed creating stub for {p}: {ex}")

        # Now write the temp tex into tmpdir
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(temp_src)

        # Run pdflatex inside tmpdir
        cmd = [
            tex_engine,
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-file-line-error",
            tex_name,
        ]
        try:
            proc = subprocess.run(
                cmd, cwd=tmpdir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired:
            if debug_dir:
                with open(os.path.join(tmpdir, "compile_timeout.log"), "wb") as lf:
                    lf.write(b"pdflatex timeout\n")
            return None

        # Save logs if debugging
        if debug_dir:
            with open(os.path.join(tmpdir, "compile_stdout.log"), "wb") as lf:
                lf.write(proc.stdout or b"")
            with open(os.path.join(tmpdir, "compile_stderr.log"), "wb") as lf:
                lf.write(proc.stderr or b"")

        aux_path = os.path.join(tmpdir, "temp_for_labels.aux")
        pdf_path = os.path.join(tmpdir, "temp_for_labels.pdf")
        log_path = os.path.join(tmpdir, "temp_for_labels.log")

        if not os.path.exists(aux_path):
            # If AUX is missing, keep artifacts when debug_dir is set; print a hint.
            if debug_dir:
                print(f"[debug] temp compile produced no AUX. See: {tmpdir}")
                if os.path.exists(log_path):
                    print(f"[debug] LaTeX log: {log_path}")
            return None

        # Parse label mappings: \newlabel{label}{{printed}{page}...}
        mapping = {}
        newlabel_re = re.compile(r'\\newlabel\{(?P<label>[^\}]+)\}\{\{(?P<printed>[^}]*)\}')
        with open(aux_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = newlabel_re.search(line)
                if m:
                    mapping[m.group("label")] = m.group("printed")

        # Helpful print
        if debug_dir:
            print(f"[debug] numbering AUX parsed. Labels found: {len(mapping)} | Debug dir: {tmpdir}")
            if os.path.exists(pdf_path):
                print(f"[debug] temp_for_labels.pdf saved for inspection.")

        return mapping
    finally:
        if tmpdir_cm:
            tmpdir_cm.__exit__(None, None, None)

# ------------------ new helper: build a minimal doc for numbering ------------------
def _extract_preamble_and_body(src: str) -> Tuple[str,str]:
    """Return (preamble, body) splitting at \\begin{document} (works on cleaned src)."""
    m = re.search(r'\\begin\{document\}', src)
    if not m:
        return src, ""
    return src[:m.start()], src[m.end():]

def _collect_usepackages(preamble: str) -> List[str]:
    """Return non-commented \\usepackage[...]{} lines from the preamble (keeps options)."""
    return re.findall(r'(?m)^(?!\s*%)(\\usepackage(?:\[[^\]]*\])?\{[^\}]+\})', preamble)

def _find_numberwithin(preamble: str) -> Optional[str]:
    """Return the \\numberwithin{equation}{...} line if present, else None."""
    m = re.search(r'(?m)^(?!\s*%)(\\numberwithin\{equation\}\{[^\}]+\})', preamble)
    return m.group(1) if m else None


def build_minimal_numbering_doc(clean_src: str, eqs: List[Dict]) -> str:
    """
    Build a tiny .tex that uses the SAME header as create_equation_file
    (standalone + ams* packages), but still reproduces chapter-based numbering.

    - No original preamble is imported.
    - Chapters are simulated (define a 'chapter' counter) and stepped where they appear.
    - One \begin{equation}...\end{equation} shell per *numberable* environment,
      labeled to read the printed numbers from the AUX.
    """
    # Detect if the source actually contains chapters
    has_chapters = bool(re.search(r'\\chapter\*?\s*\{', clean_src)) or any(e.get("chapter_index") for e in eqs)

    parts = []
    # --- SAME header as standalone equations ---
    parts.append(r'\documentclass[preview,varwidth]{standalone}' + '\n')
    parts.append(r'\usepackage{amsmath,amssymb,amsfonts,mathtools,amsthm}' + '\n')

    # Simulate chapters under 'standalone'
    if has_chapters:
        parts.append(r'\newcounter{chapter}' + '\n')
        # amsmath’s \numberwithin wires equation to chapter
        parts.append(r'\numberwithin{equation}{chapter}' + '\n')

    parts.append(r'\begin{document}' + '\n')

    # Chapter markers: step the simulated chapter counter at the right places
    chapter_positions = [m.start() for m in re.finditer(r'\\chapter\*?\s*\{', clean_src)]
    chap_iter = iter(chapter_positions)
    next_chap_pos = next(chap_iter, None)

    # Interleave chapter steps and equation shells in source order
    eqs_sorted = sorted(eqs, key=lambda e: e['start'])

    for i, e in enumerate(eqs_sorted, start=1):
        # Step chapter counter before this equation if needed
        while has_chapters and next_chap_pos is not None and next_chap_pos < e['start']:
            parts.append(r'\stepcounter{chapter}' + '\n')  # resets equation via \numberwithin
            next_chap_pos = next(chap_iter, None)

        # Only emit shells for numberable (non-starred) environments
        if not e.get("is_starred") and should_label_for_numbering(e["env"]):
            lbl = e["label_name"] if e.get("has_label") else f"{AUTO_LABEL_PREFIX}{i}"
            parts.append(r'\begin{equation}\relax' + '\n')
            parts.append(fr'\label{{{lbl}}}' + '\n')
            parts.append(r'\end{equation}' + '\n')

    # Any remaining chapters after the last equation (harmless, optional)
    while has_chapters and next_chap_pos is not None:
        parts.append(r'\stepcounter{chapter}' + '\n')
        next_chap_pos = next(chap_iter, None)

    parts.append(r'\end{document}' + '\n')
    return ''.join(parts)

def map_equations_to_display_names(clean_src, eq_dicts, debug_dir=None):
    temp_src = build_minimal_numbering_doc(clean_src, eq_dicts)
    label_map = compile_temp_and_parse_aux(temp_src, timeout=TIMER_TIMEOUT, debug_dir=debug_dir)

    if label_map is None:
        return [None] * len(eq_dicts)

    # ... then same logic as before to choose printed names
    results = []
    for i, e in enumerate(eq_dicts):
        if e["has_tag"]:
            results.append(e["tag_text"])
            continue
        if should_label_for_numbering(e["env"]) and e["has_label"] and e["label_name"] in label_map:
            results.append(label_map[e["label_name"]])
            continue
        auto = f"{AUTO_LABEL_PREFIX}{i+1}"
        if auto in label_map:
            results.append(label_map[auto])
            continue
        results.append(None)
        
    if debug_dir:
        os.makedirs(debug_dir, exist_ok=True)
        with open(os.path.join(debug_dir, "numbering_skeleton.tex"), "w", encoding="utf-8") as f:
            f.write(temp_src)
            
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

        debug_dir = os.path.join(out_dir, "_numbering_debug") if DEBUG_MODE else None
        if DEBUG_MODE:
            print(f"[debug] Debug mode ON. Artifacts will be saved in: {debug_dir}")
    
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
        equation_dicts = find_equations(tex_source_clean)
        chapters = extract_chapters(tex_source_clean)

        # split preamble from the original full source (not cleaned)
        m = re.search(r'\\begin{document}', tex_source)
        preamble = tex_source[:m.start()] if m else ""

        # Get printed names by compiling the harvest doc once
        display_names = map_equations_to_display_names(tex_source_clean, equation_dicts, debug_dir)

        # Quick per-eq debug line
        for i, e in enumerate(equation_dicts, 1):
            print(f"[debug] eq#{i}: env={e['env']}, starred={e['is_starred']}, "
                f"has_tag={e['has_tag']}, has_label={e['has_label']}, "
                f"chapter_index={e['chapter_index']}")


        # macros:
        needed_macros = set().union(*(macros_in_equation(e["clean_body"]) for e in equation_dicts)) if equation_dicts else set()
        
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
            
        if DEBUG_MODE:
            try:
                os.makedirs(debug_dir, exist_ok=True)
                with open(os.path.join(debug_dir, "summary.txt"), "w", encoding="utf-8") as sf:
                    sf.write(f"Chapters ({len(chapters)}): {[t for _, t in chapters]}\n")
                    for i, (e, nm) in enumerate(zip(equation_dicts, display_names), 1):
                        sf.write(f"eq#{i}: env={e['env']}, ch={e['chapter_index']}, "
                                f"tag={e['tag_text'] if e['has_tag'] else '-'}, "
                                f"label={e['label_name'] if e['has_label'] else '-'}, "
                                f"printed={nm}\n")
                print(f"[debug] Wrote {os.path.join(debug_dir, 'summary.txt')}")
            except Exception as ex:
                print("[debug] Failed to write summary:", ex)

    
        # ------ PDF → SVG conversion (skip if Inkscape missing) ----------
        inkscape_exe = find_inkscape()
        if not inkscape_exe:
            print("Inkscape not found; set $INKSCAPE or add to PATH. Skipping PDF→SVG.")
        else:
            for pdf in glob.glob(os.path.join(out_dir, "*.pdf")):
                svg = pdf[:-4] + ".svg"
                convert_pdf_to_svg(pdf, svg, inkscape_exe)
