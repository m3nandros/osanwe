import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = PROJECT_ROOT / "tests" / "pdf_regression_corpus.json"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "output" / "pdf_regression_reports"
DEFAULT_WORK_ROOT = PROJECT_ROOT / "output" / "pdf_regression_runs"
DEFAULT_CACHE_DIR = PROJECT_ROOT / "tests" / "translation_cache"


@dataclass
class PdfCaseResult:
    label: str
    input: str
    paper_id: str
    ok: bool
    duration_sec: float
    bundle_dir: Optional[str]
    output_pdf: Optional[str]
    reasons: List[str]
    warnings: List[str]


_TYPST_FAIL_MARKERS = [
    "error:",
    "unclosed delimiter",
    "failed",
]


def _sanitize_id(s: str) -> str:
    t = (s or "").strip()
    if not t:
        return "paper"
    out: List[str] = []
    for ch in t:
        if ch.isalnum() or ch in ("-", "_", "."):
            out.append(ch)
        else:
            out.append("_")
    res = "".join(out).strip("._-")
    return res or "paper"


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


def _run_cli_pdf(
    *,
    input_value: str,
    output_root: Path,
    paper_id: str,
    lang: str,
    extractor: str,
    headless: bool,
    stop_after: str,
    format: str,
    verbose: bool,
    cache_mode: str,
    cache_dir: Optional[Path],
    case_timeout_sec: int,
) -> Tuple[int, str]:
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "cli.py"),
        "pdf",
        "run",
        input_value,
        "-o",
        str(output_root),
        "--paper-id",
        paper_id,
        "--lang",
        lang,
        "--extractor",
        extractor,
        "--format",
        format,
        "--stop-after",
        stop_after,
    ]
    if headless:
        cmd.append("--headless")
    _ = verbose

    timeout = int(case_timeout_sec) if case_timeout_sec and case_timeout_sec > 0 else None

    env = os.environ.copy()
    if cache_mode:
        env["ROSETTA_TRANSLATION_CACHE_MODE"] = str(cache_mode).strip().lower()
    if cache_dir is not None:
        env["ROSETTA_TRANSLATION_CACHE_DIR"] = str(cache_dir)

    if timeout is not None and timeout > 0:
        translate_timeout = max(15, min(90, int(timeout * 0.6)))
        env["ROSETTA_OPENAI_TIMEOUT_SEC"] = str(translate_timeout)
        env["ROSETTA_OPENAI_MAX_RETRIES"] = "1"
        env["ROSETTA_OPENAI_INITIAL_RETRY_DELAY_SEC"] = "1"
        env["ROSETTA_OPENAI_MAX_RETRY_DELAY_SEC"] = "10"
    proc = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        timeout=timeout,
    )
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return int(proc.returncode), combined


def _artifact_paths(output_root: Path, paper_id: str, lang: str) -> Dict[str, Path]:
    paper_dir = output_root / paper_id
    bundle_dir = paper_dir / "bundle"
    return {
        "paper_dir": paper_dir,
        "bundle_dir": bundle_dir,
        "original_pdf": paper_dir / "original.pdf",
        "ru_md": bundle_dir / f"paper.{lang}.md",
        "typ": bundle_dir / f"paper.{lang}.typ",
        "pdf": paper_dir / f"translated_{lang}.pdf",
        "typst_log": bundle_dir / "logs" / "typst_compile.log",
    }


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return path.read_text(encoding="latin-1", errors="replace")


def _tail_text(text: str, max_lines: int = 60) -> str:
    lines = (text or "").splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[-max_lines:])


def _tail_file(path: Path, max_lines: int = 60) -> str:
    try:
        return _tail_text(_read_text(path), max_lines=max_lines)
    except Exception:
        return ""


def _check_typst_log(log_text: str) -> Tuple[List[str], List[str]]:
    reasons: List[str] = []
    warnings: List[str] = []

    if not log_text.strip():
        # Not always written, depending on where render is invoked.
        warnings.append("missing_typst_log")
        return reasons, warnings

    lower = log_text.lower()
    for m in _TYPST_FAIL_MARKERS:
        if m in lower and "returncode: 0" not in lower:
            reasons.append(f"typst_log_marker: {m}")
            break

    if "returncode: 0" not in lower:
        m = re.search(r"returncode:\s*(\d+)", lower)
        if m and m.group(1) != "0":
            reasons.append(f"typst_returncode={m.group(1)}")

    return reasons, warnings


def run_case(
    *,
    item: Dict[str, Any],
    work_root: Path,
    lang: str,
    extractor: str,
    headless: bool,
    stop_after: str,
    format: str,
    verbose: bool,
    cache_mode: str,
    cache_dir: Optional[Path],
    case_timeout_sec: int,
) -> PdfCaseResult:
    label = str(item.get("label", "") or "").strip() or "case"
    input_value = str(item.get("input", "") or "").strip()
    paper_id = _sanitize_id(str(item.get("paper_id", "") or "").strip() or label)

    eff_lang = str(item.get("lang", lang) or lang)
    eff_extractor = str(item.get("extractor", extractor) or extractor)
    eff_stop_after = str(item.get("stop_after", stop_after) or stop_after)
    eff_format = str(item.get("format", format) or format)
    if "headless" in item:
        eff_headless = bool(item.get("headless"))
    else:
        eff_headless = bool(headless)
    eff_timeout = int(item.get("case_timeout", case_timeout_sec) or case_timeout_sec)

    reasons: List[str] = []
    warnings: List[str] = []

    start = time.time()
    rc = 0
    out = ""
    timed_out = False
    try:
        rc, out = _run_cli_pdf(
            input_value=input_value,
            output_root=work_root,
            paper_id=paper_id,
            lang=eff_lang,
            extractor=eff_extractor,
            headless=eff_headless,
            stop_after=eff_stop_after,
            format=eff_format,
            verbose=verbose,
            cache_mode=cache_mode,
            cache_dir=cache_dir,
            case_timeout_sec=eff_timeout,
        )
    except subprocess.TimeoutExpired:
        timed_out = True
        rc, out = 124, f"cli_timeout_sec={int(eff_timeout or 0)}"

    duration = time.time() - start

    paths = _artifact_paths(work_root, paper_id, eff_lang)
    bundle_dir = paths["bundle_dir"]

    if rc != 0:
        reasons.append(f"cli_failed_rc={rc}")

    if timed_out:
        stage = "unknown"
        paper_dir = paths["paper_dir"]
        original_pdf = paths["original_pdf"]
        logs_dir = bundle_dir / "logs"
        extract_log = logs_dir / "extract.log"

        if paths["ru_md"].exists():
            stage = "translate_or_later"
        elif (bundle_dir / "paper.norm.md").exists():
            stage = "normalize_or_later"
        elif (bundle_dir / "paper.raw.md").exists():
            stage = "extract_or_later"
        elif logs_dir.exists() or bundle_dir.exists():
            stage = "extract_in_progress"
        elif original_pdf.exists():
            stage = "downloaded_pdf_or_pre_extract"
        elif paper_dir.exists():
            stage = "creating_paper_dir_or_downloading_pdf"

        warnings.append(f"timeout_stage_hint={stage}")
        warnings.append(f"timeout_fs_hint_paper_dir_exists={paper_dir.exists()}")
        warnings.append(f"timeout_fs_hint_bundle_dir_exists={bundle_dir.exists()}")
        warnings.append(f"timeout_fs_hint_original_pdf_exists={original_pdf.exists()}")
        warnings.append(f"timeout_fs_hint_logs_dir_exists={logs_dir.exists()}")
        warnings.append(f"timeout_fs_hint_extract_log_exists={extract_log.exists()}")
        if original_pdf.exists():
            try:
                warnings.append(f"timeout_fs_hint_original_pdf_size={original_pdf.stat().st_size}")
            except Exception:
                pass

        if extract_log.exists():
            try:
                warnings.append(f"timeout_fs_hint_extract_log_size={extract_log.stat().st_size}")
            except Exception:
                pass

        if extract_log.exists():
            tail = _tail_file(extract_log, max_lines=80)
            if tail.strip():
                warnings.append(f"extract_log_tail:\n{tail}")

    if eff_format == "typst" and eff_stop_after == "render":
        if not paths["pdf"].exists():
            reasons.append("missing_translated_pdf")
        if not paths["typ"].exists():
            reasons.append("missing_typst_file")

        if paths["typst_log"].exists():
            log_text = _read_text(paths["typst_log"])
            r, w = _check_typst_log(log_text)
            reasons.extend(r)
            warnings.extend(w)
        else:
            warnings.append("missing_typst_compile_log")

    if out:
        tail = "\n".join(out.strip().splitlines()[-25:])
        if reasons:
            reasons.append(f"cli_output_tail: {tail}")
        elif verbose:
            warnings.append(f"cli_output_tail: {tail}")

    ok = len(reasons) == 0

    return PdfCaseResult(
        label=label,
        input=input_value,
        paper_id=paper_id,
        ok=ok,
        duration_sec=round(duration, 2),
        bundle_dir=str(bundle_dir) if bundle_dir.exists() else None,
        output_pdf=str(paths["pdf"]) if paths["pdf"].exists() else None,
        reasons=reasons,
        warnings=warnings,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Rosetta PDF regression runner")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--tier", type=str, default="all")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only", type=str, default="")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--lang", type=str, default="ru")
    parser.add_argument("--extractor", type=str, default="auto")
    parser.add_argument("--headless", action="store_true", default=False)
    parser.add_argument("--stop-after", type=str, default="render", choices=("extract", "normalize", "translate", "render"))
    parser.add_argument("--format", type=str, default="typst", choices=("latex", "typst"))
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--case-timeout", type=int, default=0)
    parser.add_argument("--cache-mode", type=str, default="replay")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)

    args = parser.parse_args()

    items = _load_corpus(args.corpus)
    items = _filter_by_tier(items, args.tier)

    if args.only:
        wanted = {s.strip() for s in str(args.only).split(",") if s.strip()}
        items = [it for it in items if str(it.get("label", "")).strip() in wanted]

    if args.start and args.start > 0:
        items = items[args.start :]

    if args.count and args.count > 0:
        items = items[: args.count]
    elif args.limit and args.limit > 0:
        items = items[: args.limit]

    ts = time.strftime("%Y%m%d_%H%M%S")
    work_root = Path(args.work_root) / ts
    work_root.mkdir(parents=True, exist_ok=True)

    report_dir = Path(args.report_dir) / ts
    report_dir.mkdir(parents=True, exist_ok=True)

    print(f"Work root: {work_root}", flush=True)
    print(f"Report dir: {report_dir}", flush=True)

    results: List[PdfCaseResult] = []

    total = len(items)
    for idx, item in enumerate(items, start=1):
        label = str(item.get("label", "") or "").strip() or f"case_{idx:03d}"
        input_value = str(item.get("input", "") or "").strip()
        if not input_value:
            continue

        print(f"[{idx}/{total}] {label} cache_mode={str(args.cache_mode).lower()} format={args.format} stop_after={args.stop_after}", flush=True)

        res = run_case(
            item=item,
            work_root=work_root,
            lang=str(args.lang),
            extractor=str(args.extractor),
            headless=bool(args.headless),
            stop_after=str(args.stop_after),
            format=str(args.format),
            verbose=bool(args.verbose),
            cache_mode=str(args.cache_mode).lower(),
            cache_dir=Path(args.cache_dir) if args.cache_dir else None,
            case_timeout_sec=int(args.case_timeout or 0),
        )
        results.append(res)
        status = "PASS" if res.ok else "FAIL"
        print(f"[{idx}/{total}] {label} -> {status} ({res.duration_sec}s)", flush=True)

        if not res.ok:
            if res.reasons:
                print("Reasons:", flush=True)
                for reason in res.reasons[:20]:
                    print(f"  - {reason}", flush=True)
            if res.warnings:
                print("Warnings:", flush=True)
                for w in res.warnings[:20]:
                    print(f"  - {w}", flush=True)

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "corpus": str(args.corpus),
        "tier": str(args.tier).lower(),
        "work_root": str(work_root),
        "format": str(args.format),
        "stop_after": str(args.stop_after),
        "cache_mode": str(args.cache_mode).lower(),
        "cache_dir": str(args.cache_dir) if args.cache_dir else None,
        "count": len(results),
        "passed": sum(1 for r in results if r.ok),
        "failed": sum(1 for r in results if not r.ok),
        "results": [r.__dict__ for r in results],
    }

    (report_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    md_lines: List[str] = []
    md_lines.append("# Rosetta PDF Regression Report")
    md_lines.append("")
    md_lines.append("## Cases")
    md_lines.append("")
    md_lines.append("| Label | Status | Duration (s) | paper_id | Output PDF |")
    md_lines.append("|---|---:|---:|---|---|")
    for r in results:
        status = "PASS" if r.ok else "FAIL"
        md_lines.append(f"| {r.label} | {status} | {r.duration_sec} | {r.paper_id} | {r.output_pdf or ''} |")
    md_lines.append("")
    md_lines.append(f"Generated at: `{report['generated_at']}`")
    md_lines.append(f"Corpus: `{report['corpus']}`")
    md_lines.append(f"Work root: `{report['work_root']}`")
    md_lines.append(f"Format: `{report['format']}`")
    md_lines.append(f"Stop after: `{report['stop_after']}`")
    md_lines.append(f"Cache mode: `{report['cache_mode']}`")
    md_lines.append("")

    failures = [r for r in results if not r.ok]
    if failures:
        md_lines.append("## Failed cases")
        md_lines.append("")
        for r in failures:
            md_lines.append(f"### {r.label}")
            md_lines.append("")
            md_lines.append(f"- paper_id: `{r.paper_id}`")
            if r.bundle_dir:
                md_lines.append(f"- bundle_dir: `{r.bundle_dir}`")
            if r.output_pdf:
                md_lines.append(f"- output_pdf: `{r.output_pdf}`")
            if r.reasons:
                md_lines.append("- Reasons:")
                for reason in r.reasons[:40]:
                    md_lines.append(f"  - {reason}")
            if r.warnings:
                md_lines.append("- Warnings:")
                for w in r.warnings[:40]:
                    md_lines.append(f"  - {w}")
            md_lines.append("")

    (report_dir / "report.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
