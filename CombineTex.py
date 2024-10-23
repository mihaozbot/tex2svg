import sys
import glob
import os
import re

def find_main_tex_file(tex_files):
    """
    Identifies the main LaTeX file that contains \begin{document}.
    """
    for tex_file in tex_files:
        try:
            with open(tex_file, 'r', encoding='utf-8', errors='replace') as file:
                tex_content = ""
                for line in file:
                    # Strip leading/trailing spaces and check if line starts with %
                    if line.strip().startswith('%'):
                        continue  # Skip lines starting with %
                    tex_content += line  # Append non-comment lines
                # Use a raw string to avoid escape sequence errors with LaTeX commands
                if re.search(r'\\begin{document}', tex_content):  # Raw string for backslashes
                    print(f"Main LaTeX file found: {tex_file}")
                    return tex_file
        except Exception as e:
            print(f"Error reading {tex_file}: {e}")
    return None


def combine_tex_files(main_file, output_file):
    """
    Combines the main LaTeX file and its included files into a single LaTeX file,
    replacing \input and \include lines directly with the contents of the included files.
    """
    def read_file_with_includes(tex_file, already_processed=None):
        if already_processed is None:
            already_processed = set()

        if tex_file in already_processed:
            return ""

        try:
            combined_content = ""
            with open(tex_file, 'r', encoding='utf-8', errors='replace') as file:
                for line in file:
                    if line.strip().startswith('%'):
                        continue  # Skip comment lines

                    # Check if the line contains \input or \include
                    match = re.search(r'\\(?:input|include)\{(.+?)\}', line)
                    if match:
                        # Extract the filename from the \input or \include command
                        include_file = match.group(1).strip()

                        # Ensure the file has a .tex extension if it's not provided
                        if not include_file.endswith('.tex'):
                            include_file += '.tex'

                        if os.path.exists(include_file):
                            print(f"Including file: {include_file}")
                            # Recursively read the included file and add its content
                            included_content = read_file_with_includes(include_file, already_processed)
                            combined_content += included_content  # Insert the content of the included file
                        else:
                            print(f"File {include_file} not found!")
                    else:
                        combined_content += line  # Add the line if it's not \input or \include

            # Mark this file as processed to avoid recursion
            already_processed.add(tex_file)

            return combined_content

        except Exception as e:
            print(f"Error reading {tex_file}: {e}")
            return ""

    # Combine the content from the main file and any included files
    combined_tex_content = read_file_with_includes(main_file)

    # Write the combined content to the output file
    with open(output_file, 'w', encoding='utf-8') as out_file:
        out_file.write(combined_tex_content)

    print(f"Combined LaTeX file created: {output_file}")

# Example usage:
combine_tex_files('main.tex', 'combined_output.tex')

if __name__ == "__main__":
    # Get the input arguments from the console
    tex_file = sys.argv[1] if len(sys.argv) > 1 else None
    output_folder = sys.argv[2] if len(sys.argv) > 2 else None

    # Find all .tex files in the current folder if the input file is not provided
    if tex_file is None:
        tex_files = glob.glob('*.tex')
        print(f"Found {len(tex_files)} .tex files in the current directory.")
    else:
        tex_files = [tex_file]

    # Ensure we found some .tex files
    if not tex_files:
        print("No .tex files found!")
        sys.exit(1)

    # Find the main .tex file that contains \begin{document}
    main_file = find_main_tex_file(tex_files)

    if main_file:
        # Define the output file (you can specify an output file name or folder)
        output_file = os.path.join(output_folder or '.', 'combined_output.tex')

        # Combine the LaTeX files
        combine_tex_files(main_file, output_file)
    else:
        print("No main LaTeX file with \\begin{document} found.")
