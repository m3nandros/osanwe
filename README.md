# Osanwe

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![LaTeX](https://img.shields.io/badge/latex-XeLaTeX%20%7C%20pdfLaTeX-green.svg)](https://www.latex-project.org/)
[![Typography](https://img.shields.io/badge/typography-CJK%20%7C%20Cyrillic%20%7C%20Unicode-orange.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests](https://img.shields.io/badge/tests-56%20passed-brightgreen.svg)]()

**Osanwe** is a compiler-grade, fault-tolerant translation engine for scientific arXiv papers. It translates academic LaTeX documents into publication-ready **Japanese, Chinese, Korean, Russian, and European languages** while strictly preserving mathematical notation, complex styling, figures, and internal document semantics.

---

## 📌 Why Osanwe? The Real-World LaTeX Translation Problem

Translating scientific publications is an unsolved problem for standard AI tools. Existing approaches fail systematically:

1. **Commercial PDF Translators (DeepL, Google Translate):** Destroy multi-column layouts, break vector graphics, strip equation semantics, and produce uneditable, visually corrupted PDFs.
2. **Naive LLM Prompts ("Translate this .tex file"):**
   * **Math Corruption:** Hallucinate LaTeX syntax, localize mathematical symbols (e.g., translating variable names in $E = mc^2$), and drop superscript/subscript indices.
   * **AST & Macro Mangling:** Translate TeX control primitives (e.g., changing `\section` to non-Latin commands), drop closing braces `}`, and corrupt table cell alignments (`&` and `\\`).
   * **Compilation Hell:** Standard engines fail when handling non-Latin alphabets without proper font specs, hyphenation patterns, and package coordination.
3. **East Asian & CJK Layout Collapse:**
   * Traditional `pdflatex` cannot process multi-byte Unicode glyphs.
   * East Asian languages (Japanese, Chinese, Korean) lack inter-word whitespace, causing unconfigured TeX engines to overflow text off the page margins.
   * Naive translators phonetically alter author names and citations, breaking academic metadata.

**Osanwe** is built from the ground up as a **resilient compiler pipeline** designed specifically to navigate the failure modes, typography rules, and compilation edge cases of the global academic ecosystem.

---

## ⚡ Key Architectural Differentiators & Failure Recovery

| Challenge / Edge Case | Naive LLM Approach | Osanwe Architecture |
| :--- | :--- | :--- |
| **Math Notation Integrity** | Localizes variables, alters formulas, strips delimiters | **Multi-Pass Regex Masker:** Isolates all inline math (`$...$`, `\(...\)`), display math (`equation`, `align`, `$$...$$`), and TeX commands into immutable tokens (`<<<MATH_INLINE_001>>>`) prior to LLM dispatch. |
| **East Asian (CJK) Typesetting** | Text overflows margins due to lack of word breaks; missing glyph crashes; corrupted author names | **Native XeTeX & `xeCJK` Subsystem:** Dynamically binds `xeCJK`, injects locale-aware line-breaking (`\XeTeXlinebreaklocale "ja"/"zh"/"ko"`), cascades through system CJK fonts (`Hiragino Mincho`, `Noto Serif CJK`, `IPAexMincho`, `FandolSong`), and preserves Latin author/affiliation blocks according to East Asian publishing norms. |
| **Babel vs. TikZ Conflicts (Cyrillic)** | Cyrillic `babel` makes double quotes `"` active, crashing TikZ coordinates | **Runtime Preamble Injection:** Automatically instruments the document preamble with `\AtBeginDocument{\shorthandoff{"}}` alongside `cmap` and `fontenc` bindings. |
| **Fragile Table Environments** | Altered column separators (`&`) cause fatal `align` / `tabular` errors | **Resilient Table-Fallback:** If compilation fails with table structure errors, Osanwe isolates the broken environment and automatically restores original source tables to guarantee PDF emission. |
| **Preamble & Macro Redefinition** | Context windows drop preambles or corrupt custom macro definitions | **Semantic AST-Aware Splitting:** Documents are chunked strictly along logical section and paragraph boundaries. Preambles are isolated and preserved across translation passes. |
| **Network Spikes & Rate Limits** | Fails mid-document on 20-page papers, wasting API quota | **Atomic Disk-Backed Checkpointing:** Translates concurrently with chunk-level checkpoints (`translation_checkpoint.json`). Interrupted jobs resume instantly without re-translating existing chunks. |
| **Academic Typographical Standards** | Straight quotes `""`, hyphen-dashes, orphan prepositions, bilingual LLM echoes | **Linguistic Post-Processor:** Normalizes typography per target locale: CJK bilingual echo suppression (`_drop_bilingual_duplicate_paragraphs_for_cjk`), Russian guillemets (`«...»`), em-dashes (`---`), and non-breaking prepositions. |
| **Silent LLM Omissions** | LLMs occasionally leave abstract paragraphs or headings untranslated | **Safeguard Validation Agent:** Inspects the reconstructed TeX document for remaining English spans and structural drift before invoking the compiler. |
| **Citation & Bibliography Linkage** | Corrupted `\cite` / `\ref` labels break hyperref links | **Deterministic Command Isolation:** All citation keys, cross-references, equations, and labels (`\cite`, `\ref`, `\label`, `\eqref`) are masked and restored byte-for-byte. |

---

## 🌐 Multilingual & East Asian (CJK) Specialization

Osanwe is engineered for international scientific literature, with dedicated optimizations for East Asian, Cyrillic, and European target languages:

* **Japanese (`ja`), Simplified Chinese (`zh`), Korean (`ko`):**
  * **Zero-Margin Overflow:** Automatically injects `\XeTeXlinebreaklocale` and `\XeTeXlinebreakskip=0pt plus 1pt`, resolving the fundamental problem of CJK text layout in TeX.
  * **Hierarchical Font Cascade:** Prioritizes high-legibility OpenType/TrueType academic typefaces with automated fallback:
    * *Japanese:* `Hiragino Mincho ProN` ➔ `Noto Serif CJK JP` ➔ `IPAexMincho` ➔ `FandolSong`
    * *Chinese:* `Noto Serif CJK SC` ➔ `FandolSong-Regular` ➔ `Songti SC`
    * *Korean:* `Noto Serif CJK KR` ➔ `Apple SD Gothic Neo`
  * **Academic Convention Respect:** Automatically identifies and protects author names, institutions, and Latin citations from unwanted phonetic transliteration.
  * **Bilingual Hallucination Filtering:** Detects and strips duplicate bilingual paragraph echoes that frequently occur when LLMs process multi-byte ideographic scripts.
* **Cyrillic (`ru`):** Full integration with `T2A` encodings, `babel`, non-breaking preposition binding, and TikZ quote conflict resolution.
* **European Languages (`de`, `fr`, `es`, `it`):** Native Unicode handling via `fontspec` and standard micro-typographical rules.

---

## 🏗 Pipeline Architecture

```mermaid
flowchart TD
    A["arXiv Tarball / ID"] --> B["arXiv Fetcher & Flattening"]
    B --> C["Multi-Pass Content Masker"]
    C -->|Masked TeX + Token Map| D["AST-Aware Semantic Splitter"]
    D -->|Concurrent Chunks| E["LLM Translation Engine<br/>(OpenAI API + Checkpoints)"]
    E --> F["LaTeX Restorer<br/>(Reverse Token Unmasking)"]
    F --> G["Multilingual Typography Engine<br/>(xeCJK / Babel / Fontspec)"]
    G --> H["Linguistic Post-Processor<br/>(CJK Dupes, Quotes, Prepositions)"]
    H --> I["XeLaTeX / pdfLaTeX Compiler"]
    I -->|Compilation Error| J{"Error Diagnosis"}
    J -->|Table Syntax Error| K["Table Fallback Mechanism"]
    K --> I
    J -->|Log-Driven Surgical Fix| L["Auto-Repair Engine"]
    L --> I
    I -->|Success| M["Publication-Ready PDF"]
```

```
Source LaTeX ──► Deterministic Masker ──► AST Chunking ──► LLM Translation (Parallel)
                                                                   │
Target PDF ◄── XeLaTeX Compiler ◄── Typography Engine ◄──── Token Restorer
                    │
            [Table Fallback & Auto-Repair Loop]
```

### Core Execution Stages

1. **Ingestion & Flattening (`pipeline/arxiv_fetcher.py`):** Downloads complete source tarballs via the arXiv API, unpacks assets, detects the primary document via heuristic scoring, and flattens recursive `\input` and `\include` trees.
2. **Deterministic Masking (`pipeline/masker.py`):** Masks math, verbatim blocks (`minted`, `lstlisting`), TikZ figures, graphics, and cross-references using unique, immutable tokens.
3. **Semantic Chunking (`pipeline/splitter.py`):** Partitions masked content into logical chunks aligned with section headings while respecting model context windows.
4. **Concurrent Translation (`pipeline/translator.py` & `integrations/openai_client.py`):** Dispatches chunks to OpenAI models with exponential backoff retries, target language normalization (`ja`, `zh`, `ko`, `ru`, etc.), full glossary integration, and atomic progress checkpoints.
5. **Token Restoration (`pipeline/assembler.py`):** Restores all masked elements in reverse order and validates token map integrity.
6. **Multilingual Preamble Synthesis (`pipeline/pdf_compiler.py`):** Dynamically injects locale-specific typography engines (`xeCJK` for East Asian scripts with break locales, `T2A`/`babel` with `\shorthandoff{"}` for Cyrillic, or `fontspec` for Unicode European documents).
7. **Linguistic & Structural Post-Processing (`pipeline/latex_postprocessor.py`):** Applies automated locale corrections (CJK bilingual duplicate suppression, quotation marks, non-breaking prepositions).
8. **Multi-Attempt Compilation (`pipeline/pdf_compiler.py`):** Executes XeLaTeX / pdfLaTeX, parses compiler diagnostics from `.log` outputs, manages BibTeX / Biber runs, and triggers surgical auto-repair or table fallbacks if needed.

---

## ⚡ Quick Start

### Prerequisites

* Python 3.9+
* TeX Live with XeLaTeX and multilingual font packages:
  * **macOS:** `brew install --cask mactex-no-gui` (or full MacTeX)
  * **Ubuntu/Debian:** `sudo apt-get install texlive-xetex texlive-lang-cjk texlive-lang-cyrillic texlive-lang-european fonts-noto-cjk`

### Installation

```bash
# Clone repository
git clone https://github.com/m3nandros/osanwe.git
cd osanwe

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Configuration

Copy the example configuration file and provide your OpenAI API key:

```bash
cp .env.example .env
```

Edit `.env`:
```env
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-4o-mini
OPENAI_TEMPERATURE=0.3
```

---

## 📖 Usage

### CLI Interface

Translate an article directly by arXiv ID to any supported language:

```bash
# Translate to Japanese (Academic CJK typography & xeCJK)
python3 cli.py 1706.03762 --lang ja

# Translate to Simplified Chinese
python3 cli.py 1706.03762 --lang zh

# Translate to Korean
python3 cli.py 1706.03762 --lang ko

# Translate to Russian (Cyrillic babel & typography)
python3 cli.py 1706.03762 --lang ru

# Translate to German, French, Spanish, etc.
python3 cli.py 1706.03762 --lang de

# Specify custom output directory and verbose logging
python3 cli.py 1706.03762 --lang ja --output ./output --verbose
```

### Resuming Interrupted Runs

If a translation is interrupted (e.g., network timeout or API rate limit), simply re-run the same command. Osanwe automatically detects the saved checkpoint (`translation_checkpoint.json`) and resumes from the last completed chunk without re-translating existing content.

---

## 🧪 Testing

The repository includes a comprehensive test suite covering AST parsing, regex masking, semantic splitting, token restoration, compiler log parsing, and end-to-end integration:

```bash
# Run complete test suite (56 unit/integration tests)
pytest -v

# Run fast smoke regression test
python3 tests/regression_runner.py --tier smoke
```

---

## 📂 Project Structure

```
osanwe/
├── cli.py                        # Command-line entry point
├── config.py                     # Global configuration and environment settings
├── glossary.json                 # Academic & domain-specific translation glossary
├── pipeline/
│   ├── arxiv_fetcher.py          # arXiv API fetcher and archive unpacker
│   ├── masker.py                 # Deterministic regex token masker
│   ├── splitter.py               # AST-aware semantic chunk splitter
│   ├── assembler.py              # Token restorer and structural integrity checker
│   ├── translator.py             # Pipeline orchestrator with checkpointing & fallback
│   ├── latex_postprocessor.py    # Typographical and linguistic post-processor
│   ├── pdf_compiler.py           # Multi-pass XeLaTeX/pdfLaTeX runner & log analyzer
│   └── validator.py              # Structural and translation quality validation
├── integrations/
│   ├── openai_client.py          # OpenAI API wrapper with token counting & backoff
│   └── notion_client.py          # Optional Notion workspace database export
├── utils/
│   ├── logger.py                 # Structured logging utility
│   └── glossary.py               # Glossary lookup and term matching
├── tests/                        # Unit and integration test suite
└── examples/                     # Sample inputs and translated output references
```

---

## 📄 License

Distributed under the MIT License. See `LICENSE` for details.
