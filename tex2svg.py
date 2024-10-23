import re
import os
import glob
import subprocess
import threading
import sys

def find_equations(tex_file):
    try:
        with open(tex_file, 'r', encoding='utf-8', errors='ignore') as file:  # Use 'ignore' to skip invalid characters
            tex_content = file.read()
    except (UnicodeDecodeError, IOError) as e:
        print(f"Error reading {tex_file}: {e}")
        return []  # Skip this file and return an empty list

    # Extract equations
    equations = re.findall(r'\\begin{equation}(.*?)\\end{equation}', tex_content, re.DOTALL)
    equations += re.findall(r'\\\[([\s\S]*?)\\\]', tex_content, re.DOTALL)
    equations += re.findall(r'\\begin{displaymath}(.*?)\\end{displaymath}', tex_content, re.DOTALL)
    equations += re.findall(r'\\begin{align}(.*?)\\end{align}', tex_content, re.DOTALL)
    equations += re.findall(r'\\begin{multline}(.*?)\\begin{multline}', tex_content, re.DOTALL)

    return equations

def has_balanced_brackets(command):
    return command.count('{') == command.count('}')

def extract_command_with_content(command_start, preamble):
    """Extracts the full command definition starting from command_start."""
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
    else:  # if the loop completes without finding a closing bracket
        return None

    return preamble[start_index:end_index + 1]

def extract_relevant_commands(preamble):
    relevant_content = ''
    
    # Extracting non-commented \usepackage commands
    usepackage_matches = [match for match in re.findall(r'(?<!^%)\\usepackage.*?\n', preamble, re.MULTILINE) if not match.strip().startswith('%')]
    relevant_content += '\n'.join(usepackage_matches) + '\n'
    
    # Extracting new/renew/provide command definitions including \newcommand* variant
    command_definitions_patterns = [
        r'\\newcommand\*?',
        r'\\renewcommand\*?',
        r'\\providecommand\*?',
        r'\\let'
    ]

    for pattern in command_definitions_patterns:
        matches = [m.start() for m in re.finditer(pattern, preamble)]
        for match_start in matches:
            full_command = extract_command_with_content(match_start, preamble)
            if full_command.strip() != '\\':  # Ensure we aren't just adding a standalone backslash
                relevant_content += full_command + '\n'
    
    for pattern in command_definitions_patterns:
        matches = [m.start() for m in re.finditer(pattern, preamble)]
        for match_start in matches:
            full_command = extract_command_with_content(match_start, preamble)
            if full_command.strip() != '\\':  # Ensure we aren't just adding a standalone backslash
                relevant_content += full_command + '\n'
    
    # Extracting DeclareMathOperator commands
    math_operator_definitions = [match for match in re.findall(r'(?<!^%)\\DeclareMathOperator.*?\n', preamble, re.MULTILINE) if not match.strip().startswith('%')]
    relevant_content += '\n'.join(math_operator_definitions) + '\n'
    
    return relevant_content.strip()  # Using strip() to remove any leading or trailing newlines


def create_equation_file(equation, output_dir, equation_index, relevant_content):
    equation_content = '\\documentclass[preview,varwidth]{standalone}\n'
    if relevant_content:
        equation_content += relevant_content + '\n'

    equation_content += '\\begin{document}\n'

    if '\\begin{' not in equation:
        equation_content += '\\(\n'  # Replace \begin{equation} with \( ...
        equation_content += equation.strip() + '\n'
        equation_content += '\\notag\n'
        equation_content += '\\)\n'  # Replace \end{equation} with \) ...
    else:
        equation_content += '\\begin{equation}\n'
        equation_content += equation.strip() + '\n'
        equation_content += '\\notag\n'
        equation_content += '\\end{equation}\n'

    equation_content += '\\end{document}\n'

    equation_file = os.path.abspath(os.path.join(output_dir, f'{equation_index}.tex'))
    with open(equation_file, 'w') as file:
        file.write(equation_content)

    return equation_file


def compile_equation(equation_file):
    equation_basename = os.path.splitext(os.path.basename(equation_file))[0]
    output_dir = os.path.dirname(equation_file)
    pdf_file = os.path.join(output_dir, f'{equation_basename}.pdf')

    if os.path.isfile(pdf_file):
        print(f'Skipping compilation for equation {equation_basename}.pdf. PDF file already exists.')
        return equation_basename

    process = None

    def run_pdflatex():
        nonlocal process
        process = subprocess.Popen(['pdflatex', '-output-directory', output_dir, equation_file], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _, _ = process.communicate()

    timeout = 10  # Timeout in seconds
    timer = threading.Timer(timeout, lambda: process.kill() if process is not None else None)
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
        subprocess.run([inkscape_path, '--pdf-poppler', '--export-type=svg', '--export-filename=' + svg_file, pdf_file], check=True)
        print(f"Successfully converted {pdf_file} to SVG.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to convert {pdf_file} to SVG. Error: {e}")
    except FileNotFoundError:
        print(f"Inkscape executable not found at path {inkscape_path}. Please provide the correct path .")


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

        equations = find_equations(tex_file)

        # Read the original tex file to find newcommand lines

        if 0:
            try:
                with open(tex_file, 'r', encoding='utf-8', errors='replace') as file:
                    tex_content = file.read()

                # Extract the preamble (everything before \begin{document})
                preamble_match = re.search(r'^(.*?)\\begin{document}', tex_content, re.DOTALL | re.MULTILINE)
                if preamble_match:
                    preamble = preamble_match.group(1)  # Get everything before \begin{document}
                    relevant_content = extract_relevant_commands(preamble)
                else:
                    print(f"Preamble not found in {tex_file}")
                    relevant_content = None
            except Exception as e:
                print(f"Error reading {tex_file}: {e}")
                newcommands, relevant_content = [], None  # Default in case of error

        # Add the required \usepackage command to the relevant_content
        relevant_content = '\n\\usepackage{amsmath,amssymb,amsfonts,mathtools,amsthm}'


        # Create the output directory
        if output_folder is None:
            output_dir = os.path.splitext(tex_file)[0]
        else:
            output_dir = output_folder

        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        print(f"Output directory: {output_dir}")

        # Save each equation in a separate .tex file and compile to PDF
        for i, equation in enumerate(equations):
            equation_file = create_equation_file(equation, output_dir, i, relevant_content)
            equation_basename = compile_equation(equation_file)

            # Set file permissions for the PDF file
            pdf_file = os.path.join(output_dir, f'{equation_basename}.pdf')

        try:
            os.chmod(pdf_file, 0o755)  # Set read, write, and execute permissions
        except FileNotFoundError as fnf_error:
            print(f"File not found: {pdf_file}")
        except Exception as e:
            print(f"Error changing file permissions for {pdf_file}: {e}")

        try:
            subprocess.run(['inkscape', '--version'], check=True)
            inkscape_path = 'inkscape'  # Use 'inkscape' command if available in the system path
            print('Inkscape executable is available in the system PATH.')
        except FileNotFoundError:
            inkscape_path = r'C:\Program Files\Inkscape\bin\inkscape.exe'  # Fallback to absolute path if 'inkscape' command is not found
            print(f'The "inkscape" command is not available in the system path. Fallback to absolute path {inkscape_path}.')

        if os.path.exists(inkscape_path):
            print('Inkscape executable found.')
        else:
            print('Inkscape executable not found at the specified path {inkscape_path}.')

        try:
            subprocess.run([inkscape_path, '--version'], check=True)
            print("Inkscape executable is working.")
        except subprocess.CalledProcessError as e:
            print("Failed to run Inkscape. Error:", e)
        except FileNotFoundError:
            print("Inkscape executable not found. Please provide the correct path.")

        for file_name in os.listdir(output_dir):
            if file_name.endswith('.pdf'):
                pdf_file = os.path.join(output_dir, file_name)
                svg_file = os.path.join(output_dir, file_name[:-4] + '.svg')
                convert_pdf_to_svg(pdf_file, svg_file, inkscape_path)
                print(f"Output SVG file: {svg_file}")
