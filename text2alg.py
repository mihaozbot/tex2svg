import re
import os
import glob
import subprocess
import threading
import sys

def find_algorithms(tex_file):
    try:
        with open(tex_file, 'r', encoding='utf-8', errors='ignore') as file:
            tex_content = file.read()
    except (UnicodeDecodeError, IOError) as e:
        print(f"Error reading {tex_file}: {e}")
        return []  # Skip this file and return an empty list

    # Extract algorithm environments
    algorithms = re.findall(r'\\begin{algorithm}(.*?)\\end{algorithm}', tex_content, re.DOTALL)
    algorithms += re.findall(r'\\begin{algorithm\*}(.*?)\\end{algorithm\*}', tex_content, re.DOTALL)

    return algorithms

def has_balanced_braces(s):
    """Check if the string s has balanced braces."""
    stack = []
    for char in s:
        if char == '{':
            stack.append(char)
        elif char == '}':
            if not stack:
                return False  # Unbalanced closing brace
            stack.pop()
    return not stack  # True if stack is empty (all braces are balanced)

def extract_relevant_commands_line_by_line(tex_file):
    relevant_content = ''

    # Patterns to match the commands to extract
    command_patterns = [
        r'\\usepackage(?:\[[^\]]*\])?{[^}]*}',          # \usepackage{...} or \usepackage[...]{...}
        r'\\newcommand\*?(?:\s*\[[^\]]*\])?\s*{[^}]+}.*',    # \newcommand with optional arguments
        r'\\providecommand\*?(?:\s*\[[^\]]*\])?\s*{[^}]+}.*',# \providecommand with optional arguments
        r'\\DeclareMathOperator(?:\s*\*?)?\s*{[^}]+}\s*{[^}]+}',          # \DeclareMathOperator
        r'\\let\s+\\[^=\s]+=\s*\\?[^\s%]+',                               # \let commands
        r'\\def\s+\\[^\s{]+[^%]*',                                        # \def commands
        r'\\newenvironment\*?\s*{[^}]+}[^%]*?\\end{[^}]+}'                # \newenvironment
    ]
    
    combined_pattern = '|'.join(command_patterns)

    try:
        with open(tex_file, 'r', encoding='utf-8', errors='replace') as file:
            for line in file:
                # Remove comments and strip the line
                line = line.split('%', 1)[0].strip()
                if line and re.match(combined_pattern, line):
                    if has_balanced_braces(line):
                        relevant_content += line + '\n'
                    else:
                        print(f"Skipping unbalanced command: {line}")
    except Exception as e:
        print(f"Error reading {tex_file}: {e}")
    
    return relevant_content.strip()

def create_algorithm_file(algorithm, output_dir, algorithm_index, relevant_content):
    algorithm_content = '\\documentclass[preview,varwidth]{standalone}\n'
    if relevant_content:
        algorithm_content += relevant_content + '\n'

    # Add any required packages if not already included
    required_packages = [
        '\\usepackage{amsmath}',
        '\\usepackage{amssymb}',
        '\\usepackage{amsfonts}',
        '\\usepackage{mathtools}',
        '\\usepackage{amsthm}',
        '\\usepackage{algorithm}',
        '\\usepackage[noend]{algpseudocode}'
    ]

    for pkg in required_packages:
        if pkg not in algorithm_content:
            algorithm_content += pkg + '\n'

    algorithm_content += '\\begin{document}\n'
    algorithm_content += '\\begin{algorithm}\n'
    algorithm_content += algorithm.strip() + '\n'
    algorithm_content += '\\end{algorithm}\n'
    algorithm_content += '\\end{document}\n'

    algorithm_file = os.path.abspath(os.path.join(output_dir, f'alg_{algorithm_index}.tex'))
    with open(algorithm_file, 'w', encoding='utf-8') as file:
        file.write(algorithm_content)

    return algorithm_file

def compile_algorithm(algorithm_file):
    algorithm_basename = os.path.splitext(os.path.basename(algorithm_file))[0]
    output_dir = os.path.dirname(algorithm_file)
    pdf_file = os.path.join(output_dir, f'{algorithm_basename}.pdf')

    process = None

    def run_pdflatex():
        nonlocal process
        process = subprocess.Popen(
            ['pdflatex', '-interaction=nonstopmode', '-output-directory', output_dir, algorithm_file],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = process.communicate()

    timeout = 20  # Timeout in seconds
    timer = threading.Timer(timeout, lambda: process.kill() if process is not None else None)
    try:
        timer.start()
        run_pdflatex()
        timer.cancel()
        if os.path.isfile(pdf_file):
            print(f'Algorithm {algorithm_basename} compiled successfully.')
        else:
            print(f'Algorithm {algorithm_basename} failed to compile. PDF file was not created.')
    except Exception as e:
        print(f'Algorithm {algorithm_basename} failed! Error: {e}')
    finally:
        timer.cancel()
        if process is not None:
            process.kill()

    return algorithm_basename

def convert_pdf_to_svg(pdf_file, svg_file, inkscape_path):
    if os.path.exists(svg_file):
        print(f"SVG file {svg_file} already exists. Skipping conversion.")
        return

    try:
        subprocess.run(
            [inkscape_path, '--pdf-poppler', '--export-type=svg', '--export-filename=' + svg_file, pdf_file],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        print(f"Successfully converted {pdf_file} to SVG.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to convert {pdf_file} to SVG. Error: {e}")
    except FileNotFoundError:
        print(f"Inkscape executable not found at path {inkscape_path}. Please provide the correct path.")

if __name__ == "__main__":
    # Get the input arguments from the console
    tex_file = sys.argv[1] if len(sys.argv) > 1 else None
    output_folder = sys.argv[2] if len(sys.argv) > 2 else None

    # Find all .tex files in the current folder if the input file is not provided
    if tex_file is None:
        tex_files = glob.glob('*.tex')
    else:
        tex_files = [tex_file]

    # Iterate over each .tex file
    print(f"Processing input files: {tex_files}")
    for tex_file in tex_files:
        print(f"Processing input file: {tex_file}")

        algorithms = find_algorithms(tex_file)

        # Extract relevant commands line by line
        relevant_content = extract_relevant_commands_line_by_line(tex_file)

        # Add the required \usepackage commands to the relevant_content if not already included
        required_packages = [
            '\\usepackage{amsmath}',
            '\\usepackage{amssymb}',
            '\\usepackage{amsfonts}',
            '\\usepackage{mathtools}',
            '\\usepackage{amsthm}',
            '\\usepackage{algorithm}',
            '\\usepackage[noend]{algpseudocode}'
        ]

        for pkg in required_packages:
            if pkg not in relevant_content:
                relevant_content += '\n' + pkg

        # Create the output directory
        if output_folder is None:
            output_dir = os.path.splitext(tex_file)[0]
        else:
            output_dir = output_folder

        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        print(f"Output directory: {output_dir}")

        # Save each algorithm in a separate .tex file and compile to PDF
        for i, algorithm in enumerate(algorithms):
            algorithm_file = create_algorithm_file(algorithm, output_dir, i, relevant_content)
            algorithm_basename = compile_algorithm(algorithm_file)

            # Set file permissions for the PDF file
            pdf_file = os.path.join(output_dir, f'{algorithm_basename}.pdf')

            if os.path.isfile(pdf_file):
                try:
                    os.chmod(pdf_file, 0o755)  # Set read, write, and execute permissions
                except FileNotFoundError as fnf_error:
                    print(f"File not found: {pdf_file}")
                except Exception as e:
                    print(f"Error changing file permissions for {pdf_file}: {e}")
            else:
                print(f"PDF file {pdf_file} does not exist. Skipping permission change.")

        # Check and set Inkscape path
        try:
            subprocess.run(['inkscape', '--version'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            inkscape_path = 'inkscape'  # Use 'inkscape' command if available in the system path
            print('Inkscape executable is available in the system PATH.')
        except FileNotFoundError:
            inkscape_path = r'C:\Program Files\Inkscape\bin\inkscape.exe'  # Fallback to absolute path if 'inkscape' command is not found
            print(f'The "inkscape" command is not available in the system path. Fallback to absolute path {inkscape_path}.')

        if os.path.exists(inkscape_path) or inkscape_path == 'inkscape':
            print('Inkscape executable found.')
        else:
            print(f'Inkscape executable not found at the specified path {inkscape_path}.')

        try:
            subprocess.run([inkscape_path, '--version'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            print("Inkscape executable is working.")
        except subprocess.CalledProcessError as e:
            print("Failed to run Inkscape. Error:", e)
        except FileNotFoundError:
            print("Inkscape executable not found. Please provide the correct path.")

        # Convert PDFs to SVGs
        for file_name in os.listdir(output_dir):
            if file_name.endswith('.pdf'):
                pdf_file = os.path.join(output_dir, file_name)
                svg_file = os.path.join(output_dir, file_name[:-4] + '.svg')
                convert_pdf_to_svg(pdf_file, svg_file, inkscape_path)
                print(f"Output SVG file: {svg_file}")
