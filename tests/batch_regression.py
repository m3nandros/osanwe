import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = PROJECT_ROOT / "tests" / "regression_corpus.json"
DEFAULT_REPORT_ROOT = PROJECT_ROOT / "output" / "regression_reports_batches"


@dataclass
class BatchResult:
    batch_index: int
    start: int
    count: int
    report_dir: str
    exit_code: int
    passed: int
    failed: int


def _run_regression_runner(
    corpus: Path,
    tier: str,
    cache_mode: str,
    cache_dir: Optional[Path],
    start: int,
    count: int,
    verbose: bool,
    report_dir: Path,
    case_timeout_sec: int,
    mode: str,
) -> int:
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "tests" / "regression_runner.py"),
        "--corpus",
        str(corpus),
        "--tier",
        tier,
        "--mode",
        str(mode),
        "--cache-mode",
        cache_mode,
        "--start",
        str(start),
        "--count",
        str(count),
        "--case-timeout",
        str(int(case_timeout_sec or 0)),
        "--report-dir",
        str(report_dir),
    ]
    if cache_dir is not None:
        cmd.extend(["--cache-dir", str(cache_dir)])
    if verbose:
        cmd.append("--verbose")

    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    return int(proc.returncode)


def _read_report_summary(report_dir: Path) -> Tuple[int, int]:
    report_path = report_dir / "report.json"
    if not report_path.exists():
        return 0, 0
    payload: Dict[str, Any] = json.loads(report_path.read_text(encoding="utf-8"))
    return int(payload.get("passed", 0) or 0), int(payload.get("failed", 0) or 0)


def _extract_timeout_ids(report_dir: Path) -> List[str]:
    report_path = report_dir / "report.json"
    if not report_path.exists():
        return []
    payload: Dict[str, Any] = json.loads(report_path.read_text(encoding="utf-8"))
    results = payload.get("results", []) or []
    out: List[str] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        arxiv_id = str(r.get("arxiv_id", "")).strip()
        if not arxiv_id:
            continue
        reasons = r.get("reasons", []) or []
        if not isinstance(reasons, list):
            reasons = []
        # Timeout indicator from regression_runner: rc=124 and/or cli_timeout_sec marker in reasons/tail.
        is_timeout = False
        for reason in reasons:
            s = str(reason)
            if s.startswith("cli_failed_rc=124"):
                is_timeout = True
                break
            if "cli_timeout_sec=" in s:
                is_timeout = True
                break
        if is_timeout:
            out.append(arxiv_id)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Run regression runner in batches")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--tier", type=str, default="extended")
    parser.add_argument("--cache-mode", type=str, default="replay")
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--mode", type=str, default="resilient")
    parser.add_argument("--batch-size", type=int, default=3)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--max-batches", type=int, default=0)
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--case-timeout", type=int, default=0)
    parser.add_argument("--retry-timeouts", action="store_true")
    parser.add_argument("--retry-max-passes", type=int, default=2)
    parser.add_argument("--retry-timeout-multiplier", type=float, default=2.0)
    parser.add_argument("--stop-on-fail", action="store_true")
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()

    tier = str(args.tier).strip().lower()
    cache_mode = str(args.cache_mode).strip().lower()
    mode = str(args.mode).strip().lower()
    batch_size = max(1, int(args.batch_size))
    start0 = max(0, int(args.start))
    max_batches = max(0, int(args.max_batches))
    case_timeout_sec = max(0, int(args.case_timeout or 0))
    retry_timeouts = bool(args.retry_timeouts)
    retry_max_passes = max(0, int(args.retry_max_passes or 0))
    retry_multiplier = float(args.retry_timeout_multiplier or 2.0)
    if retry_multiplier < 1.0:
        retry_multiplier = 1.0

    ts = time.strftime("%Y%m%d_%H%M%S")
    run_root = Path(args.report_root) / f"{tier}_{cache_mode}_{mode}_{ts}_bs{batch_size}_start{start0}"
    run_root.mkdir(parents=True, exist_ok=True)

    batches: List[BatchResult] = []

    batch_index = 0
    cursor = start0
    while True:
        batch_index += 1
        if max_batches and batch_index > max_batches:
            break

        report_dir = run_root / f"batch_{batch_index:03d}_start{cursor}_n{batch_size}"
        report_dir.mkdir(parents=True, exist_ok=True)

        if batch_index == 1 and not args.verbose:
            print("NOTE: add --verbose to stream progress from cli.py (otherwise output is buffered).", flush=True)
        print(f"=== Batch {batch_index}: start={cursor} count={batch_size} report_dir={report_dir} ===", flush=True)
        rc = _run_regression_runner(
            corpus=args.corpus,
            tier=tier,
            cache_mode=cache_mode,
            cache_dir=args.cache_dir,
            start=cursor,
            count=batch_size,
            verbose=bool(args.verbose),
            report_dir=report_dir,
            case_timeout_sec=case_timeout_sec,
            mode=mode,
        )

        if retry_timeouts and mode != "strict" and case_timeout_sec > 0 and retry_max_passes > 0:
            timeout_ids = _extract_timeout_ids(report_dir)
            if timeout_ids:
                self_note = f"Retrying {len(timeout_ids)} timeout case(s) with increased case-timeout"
                print(self_note, flush=True)

            current_ids = timeout_ids
            for p in range(1, retry_max_passes + 1):
                if not current_ids:
                    break
                retry_dir = report_dir / f"retry_timeout_pass{p:02d}"
                retry_dir.mkdir(parents=True, exist_ok=True)

                new_timeout = int(case_timeout_sec * (retry_multiplier ** p))
                only_arg = ",".join(current_ids)
                # Re-run only timed out cases; resume should pick up from checkpoint.
                cmd = [
                    sys.executable,
                    str(PROJECT_ROOT / "tests" / "regression_runner.py"),
                    "--corpus",
                    str(args.corpus),
                    "--tier",
                    tier,
                    "--mode",
                    mode,
                    "--cache-mode",
                    cache_mode,
                    "--only",
                    only_arg,
                    "--case-timeout",
                    str(new_timeout),
                    "--report-dir",
                    str(retry_dir),
                ]
                if args.cache_dir is not None:
                    cmd.extend(["--cache-dir", str(args.cache_dir)])
                if args.verbose:
                    cmd.append("--verbose")
                subprocess.run(cmd, cwd=str(PROJECT_ROOT))

                current_ids = _extract_timeout_ids(retry_dir)

        passed, failed = _read_report_summary(report_dir)
        batches.append(
            BatchResult(
                batch_index=batch_index,
                start=cursor,
                count=batch_size,
                report_dir=str(report_dir),
                exit_code=rc,
                passed=passed,
                failed=failed,
            )
        )

        if passed == 0 and failed == 0:
            print("No cases were executed in this batch; stopping.", flush=True)
            break

        if failed and args.stop_on_fail:
            print("Batch had failures and --stop-on-fail is set; stopping.", flush=True)
            break

        cursor += batch_size

    summary = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "corpus": str(args.corpus),
        "tier": tier,
        "cache_mode": cache_mode,
        "mode": mode,
        "cache_dir": str(args.cache_dir) if args.cache_dir is not None else None,
        "batch_size": batch_size,
        "case_timeout": case_timeout_sec,
        "retry_timeouts": retry_timeouts,
        "retry_max_passes": retry_max_passes,
        "retry_timeout_multiplier": retry_multiplier,
        "start": start0,
        "max_batches": max_batches,
        "stop_on_fail": bool(args.stop_on_fail),
        "batches": [b.__dict__ for b in batches],
        "total_passed": sum(b.passed for b in batches),
        "total_failed": sum(b.failed for b in batches),
    }

    (run_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("=== Batch run summary ===")
    print(f"Run root: {run_root}")
    print(f"Batches: {len(batches)}")
    print(f"Total passed: {summary['total_passed']}")
    print(f"Total failed: {summary['total_failed']}")

    return 2 if summary["total_failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
