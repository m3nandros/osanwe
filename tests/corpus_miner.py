import argparse
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import arxiv

from pipeline.arxiv_fetcher import ArxivFetcher


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS_PATH = PROJECT_ROOT / "tests" / "regression_corpus.json"


@dataclass
class Candidate:
    arxiv_id: str
    title: str
    primary_category: str


def _read_main_tex_class(main_tex_path: Path) -> Optional[str]:
    try:
        txt = main_tex_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        try:
            txt = main_tex_path.read_text(encoding="latin-1", errors="replace")
        except Exception:
            return None

    head = "\n".join(txt.splitlines()[:250])

    head = re.sub(r"(?m)^\s*%.*$", "", head)

    m = re.search(r"\\documentclass(?:\[[^\]]*\])?\{([^}]+)\}", head)
    if not m:
        return None
    cls = m.group(1).strip()
    cls = cls.split(",")[0].strip()
    return cls or None


def _load_json(path: Path) -> Dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, payload: Dict):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _existing_ids(corpus_payload: Dict) -> Set[str]:
    out: Set[str] = set()
    for it in corpus_payload.get("items", []) or []:
        arxiv_id = str(it.get("arxiv_id", "")).strip()
        if arxiv_id:
            out.add(arxiv_id)
    return out


def _make_label(cls: str, arxiv_id: str) -> str:
    return f"{cls}-{arxiv_id}"


def _query_candidates(categories: List[str], per_category: int) -> List[Candidate]:
    candidates: List[Candidate] = []

    for cat in categories:
        search = arxiv.Search(
            query=f"cat:{cat}",
            max_results=per_category,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )
        client = arxiv.Client(page_size=min(100, per_category), delay_seconds=3, num_retries=3)
        for result in client.results(search):
            arxiv_id = str(result.get_short_id())
            title = str(result.title or "").strip()
            primary_category = str(getattr(result, "primary_category", "") or cat)
            candidates.append(Candidate(arxiv_id=arxiv_id, title=title, primary_category=primary_category))

    seen: Set[str] = set()
    uniq: List[Candidate] = []
    for c in candidates:
        if c.arxiv_id in seen:
            continue
        seen.add(c.arxiv_id)
        uniq.append(c)
    return uniq


def mine_and_merge(
    corpus_path: Path,
    target_count: int,
    categories: List[str],
    per_category: int,
    allowed_classes: Set[str],
    tier: str,
    dry_run: bool,
) -> Tuple[int, int]:
    corpus_payload = _load_json(corpus_path)
    items: List[Dict] = corpus_payload.get("items", [])
    if not isinstance(items, list):
        raise ValueError("Invalid corpus format: items must be a list")

    existing = _existing_ids(corpus_payload)

    candidates = _query_candidates(categories, per_category)

    fetcher = ArxivFetcher(temp_dir=None)

    added = 0
    scanned = 0

    for cand in candidates:
        if added >= target_count:
            break
        if cand.arxiv_id in existing:
            continue

        scanned += 1
        try:
            source_dir = fetcher.download_sources(cand.arxiv_id)
            main_tex = fetcher.find_main_tex(source_dir)
            cls = _read_main_tex_class(main_tex)
        except Exception:
            continue

        if not cls:
            continue

        cls_norm = cls.strip()
        if cls_norm not in allowed_classes:
            continue

        items.append(
            {
                "arxiv_id": cand.arxiv_id,
                "label": _make_label(cls_norm, cand.arxiv_id),
                "tier": tier,
                "notes": f"auto-mined class={cls_norm} cat={cand.primary_category}",
            }
        )
        existing.add(cand.arxiv_id)
        added += 1

    corpus_payload["items"] = items
    corpus_payload["version"] = int(corpus_payload.get("version", 1) or 1)
    corpus_payload["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    if not dry_run:
        _save_json(corpus_path, corpus_payload)

    return added, scanned


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument("--target-count", type=int, default=50)
    parser.add_argument("--tier", type=str, default="extended")
    parser.add_argument("--categories", type=str, default="cs.CV,cs.LG,cs.CL,math.PR,physics.optics")
    parser.add_argument("--per-category", type=int, default=200)
    parser.add_argument(
        "--classes",
        type=str,
        default="IEEEtran,neurips_2024,neurips_2023,iclr2025_conference,iclr2024_conference,cvpr,elsarticle,acmart,llncs,mnras,quantumarticle",
    )
    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    categories = [c.strip() for c in str(args.categories).split(",") if c.strip()]
    allowed_classes = {c.strip() for c in str(args.classes).split(",") if c.strip()}

    added, scanned = mine_and_merge(
        corpus_path=args.corpus,
        target_count=max(0, int(args.target_count)),
        categories=categories,
        per_category=max(1, int(args.per_category)),
        allowed_classes=allowed_classes,
        tier=str(args.tier).strip().lower(),
        dry_run=bool(args.dry_run),
    )

    print(f"scanned={scanned} added={added} corpus={args.corpus}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
