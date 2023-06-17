
# tex2svg

tex2svg is a **command-line** tool for converting **LaTeX equations** to **SVG** format. It allows you to: 
1. extract individual equations from a LaTeX file, 
2. compile them to PDF,
3. and then convert them to SVG using Inkscape.

## Installation

1. Ensure that you have Python 3 installed on your system.

2. Clone the tex2svg repository:
```
git clone https://github.com/mihaozbot/tex2svg.git
```
Install the required dependencies:
```
pip install -r requirements.txt
```
**Make sure Inkscape is installed on your system**. If not, download and install it from the official website: https://inkscape.org/

##  Usage
Run the tex2svg command with the following arguments:
```
python tex2svg.py [TEX_FILE] [OUTPUT_DIR]
```
TEX_FILE (optional): The path to the LaTeX file containing equations. If not provided, tex2svg will search for LaTeX files in the current folder.

OUTPUT_DIR (optional): The output directory where the SVG files will be saved. If not provided, tex2svg will create a folder with the same name as the LaTeX file in the current folder.

The tex2svg program will extract equations from the LaTeX file, compile them to PDF, and then convert them to SVG using Inkscape.

##  Examples
Convert equations from a LaTeX file named "equations.tex" and save the SVG files in an "output" folder:
```
python tex2svg.py equations.tex output
```
Convert equations from all LaTeX files in the current folder and save the SVG files in separate folders:
```
python tex2svg.py
```
## License

This project is licensed under the GNU General Public License (GPL).
