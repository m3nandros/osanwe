# PDF Reconstruction Pipeline (Variant A)

This is an **optional** extension to Rosetta that supports translating papers where the source is **PDF only**.

It is implemented as a separate CLI group: `cli.py pdf ...`.

## Commands (MVP)

## One-shot command (recommended)

Run the full pipeline in one command:

```bash
python3 cli.py pdf run <url_or_pdf> -o out_dir --lang ru
```

Extractor selection:

- `--extractor auto` (default for `run`): use MinerU (`magic-pdf`) if available, otherwise Marker (`marker_single`).
- You can override it explicitly: `--extractor mineru` or `--extractor marker`.

Debugging helper (stop after a stage):

```bash
python3 cli.py pdf run <url_or_pdf> -o out_dir --lang ru --stop-after normalize
```

### 1) Extract PDF -> bundle (Markdown + assets)

```bash
python3 cli.py pdf extract input.pdf -o out_dir
```

You can also pass a URL. Note that many sites (e.g. ResearchGate) serve an HTML landing page instead of a direct PDF.
In that case you must provide a **direct PDF URL** or download the PDF locally first.

The downloader enforces a size limit (currently 50 MB) to avoid accidentally downloading huge non-PDF payloads.

Optional extractor selection:

```bash
python3 cli.py pdf extract input.pdf -o out_dir --extractor mineru
python3 cli.py pdf extract input.pdf -o out_dir --extractor marker
```

Output structure:

- `out_dir/bundle/paper.raw.md`
- `out_dir/bundle/paper.norm.md` (after normalize)
- `out_dir/bundle/paper.<lang>.md` (after translate)
- `out_dir/bundle/assets/*`
- `out_dir/bundle/manifest.json`
- `out_dir/bundle/logs/extract.log`

### 2) Normalize Markdown

```bash
python3 cli.py pdf normalize out_dir/bundle
```

### 3) Translate Markdown -> target language

```bash
python3 cli.py pdf translate out_dir/bundle --lang ru
```

By default the pipeline skips a `# References` section (if present). To disable that:

```bash
python3 cli.py pdf translate out_dir/bundle --lang ru --no-skip-references
```

### 4) Render translated Markdown -> PDF

```bash
python3 cli.py pdf render out_dir/bundle/paper.ru.md -o out_dir/paper_ru.pdf
```

Template can be overridden:

```bash
python3 cli.py pdf render out_dir/bundle/paper.ru.md -o out_dir/paper_ru.pdf --template templates/rosetta.latex
```

## Optional external dependencies

### MinerU (magic-pdf)

Default extractor is `mineru` which calls `magic-pdf`:

```bash
magic-pdf -p input.pdf -o out_dir -m auto
```

If `magic-pdf` is not installed, the pipeline will error with installation hints.

### Marker

Fallback extractor is `marker` which calls `marker_single`. If not installed, the pipeline errors with installation hints.

### Pandoc + XeLaTeX

Rendering requires:

- `pandoc`
- `xelatex` (TeX Live / MacTeX)

If missing, the pipeline errors with installation hints.

## Notes

- The existing arXiv/LaTeX pipeline is **unchanged**.
- All PDF-related tooling is **lazy-loaded**: only imported/executed when running `cli.py pdf ...`.
- For CI/tests, a `mock` extractor is used (no external tools required).
