import argparse
import json
import re
import subprocess
import sys
import time
import os
import selectors
from collections import deque, Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = PROJECT_ROOT / "tests" / "regression_corpus.json"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "output" / "regression_reports"


@dataclass
class CaseResult:
    arxiv_id: str
    label: str
    ok: bool
    duration_sec: float
    translated_tex: Optional[str]
    translated_log: Optional[str]
    translated_pdf: Optional[str]
    reasons: List[str]
    warnings: List[str]


_LOG_ERROR_PATTERNS: List[Tuple[str, str]] = [
    ("missing_file", r"LaTeX Error: File `[^']+' not found"),
    ("missing_package", r"! LaTeX Error: File `[^']+\.sty' not found"),
    ("bibtex_error", r"I couldn't open database file"),
    ("bibtex_error", r"BibTeX.*error"),
    ("runaway_argument", r"Runaway argument\?"),
    ("emergency_stop", r"! Emergency stop\.?"),
    ("undefined_control_sequence", r"Undefined control sequence\.?"),
]


_UNRESOLVED_MARKERS = [
    "There were undefined references",
    "Rerun to get cross-references right",
]


_BIBER_MARKERS = [
    "Please (re)run Biber",
    "Run Biber",
]


def _run_cli(
    arxiv_id: str,
    verbose: bool,
    cache_mode: str,
    cache_dir: Optional[Path],
    case_timeout_sec: int,
    mode: str,
) -> Tuple[int, str]:
    cmd = [sys.executable, str(PROJECT_ROOT / "cli.py"), arxiv_id]
    if verbose:
        cmd.append("--verbose")

    env = os.environ.copy()
    if cache_mode:
        env["ROSETTA_TRANSLATION_CACHE_MODE"] = cache_mode
    if cache_dir is not None:
        env["ROSETTA_TRANSLATION_CACHE_DIR"] = str(cache_dir)

    # Quality-first: give pdflatex enough time to finish cleanly.
    # Users can override by exporting ROSETTA_PDFLATEX_TIMEOUT_SEC explicitly.
    env.setdefault("ROSETTA_PDFLATEX_TIMEOUT_SEC", "600")

    m = str(mode or "resilient").strip().lower()
    if m not in ("resilient", "strict"):
        m = "resilient"
    if m == "strict":
        env["ROSETTA_TRANSLATION_RESUME"] = "0"
        env["ROSETTA_TRANSLATION_CHUNK_RETRIES"] = "1"
    else:
        env["ROSETTA_TRANSLATION_RESUME"] = "1"
        env["ROSETTA_TRANSLATION_CHUNK_RETRIES"] = "3"

    timeout = int(case_timeout_sec) if case_timeout_sec and case_timeout_sec > 0 else None

    if verbose:
        p = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        assert p.stdout is not None

        tail = deque(maxlen=300)
        start_t = time.time()

        sel = selectors.DefaultSelector()
        try:
            sel.register(p.stdout, selectors.EVENT_READ)

            while True:
                if timeout is not None and (time.time() - start_t) > timeout:
                    try:
                        p.kill()
                    except Exception:
                        pass
                    tail.append(f"cli_timeout_sec={timeout}")
                    return 124, "\n".join(tail)

                rc = p.poll()
                if rc is not None:
                    # Drain any remaining buffered output
                    while True:
                        events = sel.select(timeout=0)
                        if not events:
                            break
                        line = p.stdout.readline()
                        if not line:
                            break
                        sys.stdout.write(line)
                        sys.stdout.flush()
                        tail.append(line.rstrip("\n"))
                    return int(rc), "\n".join(tail)

                events = sel.select(timeout=0.5)
                if not events:
                    continue
                line = p.stdout.readline()
                if not line:
                    continue
                sys.stdout.write(line)
                sys.stdout.flush()
                tail.append(line.rstrip("\n"))
        finally:
            try:
                sel.close()
            except Exception:
                pass

    proc = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
    )
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return proc.returncode, combined


def _artifact_paths(arxiv_id: str) -> Dict[str, Path]:
    base = PROJECT_ROOT / "temp" / arxiv_id / "source"
    return {
        "work_dir": base,
        "tex": base / "translated.tex",
        "log": base / "translated.log",
        "pdf": base / "translated.pdf",
    }


def _cleanup_case_artifacts(paths: Dict[str, Path]):
    # Remove canonical artifacts
    for k in ("tex", "log", "pdf"):
        p = paths.get(k)
        if p is None:
            continue
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass

    # Critical: remove resume checkpoint so that each regression run actually
    # re-executes compilation + surgical fixes (otherwise stale artifacts persist).
    work_dir = paths.get("work_dir")
    if isinstance(work_dir, Path):
        try:
            ckpt = work_dir / "translation_checkpoint.json"
            if ckpt.exists():
                ckpt.unlink()
        except Exception:
            pass

        # Remove previous fixed attempts and common LaTeX aux artifacts for translated.*
        patterns = [
            "translated_fixed_*.*",
            "translated.*",
            "translated_*.*",
        ]
        exts = {
            ".aux",
            ".out",
            ".toc",
            ".lof",
            ".lot",
            ".bbl",
            ".blg",
            ".bcf",
            ".run.xml",
            ".synctex.gz",
        }
        try:
            for pat in patterns:
                for p in work_dir.glob(pat):
                    if p.name in ("translated.tex", "translated.log", "translated.pdf"):
                        continue
                    # Keep unrelated template sources; only clean translated-derived artifacts.
                    if p.is_file() and (p.suffix in exts or p.name.startswith("translated_fixed_")):
                        try:
                            p.unlink()
                        except Exception:
                            pass
        except Exception:
            pass


def _read_text(path: Path) -> str:
    return path.read_text(encoding="latin-1", errors="replace")


def _check_log_for_failures(log_text: str) -> List[str]:
    reasons: List[str] = []

    # Critical LaTeX errors (always fail)
    m_file = re.search(r"! LaTeX Error: File `([^']+)' not found", log_text)
    if m_file:
        fname = m_file.group(1).strip()
        if fname.lower().endswith(".sty"):
            reasons.append(f"missing_package: {fname}")
        elif fname.lower().endswith(".cls"):
            reasons.append(f"missing_class: {fname}")
        else:
            reasons.append(f"missing_file: {fname}")
    elif "! LaTeX Error:" in log_text:
        m = re.search(r"! LaTeX Error:.*", log_text)
        if m:
            reasons.append(f"latex_error: {m.group(0).strip()}")
        else:
            reasons.append("latex_error")

    for category, pattern in _LOG_ERROR_PATTERNS:
        if re.search(pattern, log_text):
            reasons.append(f"log_category: {category}")

    return reasons


def _check_log_for_warnings(log_text: str) -> List[str]:
    warnings: List[str] = []
    if not log_text:
        return warnings

    if "Compilation timed out" in log_text:
        warnings.append("pdflatex_timeout")

    # Non-fatal markers that often appear even when the output is acceptable.
    if "Citation" in log_text and "undefined" in log_text:
        warnings.append("undefined_citations")
    if "Reference" in log_text and "undefined" in log_text:
        warnings.append("undefined_references")
    for marker in _UNRESOLVED_MARKERS:
        if marker in log_text:
            warnings.append(f"rerun_marker: {marker}")

    if "biblatex" in log_text.lower() and any(m.lower() in log_text.lower() for m in _BIBER_MARKERS):
        warnings.append("biblatex_biber_marker")

    if re.search(r"No file [^\s]+\.bbl\.?", log_text):
        warnings.append("missing_bbl")

    overfull = len(re.findall(r"Overfull \\hbox", log_text))
    underfull = len(re.findall(r"Underfull \\hbox", log_text))
    if overfull:
        warnings.append(f"overfull_hbox={overfull}")
    if underfull:
        warnings.append(f"underfull_hbox={underfull}")
    return warnings


def _check_tex_for_issues(tex_text: str) -> List[str]:
    reasons: List[str] = []

    def _strip_tex_comments(s: str) -> str:
        out_lines: List[str] = []
        for line in (s or "").splitlines():
            # TeX ignores everything after an unescaped %
            head = re.split(r"(?<!\\)%", line, maxsplit=1)[0]
            out_lines.append(head)
        return "\n".join(out_lines)

    def _remove_command_brace_arg(s: str, cmd_prefix: str) -> str:
        # Removes occurrences like \sidenoteJose{...} including nested braces in the argument.
        if not s:
            return s
        out: List[str] = []
        i = 0
        n = len(s)
        while i < n:
            if s[i] != "\\":
                out.append(s[i])
                i += 1
                continue
            j = i + 1
            while j < n and s[j].isalpha():
                j += 1
            name = s[i + 1 : j]
            if not name.startswith(cmd_prefix):
                out.append(s[i])
                i += 1
                continue
            k = j
            while k < n and s[k].isspace():
                k += 1
            if k >= n or s[k] != "{":
                # Keep the command as-is if no brace arg follows.
                out.append(s[i])
                i += 1
                continue
            depth = 0
            while k < n:
                ch = s[k]
                if ch == "\\" and k + 1 < n:
                    k += 2
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        k += 1
                        break
                k += 1
            i = k
        return "".join(out)

    if re.search(r"<<[A-Z_]+_\d+>>", tex_text):
        reasons.append("unrestored_tokens")

    tex_no_comments = _strip_tex_comments(tex_text)
    tex_no_sidenotes = _remove_command_brace_arg(tex_no_comments, "sidenote")
    if "??" in tex_no_sidenotes:
        reasons.append("double_question_marks_in_tex")

    bad_headings = []
    for m in re.finditer(
        r"\\(?:part|chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?\{([^}]*)\}",
        tex_text,
    ):
        title = m.group(1)
        t = re.sub(r"\s+", " ", title).strip().lower()
        if not t:
            continue
        if re.fullmatch(r"[a-z0-9 .,:;\-]+", t) and not re.search(r"[а-яА-ЯёЁ]", t):
            if re.search(r"[a-zA-Z]", t):
                bad_headings.append(title)

    if bad_headings:
        reasons.append(f"untranslated_headings: {bad_headings[:3]}")

    return reasons


def _summarize_paths(paths: Dict[str, Path]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    tex = str(paths["tex"]) if paths["tex"].exists() else None
    log = str(paths["log"]) if paths["log"].exists() else None
    pdf = str(paths["pdf"]) if paths["pdf"].exists() else None
    return tex, log, pdf


def run_case(arxiv_id: str, label: str, verbose: bool) -> CaseResult:
    start = time.time()
    rc, out = _run_cli(arxiv_id, verbose=verbose, cache_mode="off", cache_dir=None, case_timeout_sec=0, mode="resilient")
    duration = time.time() - start

    paths = _artifact_paths(arxiv_id)
    reasons: List[str] = []
    warnings: List[str] = []

    if rc != 0:
        reasons.append(f"cli_failed_rc={rc}")

    if paths["log"].exists():
        log_text = _read_text(paths["log"])
        log_reasons = _check_log_for_failures(log_text)
        warnings.extend(_check_log_for_warnings(log_text))

        pdf_exists = paths["pdf"].exists()
        fatal_in_log = (
            ("Fatal error occurred" in log_text)
            or ("no output PDF file produced" in log_text)
            or ("job aborted" in log_text)
            or ("no legal \\end found" in log_text)
            or bool(re.search(r"! Emergency stop\\.?", log_text))
        )

        if pdf_exists and (not fatal_in_log):
            kept: List[str] = []
            demoted: List[str] = []
            for r in log_reasons:
                if r.startswith("missing_package:") or r.startswith("missing_class:"):
                    kept.append(r)
                elif r.startswith("missing_file:") or r.startswith("latex_error:") or r.startswith("log_category:"):
                    demoted.append(r)
                else:
                    kept.append(r)
            reasons.extend(kept)
            warnings.extend([f"nonfatal_{r}" for r in demoted])
        else:
            reasons.extend(log_reasons)
    else:
        reasons.append("missing_translated_log")

    if paths["tex"].exists():
        tex_text = _read_text(paths["tex"])
        reasons.extend(_check_tex_for_issues(tex_text))
    else:
        reasons.append("missing_translated_tex")

    if not paths["pdf"].exists():
        reasons.append("missing_translated_pdf")

    ok = len(reasons) == 0
    tex_p, log_p, pdf_p = _summarize_paths(paths)

    if not ok and out:
        tail = "\n".join(out.strip().splitlines()[-20:])
        reasons.append(f"cli_output_tail: {tail}")

    return CaseResult(
        arxiv_id=arxiv_id,
        label=label,
        ok=ok,
        duration_sec=round(duration, 2),
        translated_tex=tex_p,
        translated_log=log_p,
        translated_pdf=pdf_p,
        reasons=reasons,
        warnings=warnings,
    )


def _load_corpus(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items", [])
    if not isinstance(items, list):
        raise ValueError("Invalid corpus format: items must be a list")
    return items


def _filter_by_tier(items: List[Dict[str, Any]], tier: str) -> List[Dict[str, Any]]:
    t = (tier or "all").strip().lower()
    if t in ("all", "*"):
        return items
    if t not in ("smoke", "extended"):
        raise ValueError(f"Unsupported tier: {tier}")
    out: List[Dict[str, Any]] = []
    for it in items:
        it_tier = str(it.get("tier", "extended") or "extended").strip().lower()
        if it_tier == t:
            out.append(it)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Rosetta regression runner")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--tier", type=str, default="all")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--only", type=str, default="")
    parser.add_argument("--case-timeout", type=int, default=0)
    parser.add_argument("--mode", type=str, default="resilient")
    parser.add_argument("--cache-mode", type=str, default="off")
    parser.add_argument("--cache-dir", type=Path, default=None)

    args = parser.parse_args()

    items = _load_corpus(args.corpus)
    items = _filter_by_tier(items, args.tier)

    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        items = [it for it in items if str(it.get("arxiv_id", "")).strip() in wanted]

    if args.start and args.start > 0:
        items = items[args.start :]

    if args.count and args.count > 0:
        items = items[: args.count]
    elif args.limit and args.limit > 0:
        items = items[: args.limit]

    args.report_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.report_dir / "report.json"

    results: List[CaseResult] = []
    total = len(items)
    for idx, item in enumerate(items, start=1):
        arxiv_id = str(item.get("arxiv_id", "")).strip()
        label = str(item.get("label", "")).strip() or arxiv_id
        if not arxiv_id:
            continue
        paths = _artifact_paths(arxiv_id)
        _cleanup_case_artifacts(paths)
        print(
            f"[{idx}/{total}] {arxiv_id} ({label}) cache_mode={str(args.cache_mode).lower()} mode={str(args.mode).lower()}",
            flush=True,
        )
        start = time.time()
        try:
            rc, out = _run_cli(
                arxiv_id,
                verbose=args.verbose,
                cache_mode=str(args.cache_mode).lower(),
                cache_dir=args.cache_dir,
                case_timeout_sec=int(args.case_timeout or 0),
                mode=str(args.mode).lower(),
            )
        except subprocess.TimeoutExpired:
            rc, out = 124, f"cli_timeout_sec={int(args.case_timeout or 0)}"
        duration = time.time() - start
        reasons: List[str] = []
        warnings: List[str] = []

        if rc != 0:
            reasons.append(f"cli_failed_rc={rc}")

        if paths["log"].exists():
            log_text = _read_text(paths["log"])
            log_reasons = _check_log_for_failures(log_text)
            warnings.extend(_check_log_for_warnings(log_text))

            pdf_exists = paths["pdf"].exists()
            fatal_in_log = (
                ("Fatal error occurred" in log_text)
                or ("no output PDF file produced" in log_text)
                or ("job aborted" in log_text)
                or ("no legal \\end found" in log_text)
                or bool(re.search(r"! Emergency stop\\.?", log_text))
            )

            if (str(args.mode).strip().lower() != "strict") and pdf_exists and (not fatal_in_log):
                kept: List[str] = []
                demoted: List[str] = []
                for r in log_reasons:
                    if r.startswith("missing_package:") or r.startswith("missing_class:"):
                        kept.append(r)
                    elif r.startswith("missing_file:") or r.startswith("latex_error:") or r.startswith("log_category:"):
                        demoted.append(r)
                    else:
                        kept.append(r)
                reasons.extend(kept)
                warnings.extend([f"nonfatal_{r}" for r in demoted])
            else:
                reasons.extend(log_reasons)
        else:
            reasons.append("missing_translated_log")

        if paths["tex"].exists():
            tex_text = _read_text(paths["tex"])
            reasons.extend(_check_tex_for_issues(tex_text))
        else:
            reasons.append("missing_translated_tex")

        if not paths["pdf"].exists():
            reasons.append("missing_translated_pdf")

        ok = len(reasons) == 0
        tex_p, log_p, pdf_p = _summarize_paths(paths)

        if not ok and out:
            tail = "\n".join(out.strip().splitlines()[-20:])
            reasons.append(f"cli_output_tail: {tail}")

        res = CaseResult(
            arxiv_id=arxiv_id,
            label=label,
            ok=ok,
            duration_sec=round(duration, 2),
            translated_tex=tex_p,
            translated_log=log_p,
            translated_pdf=pdf_p,
            reasons=reasons,
            warnings=warnings,
        )
        results.append(res)
        status = "PASS" if ok else "FAIL"
        print(f"[{idx}/{total}] {arxiv_id} -> {status} ({round(duration, 2)}s)", flush=True)

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "corpus": str(args.corpus),
        "tier": str(args.tier).lower(),
        "mode": str(args.mode).lower(),
        "cache_mode": str(args.cache_mode).lower(),
        "cache_dir": str(args.cache_dir) if args.cache_dir is not None else None,
        "count": len(results),
        "passed": sum(1 for r in results if r.ok),
        "failed": sum(1 for r in results if not r.ok),
        "results": [r.__dict__ for r in results],
    }

    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = args.report_dir / "report.md"
    failures = [r for r in results if not r.ok]

    warning_counts: Counter[str] = Counter()
    for r in results:
        for w in getattr(r, "warnings", []) or []:
            warning_counts[w] += 1

    reason_counts: Counter[str] = Counter()
    for r in results:
        for reason in r.reasons:
            if reason.startswith("cli_output_tail:"):
                continue
            reason_counts[reason] += 1

    md_lines: List[str] = []
    md_lines.append(f"# Rosetta Regression Report")
    md_lines.append("")

    md_lines.append("## Cases")
    md_lines.append("")
    md_lines.append("| arXiv ID | Label | Status | Duration (s) | Warnings |")
    md_lines.append("|---|---|---:|---:|---|")
    for r in results:
        status = "PASS" if r.ok else "FAIL"
        w = ", ".join((getattr(r, "warnings", []) or [])[:3])
        md_lines.append(f"| {r.arxiv_id} | {r.label} | {status} | {r.duration_sec} | {w} |")
    md_lines.append("")
    md_lines.append(f"Generated at: `{report['generated_at']}`")
    md_lines.append(f"Corpus: `{report['corpus']}`")
    md_lines.append(f"Cache mode: `{report['cache_mode']}`")
    md_lines.append(f"Cache dir: `{report['cache_dir']}`")
    md_lines.append("")
    md_lines.append(f"## Summary")
    md_lines.append("")
    md_lines.append(f"- Total: **{report['count']}**")
    md_lines.append(f"- Passed: **{report['passed']}**")
    md_lines.append(f"- Failed: **{report['failed']}**")
    md_lines.append("")

    if reason_counts:
        md_lines.append("## Failure reasons (counts)")
        md_lines.append("")
        for reason, cnt in reason_counts.most_common():
            md_lines.append(f"- **{reason}**: {cnt}")
        md_lines.append("")

    if warning_counts:
        md_lines.append("## Warnings (counts)")
        md_lines.append("")
        for w, cnt in warning_counts.most_common():
            md_lines.append(f"- **{w}**: {cnt}")
        md_lines.append("")

    if failures:
        md_lines.append("## Failed cases")
        md_lines.append("")
        for r in failures:
            md_lines.append(f"### {r.arxiv_id} ({r.label})")
            md_lines.append("")
            md_lines.append(f"- Duration: `{r.duration_sec}s`")
            if r.translated_tex:
                md_lines.append(f"- translated.tex: `{r.translated_tex}`")
            if r.translated_log:
                md_lines.append(f"- translated.log: `{r.translated_log}`")
            if r.translated_pdf:
                md_lines.append(f"- translated.pdf: `{r.translated_pdf}`")
            if r.reasons:
                md_lines.append("- Reasons:")
                for reason in r.reasons[:25]:
                    md_lines.append(f"  - {reason}")
            if getattr(r, "warnings", None):
                md_lines.append("- Warnings:")
                for w in (r.warnings or [])[:25]:
                    md_lines.append(f"  - {w}")
            md_lines.append("")

    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    failed = [r for r in results if not r.ok]
    if failed:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
