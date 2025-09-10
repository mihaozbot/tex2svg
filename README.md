# tex2svg

`tex2svg` is a tool that converts all **LaTeX equations** in a file to standalone **SVG** equation images.

It will:
1) **extract** equations from a `.tex` file,  
2) **compile** each to **PDF** using your LaTeX compiler, and  
3) **convert** PDFs to **SVG** via **Inkscape**.

If your project is split across multiple `.tex` files, use the included **`combine_tex.py`** to flatten them into a single file first.

---

## Installation

Install Inkscape: https://inkscape.org/

---

## Quick Start

Put one or more `.tex` file in the same folder as the program or provide a path to it.

```bash
python tex2svg.py equations.tex output
```

Convert equations from **all** `.tex` files in the current folder:

```bash
python tex2svg.py
```

---

## Usage

```bash
python tex2svg.py [TEX_FILE] [OUTPUT_DIR]
```

**Arguments**
- `TEX_FILE` *(optional)*: Path to a LaTeX file.  
  If omitted, `tex2svg` scans the current directory for `.tex` files.
- `OUTPUT_DIR` *(optional)*: Directory to write SVGs to.  
  If omitted, a folder named after the LaTeX file is created in the current directory.

**What happens**
1. `tex2svg` parses the LaTeX file and extracts equations.  
2. Each equation is compiled to **PDF** using your LaTeX compiler.  
3. Each PDF is converted to **SVG** with **Inkscape**.

---

## Multi-file projects (`\input` / `\include`)

If your main document uses `\input{...}` or `\include{...}`, first flatten it with **`combine_tex.py`**, then run `tex2svg` on the combined output.

### Combine step

```bash
# Auto-detect main .tex (the one with \begin{document}) and write combined_output.tex
python combine_tex.py

# Or specify main file and output directory:
python combine_tex.py main.tex build
# -> writes build/combined_output.tex
```

Then run:

```bash
python tex2svg.py build/combined_output.tex svgs
```

**Notes about the combiner**
- Lines starting with `%` are treated as comments and skipped.  
- Only `\input{...}` and `\include{...}` are inlined.  
- Included paths are resolved relative to the including file.

---

## Configuration

### Inkscape path (hardcoded fallback)
`tex2svg` first tries to run `inkscape` from your **PATH**.  
If it isn’t found, the script uses a **hardcoded fallback path**. You can **manually change** that path in `tex2svg.py`.

Open `tex2svg.py` and look for the section that sets a default/fallback Inkscape path (often near the top of the file).  
Update it to match your system. For example:

```python
# Example only — edit tex2svg.py to match your install if auto-detect fails
DEFAULT_INKSCAPE_PATHS = [
    r"C:\Program Files\Inkscape\bin\inkscape.exe",           # Windows
    "/Applications/Inkscape.app/Contents/MacOS/inkscape",    # macOS
    "/usr/bin/inkscape",
    "/usr/local/bin/inkscape"                                # Linux
]
```

### LaTeX compiler (required)
`tex2svg` **requires** a LaTeX compiler (`pdflatex`, `xelatex`, or `lualatex`).  
Ensure the compiler is installed and on your **PATH** (e.g., `pdflatex --version` should work).  
If the script allows choosing a compiler, set it there; otherwise it uses the default defined in `tex2svg.py`.

---

## Troubleshooting

- **“Inkscape not found”**  
  - Verify `inkscape --version` works in your terminal.  
  - If not, add Inkscape to PATH or update the **hardcoded fallback path** in `tex2svg.py`.

- **“LaTeX compiler not found” / PDF not generated**  
  - Ensure `pdflatex`/`xelatex`/`lualatex` is installed and on PATH.  
  - Install missing LaTeX packages if your document requires them.

- **No SVGs produced**  
  - Check the LaTeX log/output for compile errors.  
  - Confirm equations are actually being detected by the script.

- **Multi-file project not fully inlined**  
  - Run `combine_tex.py` first and then point `tex2svg` at the combined file.

---

## How it works (high level)

1. **Parse & extract**: finds equation environments / math blocks in your `.tex`.  
2. **Compile**: creates minimal temporary docs and compiles each to PDF.  
3. **Convert**: calls Inkscape (CLI) to convert each PDF to SVG.  
4. **Save**: writes SVGs into the selected output directory.

---

## License

This project is licensed under the **GNU General Public License (GPL)**.
