import re
import os
import glob
import subprocess
import threading
import sys

def find_equations(tex_file):
    with open(tex_file, 'r') as file:
        tex_content = file.read()
        equations = re.findall(r'\\begin{equation}(.*?)\\end{equation}', tex_content, re.DOTALL)
        return equations


def create_equation_file(equation, output_dir, equation_index, newcommands):
    equation_content = '\\documentclass[preview,varwidth]{standalone}\n'
    equation_content += '\\usepackage{amsmath,amsfonts}\n'
    equation_content += '\\usepackage[noabbrev]{cleveref}\n'

    for newcommand in newcommands:
        equation_content += newcommand.strip() + '\n'

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
    finally:
        timer.cancel()
        if process is not None:
            process.kill()

    print(f'Equation {equation_basename} compiled successfully.')
    return equation_basename


def convert_pdf_to_svg(pdf_file, svg_file, inkscape_path):
    try:
        subprocess.run([inkscape_path, '--pdf-poppler', '--export-type=svg', '--export-filename=' + svg_file, pdf_file], check=True)
        print(f"Successfully converted {pdf_file} to SVG.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to convert {pdf_file} to SVG. Error: {e}")
    except FileNotFoundError:
        print("Inkscape executable not found. Please provide the correct path.")


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
    for tex_file in tex_files:
        print(f"Processing input file: {tex_file}")

        equations = find_equations(tex_file)

        # Read the original tex file to find newcommand lines
        with open(tex_file, 'r') as file:
            tex_content = file.read()
            newcommands = re.findall(r'\\newcommand.*?\n', tex_content, re.DOTALL)

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
            equation_file = create_equation_file(equation, output_dir, i, newcommands)
            equation_basename = compile_equation(equation_file)

            # Set file permissions for the PDF file
            pdf_file = os.path.join(output_dir, f'{equation_basename}.pdf')
            os.chmod(pdf_file, 0o755)  # Read, write, and execute permissions

        try:
            subprocess.run(['inkscape', '--version'], check=True)
            inkscape_path = 'inkscape'  # Use 'inkscape' command if available in the system path
            print('Inkscape executable is available in the system PATH.')
        except FileNotFoundError:
            inkscape_path = r'C:\Program Files\Inkscape\bin\inkscape.exe'  # Fallback to absolute path if 'inkscape' command is not found
            print('The "inkscape" command is not available in the system path. Fallback to absolute path.')

        if os.path.exists(inkscape_path):
            print('Inkscape executable found.')
        else:
            print('Inkscape executable not found at the specified path.')

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
