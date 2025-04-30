import re
import os
import glob
import subprocess
import threading
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
COMMENT_RE = re.compile(r'(^|[^\\])%.*$', re.MULTILINE)   # TeX comment lines


# ─────────────────────────────────────────────
#  FIND EQUATIONS (returns (env, body) tuples)
# ─────────────────────────────────────────────
def find_equations(tex_file):
    try:
        with open(tex_file, 'r', encoding='utf-8', errors='ignore') as file:
            tex_content = file.read()
    except (UnicodeDecodeError, IOError) as e:
        print(f"Error reading {tex_file}: {e}")
        return []

    pattern = re.compile(
        r'''
        \\begin\{(?P<env>equation\*?|align\*?|multline\*?|gather\*?|displaymath)\}
        (?P<body>[\s\S]*?)
        \\end\{\1\}
        |
        \\\[(?P<bracket_body>[\s\S]*?)\\]
        ''',
        re.VERBOSE
    )

    equations = []
    for m in pattern.finditer(tex_content):
        if m.group('env'):
            env  = m.group('env')
            body = m.group('body')
        else:
            env  = 'brackets'
            body = m.group('bracket_body')

        # strip comments
        body_clean = re.sub(COMMENT_RE, '', body).strip()
        if not body_clean:
            continue

        # remove trailing punctuation
        body_clean = strip_trailing_punctuation(body_clean)

        equations.append((env, body_clean))

    
    return equations

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

    process = None

    def run_pdflatex():
        nonlocal process
        process = subprocess.Popen(['pdflatex', '-output-directory', output_dir, equation_file],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _, _ = process.communicate()

    timer = threading.Timer(10, lambda: process.kill() if process is not None else None)
    try:
        timer.start()
        run_pdflatex()
        print(f'Equation {equation_basename} compiled successfully.')
    except:
        print(f'Equation {equation_basename} failed!')
    finally:
        timer.cancel()
        if process is not None:
            process.kill()
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


# ─────────────────────────────────────────────
#  MAIN (enumerate start=1; rest unchanged)
# ─────────────────────────────────────────────
if __name__ == "__main__":
    tex_file = sys.argv[1] if len(sys.argv) > 1 else None
    output_folder = sys.argv[2] if len(sys.argv) > 2 else None

    tex_files = glob.glob('*.tex') if tex_file is None else [tex_file]

    print(f"Processing input files: {tex_files}")
    for tex_file in tex_files:
        print(f"Processing input file: {tex_file}")

        equations = find_equations(tex_file)

        print(f"Found {len(equations)} equations:")
        for n, (env, body) in enumerate(equations, start=1):
            print(f"\n--- Eq {n} ({env}) ---\n{body}\n")

        relevant_content = '\n\\usepackage{amsmath,amssymb,amsfonts,mathtools,amsthm}'

        output_dir = os.path.splitext(tex_file)[0] if output_folder is None else output_folder
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        print(f"Output directory: {output_dir}")

        # Save each equation (start=1 for numbering)
        for i, eq in enumerate(equations, start=1):
            equation_file = create_equation_file(eq, output_dir, i, relevant_content)
            equation_basename = compile_equation(equation_file)
            pdf_file = os.path.join(output_dir, f'{equation_basename}.pdf')

        # permissions block unchanged
        try:
            os.chmod(pdf_file, 0o755)
        except FileNotFoundError:
            print(f"File not found: {pdf_file}")
        except Exception as e:
            print(f"Error changing permissions for {pdf_file}: {e}")

        # Inkscape path detection (unchanged)
        try:
            subprocess.run(['inkscape', '--version'], check=True)
            inkscape_path = 'inkscape'
            print('Inkscape executable is available in the system PATH.')
        except FileNotFoundError:
            inkscape_path = r'C:\Program Files\Inkscape\bin\inkscape.exe'
            print(f'The "inkscape" command is not in PATH. Using fallback {inkscape_path}.')

        if os.path.exists(inkscape_path):
            print('Inkscape executable found.')
        else:
            print(f'Inkscape executable not found at the specified path {inkscape_path}.')

        try:
            subprocess.run([inkscape_path, '--version'], check=True)
            print("Inkscape executable is working.")
        except subprocess.CalledProcessError as e:
            print("Failed to run Inkscape. Error:", e)
        except FileNotFoundError:
            print("Inkscape executable not found. Please provide the correct path.")

        # Convert every PDF in the output dir
        for file_name in os.listdir(output_dir):
            if file_name.endswith('.pdf'):
                pdf_file = os.path.join(output_dir, file_name)
                svg_file = os.path.join(output_dir, file_name[:-4] + '.svg')
                convert_pdf_to_svg(pdf_file, svg_file, inkscape_path)
                print(f"Output SVG file: {svg_file}")
