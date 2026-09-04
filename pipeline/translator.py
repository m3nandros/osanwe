"""
Translation Orchestrator module for Rosetta v3.

Coordinates the entire translation pipeline:
Fetcher -> Masker -> Splitter -> Translator -> Assembler -> Post-processor -> Compiler
"""

import os
import re
import time
import json
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

from pipeline.arxiv_fetcher import ArxivFetcher, ArxivArticle
from pipeline.masker import ContentMasker
from pipeline.splitter import ContentSplitter
from pipeline.assembler import LaTeXRestorer
from integrations.openai_client import OpenAIClient
# Skeletons
from pipeline.latex_postprocessor import post_process_latex
from pipeline.validator import validate_translation
from pipeline.pdf_compiler import compile_pdf, CompilationResult
from utils.logger import get_logger
from utils.glossary import load_glossary
from config import Config

logger = get_logger(__name__)

class TranslationOrchestrator:
    """
    Orchestrates the translation process for a single article.
    """
    
    def __init__(self):
        """Initialize all pipeline components."""
        self.fetcher = ArxivFetcher()
        self.masker = ContentMasker()
        self.splitter = ContentSplitter()
        self.client = OpenAIClient()
        self.restorer = LaTeXRestorer()
        self.logger = get_logger(__name__)

    def _normalize_target_lang(self, target_lang: str) -> str:
        t = str(target_lang or "ru").strip().lower() or "ru"
        mapping = {
            "ch": "zh",
            "cn": "zh",
            "zh-cn": "zh",
            "zh-hans": "zh",
            "zh-hant": "zh",
            "jp": "ja",
            "jpn": "ja",
            "ja-jp": "ja",
            "kr": "ko",
            "kor": "ko",
            "ko-kr": "ko",
            "korean": "ko",
        }
        return mapping.get(t, t)

    def _checkpoint_path(self, work_dir: Path) -> Path:
        return work_dir / "translation_checkpoint.json"

    def _atomic_write_json(self, path: Path, payload: Dict[str, Any]):
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    def _sha256_text(self, s: str) -> str:
        h = hashlib.sha256()
        h.update(s.encode("utf-8", errors="replace"))
        return h.hexdigest()

    def _timing_path(self, work_dir: Path, target_lang: str) -> Path:
        lang = self._normalize_target_lang(target_lang)
        return work_dir / f"translation_timing.{lang}.json"

    def _compile_cache_path(self, work_dir: Path, target_lang: str) -> Path:
        lang = self._normalize_target_lang(target_lang)
        return work_dir / f"compile_cache.{lang}.json"

    def translate_article(self, arxiv_id: str, output_dir: Optional[Path] = None, target_lang: str = "ru") -> bool:
        """
        Run the full translation pipeline for an arXiv article.
        
        Args:
            arxiv_id: arXiv ID (e.g., "1706.03762")
            output_dir: Optional override for output directory
            
        Returns:
            True if successful, False otherwise
        """
        self.logger.info(f"Starting translation pipeline for {arxiv_id}")

        t_pipeline_start = time.time()
        timing: Dict[str, Any] = {
            "arxiv_id": arxiv_id,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "target_lang": str(target_lang),
            "target_lang_normalized": None,
            "stages": {},
            "chunks": [],
            "final": {},
        }
        timing_path: Optional[Path] = None

        normalized_target_lang = self._normalize_target_lang(target_lang)
        is_cjk = normalized_target_lang in ("ja", "zh", "ko")

        try:
            full_glossary: Optional[Dict[str, Any]] = load_glossary(str(Config.GLOSSARY_PATH))
        except Exception as e:
            self.logger.warning(f"Failed to load glossary: {e}")
            full_glossary = None
        self.logger.info(f"Target language: {target_lang} -> {normalized_target_lang}")
        validate_for_lang = normalized_target_lang not in ("en",)

        timing["target_lang_normalized"] = normalized_target_lang
        # 1. Fetch Article
        try:
            t0 = time.time()
            article = self.fetcher.fetch_article(arxiv_id)
            if not article:
                self.logger.error(f"Failed to fetch article {arxiv_id}")
                return False
            timing["stages"]["fetch_sec"] = round(time.time() - t0, 6)
        except Exception as e:
            self.logger.error(f"Exception during fetching: {e}")
            return False
            
        work_dir = article.source_directory
        main_tex = article.main_tex_path

        timing_path = self._timing_path(work_dir, normalized_target_lang)
        
        self.logger.info(f"Working directory: {work_dir}")
        self.logger.info(f"Main TeX file: {main_tex}")
        # 2. Read Content
        try:
            t0 = time.time()
            with open(main_tex, 'r', encoding='utf-8', errors='replace') as f:
                original_content = f.read()
            timing["stages"]["read_main_tex_sec"] = round(time.time() - t0, 6)
                
            # Flatten content (recursive input)
            self.logger.info("Flattening LaTeX content (resolving inputs)...")
            t0 = time.time()
            original_content = self._flatten_latex(work_dir, original_content)
            timing["stages"]["flatten_sec"] = round(time.time() - t0, 6)
            
            # Patch style files
            t0 = time.time()
            self._patch_style_files(work_dir)
            timing["stages"]["patch_style_files_sec"] = round(time.time() - t0, 6)
            
        except Exception as e:
            self.logger.error(f"Failed to read/flatten main file: {e}")
            return False
            
        # 3. Mask Content
        self.logger.info("Phase 3: Masking content...")
        t0 = time.time()
        masked = self.masker.mask_content(original_content)
        timing["stages"]["mask_sec"] = round(time.time() - t0, 6)
        self.logger.info(f"Masking complete. Stats: {masked.stats}")


        # 4. Split Content
        self.logger.info("Phase 4: Splitting content...")
        t0 = time.time()
        chunks = self.splitter.split_content(masked.text)
        timing["stages"]["split_sec"] = round(time.time() - t0, 6)
        self.logger.info(f"Split into {len(chunks)} chunks")

        # 5. Translate Chunks
        self.logger.info("Phase 5: Translating chunks...")
        translated_chunks = []
        total_cost = 0.0

        translate_stage_t0 = time.time()

        chunk_retries = int(os.environ.get("ROSETTA_TRANSLATION_CHUNK_RETRIES", "3") or "3")
        if chunk_retries < 1:
            chunk_retries = 1

        resume_enabled = str(os.environ.get("ROSETTA_TRANSLATION_RESUME", "1") or "1").strip().lower() not in ("0", "false", "no")
        checkpoint_path = self._checkpoint_path(work_dir)
        masked_sha = self._sha256_text(masked.text)
        resumed_by_index: Dict[int, Dict[str, Any]] = {}
        if resume_enabled and checkpoint_path.exists():
            try:
                ckpt = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                ckpt_lang = self._normalize_target_lang(str(ckpt.get("target_lang", "") or "").strip().lower())
                if (
                    str(ckpt.get("arxiv_id", "")) == arxiv_id
                    and str(ckpt.get("masked_sha256", "")) == masked_sha
                    and ckpt_lang == normalized_target_lang
                ):
                    stored = ckpt.get("translated_by_index", {}) or {}
                    if isinstance(stored, dict):
                        for k, v in stored.items():
                            try:
                                idx = int(k)
                            except Exception:
                                continue
                            if isinstance(v, dict) and "text" in v and "order" in v and "type" in v:
                                resumed_by_index[idx] = v
                    total_cost = float(ckpt.get("total_cost", 0.0) or 0.0)
                    self.logger.info(f"Resuming translation from checkpoint: {len(resumed_by_index)}/{len(chunks)} chunks already translated")
                else:
                    self.logger.info("Checkpoint exists but does not match current masked content; ignoring checkpoint")
            except Exception as e:
                self.logger.warning(f"Could not load checkpoint: {e}")

        translated_by_index_to_save: Dict[str, Any] = {str(k): v for k, v in resumed_by_index.items()}

        assemble_only = str(os.environ.get("ROSETTA_ASSEMBLE_FROM_CHECKPOINT", "0") or "0").strip().lower() in (
            "1",
            "true",
            "yes",
        )

        if assemble_only:
            if not (resume_enabled and checkpoint_path.exists() and resumed_by_index):
                self.logger.error(
                    "ROSETTA_ASSEMBLE_FROM_CHECKPOINT is enabled, but no usable checkpoint was found."
                )
                return False

            missing = [i for i in range(len(chunks)) if i not in resumed_by_index]
            if missing:
                self.logger.error(
                    f"Checkpoint is incomplete: missing {len(missing)}/{len(chunks)} chunks. Missing indices: {missing[:24]}"
                )
                return False

            self.logger.info(
                f"Assembling from checkpoint only: {len(resumed_by_index)}/{len(chunks)} chunks (no API calls)"
            )

            for i, chunk in enumerate(chunks):
                resumed = resumed_by_index[i]
                resumed_text = str(resumed.get("text", ""))
                translated_chunks.append(
                    {
                        "order": resumed.get("order", chunk.order),
                        "text": resumed_text,
                        "type": resumed.get("type", chunk.type),
                    }
                )

            self.logger.info(f"Using checkpoint total_cost=${total_cost:.4f}")

            # Jump to assembly/restoration without translating.
            self.logger.info("Phase 6: Assembling and Restoring...")
            t0 = time.time()
            restoration_result = self.restorer.assemble_and_restore(translated_chunks, masked.token_map)
            timing["stages"]["assemble_restore_sec"] = round(time.time() - t0, 6)
            if not restoration_result.success:
                self.logger.warning(f"Restoration had issues: {len(restoration_result.missing_tokens)} missing tokens")
            translated_text = restoration_result.full_text
            translated_text = self._restore_preamble_if_missing(translated_text, original_content)
            if normalized_target_lang == "ru":
                self.logger.info("Phase 7: Adding Cyrillic support...")
                translated_text = self._add_cyrillic_support(translated_text)
            else:
                self.logger.info("Phase 7: Skipping Cyrillic support (target language is not ru)...")
            translated_text = self._add_arxiv_stamp(translated_text, article)
            self.logger.info("Phase 8: Post-processing...")
            t0 = time.time()
            translated_text = post_process_latex(translated_text, target_lang=normalized_target_lang)
            timing["stages"]["postprocess_sec"] = round(time.time() - t0, 6)

            # Save & compile
            output_path = work_dir / "translated.tex"
            try:
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(translated_text)
                self.logger.info(f"Saved translated LaTeX to {output_path}")
            except Exception as e:
                self.logger.error(f"Failed to save output: {e}")
                return False

            self.logger.info("Phase 9: Compiling...")
            t0 = time.time()
            compile_result = compile_pdf(output_path)
            timing["stages"]["compile_sec"] = round(time.time() - t0, 6)
            if compile_result.success:
                self.logger.info(f"Compilation successful! PDF saved to {compile_result.pdf_path}")
            else:
                self.logger.error(f"Compilation failed after {compile_result.attempts} attempts.")
                self.logger.error(f"Errors: {compile_result.errors}")
            timing["stages"]["pipeline_total_sec"] = round(time.time() - t_pipeline_start, 6)
            timing["final"]["total_cost_usd"] = round(float(total_cost or 0.0), 6)
            if timing_path:
                try:
                    timing_path.write_text(
                        json.dumps(timing, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                except Exception as e:
                    self.logger.warning(f"Failed to write timing report: {e}")
            return True

        is_pdf_wrapper = ("\\usepackage{pdfpages}" in masked.text) and ("\\includepdf" in masked.text)

        concurrency_env = (os.environ.get("ROSETTA_TRANSLATION_CONCURRENCY", "") or "").strip()
        try:
            concurrency = int(concurrency_env) if concurrency_env else 6
        except Exception:
            concurrency = 12
        if concurrency < 1:
            concurrency = 1

        thread_local = threading.local()

        def _get_thread_client() -> OpenAIClient:
            c = getattr(thread_local, "client", None)
            if c is None:
                c = OpenAIClient()
                thread_local.client = c
            return c

        def _translate_chunk_task(i: int, chunk: Any) -> Dict[str, Any]:
            t0_local = time.time()
            task_stats: Dict[str, Any] = {
                "chunk_index": i,
                "chunk_type": getattr(chunk, "type", None),
                "token_count": getattr(chunk, "token_count", None),
                "api_attempts": 0,
                "validate": {},
            }

            client = _get_thread_client()
            last_err: Optional[Exception] = None
            result = None
            for attempt in range(chunk_retries):
                try:
                    task_stats["api_attempts"] = attempt + 1
                    result = client.translate_latex(
                        chunk.text,
                        target_lang=normalized_target_lang,
                        full_glossary=full_glossary,
                    )
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    self.logger.warning(
                        f"Chunk {i+1}/{len(chunks)} translation attempt {attempt+1}/{chunk_retries} failed: {e}"
                    )
                    time.sleep(min(8.0, 1.0 * (2 ** attempt)))
            if result is None:
                raise last_err if last_err is not None else RuntimeError("Chunk translation failed")

            translated_text_local = result.translated_content
            if validate_for_lang and chunk.type in ('intro', 'section', 'subsection', 'paragraph'):
                translated_text_local = self._validate_and_retry_translation(
                    chunk.text,
                    translated_text_local,
                    chunk.type,
                    i,
                    target_lang=normalized_target_lang,
                    stats=task_stats["validate"],
                    client=client,
                    full_glossary=full_glossary,
                )

            task_stats["duration_sec"] = round(time.time() - t0_local, 6)
            task_stats["cost_usd"] = float(getattr(result, "cost_usd", 0.0) or 0.0)
            return {
                "i": i,
                "order": chunk.order,
                "type": chunk.type,
                "text": translated_text_local,
                "cost_usd": task_stats["cost_usd"],
                "timing": task_stats,
            }

        translated_slots: List[Optional[Dict[str, Any]]] = [None for _ in range(len(chunks))]
        pending_indices: List[int] = []

        for i, chunk in enumerate(chunks):
            if resume_enabled and i in resumed_by_index:
                resumed = resumed_by_index[i]
                resumed_text = str(resumed.get("text", ""))

                if validate_for_lang and chunk.type in ('intro', 'section', 'subsection', 'paragraph'):
                    t0 = time.time()
                    validated_text = self._validate_and_retry_translation(
                        chunk.text,
                        resumed_text,
                        chunk.type,
                        i,
                        target_lang=normalized_target_lang,
                        stats={},
                        client=self.client,
                        full_glossary=full_glossary,
                    )
                    timing["chunks"].append(
                        {
                            "chunk_index": i,
                            "chunk_type": chunk.type,
                            "token_count": chunk.token_count,
                            "duration_sec": round(time.time() - t0, 6),
                            "resume": True,
                            "note": "resume_validation",
                        }
                    )
                    if validated_text != resumed_text:
                        resumed = dict(resumed)
                        resumed["text"] = validated_text
                        resumed_by_index[i] = resumed
                        translated_by_index_to_save[str(i)] = {
                            'order': resumed.get('order', chunk.order),
                            'text': validated_text,
                            'type': resumed.get('type', chunk.type),
                        }
                        payload = {
                            'arxiv_id': arxiv_id,
                            'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                            'target_lang': normalized_target_lang,
                            'masked_sha256': masked_sha,
                            'total_chunks': len(chunks),
                            'total_cost': total_cost,
                            'translated_by_index': translated_by_index_to_save,
                        }
                        self._atomic_write_json(checkpoint_path, payload)
                        resumed_text = validated_text

                translated_slots[i] = {
                    'order': resumed.get('order', chunk.order),
                    'text': resumed_text,
                    'type': resumed.get('type', chunk.type),
                }
                continue

            if is_pdf_wrapper:
                self.logger.info(f"Skipping chunk {i+1}/{len(chunks)}: {chunk.type} (pdf wrapper, no translation needed)")
                passthrough_text = chunk.text
                for token, original in masked.token_map.items():
                    passthrough_text = passthrough_text.replace(token, original)
                translated_slots[i] = {
                    'order': chunk.order,
                    'text': passthrough_text,
                    'type': chunk.type
                }
                if resume_enabled:
                    translated_by_index_to_save[str(i)] = {
                        'order': chunk.order,
                        'text': passthrough_text,
                        'type': chunk.type
                    }
                    payload = {
                        'arxiv_id': arxiv_id,
                        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                        'target_lang': normalized_target_lang,
                        'masked_sha256': masked_sha,
                        'total_chunks': len(chunks),
                        'total_cost': total_cost,
                        'translated_by_index': translated_by_index_to_save,
                    }
                    self._atomic_write_json(checkpoint_path, payload)
                continue

            # Skip bibliography chunks - don't translate them
            if chunk.type == 'bib':
                self.logger.info(f"Skipping chunk {i+1}/{len(chunks)}: {chunk.type} (bibliography, no translation needed)")
                # Restore tokens in bibliography (it wasn't translated, so tokens are still there)
                bib_text = chunk.text
                for token, original in masked.token_map.items():
                    bib_text = bib_text.replace(token, original)
                translated_slots[i] = {
                    'order': chunk.order,
                    'text': bib_text,  # Restored bibliography
                    'type': chunk.type
                }
                if resume_enabled:
                    translated_by_index_to_save[str(i)] = {
                        'order': chunk.order,
                        'text': bib_text,
                        'type': chunk.type
                    }
                    payload = {
                        'arxiv_id': arxiv_id,
                        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                        'target_lang': normalized_target_lang,
                        'masked_sha256': masked_sha,
                        'total_chunks': len(chunks),
                        'total_cost': total_cost,
                        'translated_by_index': translated_by_index_to_save,
                    }
                    self._atomic_write_json(checkpoint_path, payload)
                continue

            pending_indices.append(i)

        if pending_indices:
            self.logger.info(
                f"Translating {len(pending_indices)} chunks with concurrency={concurrency} (skipped/resumed={len(chunks) - len(pending_indices)})"
            )
            try:
                with ThreadPoolExecutor(max_workers=concurrency) as ex:
                    futures = {ex.submit(_translate_chunk_task, i, chunks[i]): i for i in pending_indices}
                    for fut in as_completed(futures):
                        i = futures[fut]
                        try:
                            payload = fut.result()
                        except Exception as e:
                            self.logger.error(f"Error translating chunk {i}: {e}")
                            return False

                        translated_slots[i] = {
                            'order': payload['order'],
                            'text': payload['text'],
                            'type': payload['type'],
                        }

                        total_cost += float(payload.get('cost_usd', 0.0) or 0.0)
                        if isinstance(payload.get("timing"), dict):
                            timing["chunks"].append(dict(payload["timing"]))

                        if resume_enabled:
                            translated_by_index_to_save[str(i)] = {
                                'order': payload['order'],
                                'text': payload['text'],
                                'type': payload['type'],
                            }
                            ckpt_payload = {
                                'arxiv_id': arxiv_id,
                                'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                                'target_lang': normalized_target_lang,
                                'masked_sha256': masked_sha,
                                'total_chunks': len(chunks),
                                'total_cost': total_cost,
                                'translated_by_index': translated_by_index_to_save,
                            }
                            self._atomic_write_json(checkpoint_path, ckpt_payload)
            except Exception as e:
                self.logger.error(f"Parallel translation failed: {e}")
                return False

        translated_chunks = [x for x in translated_slots if x is not None]
        timing["stages"]["translate_sec"] = round(time.time() - translate_stage_t0, 6)
        self.logger.info(f"Translation complete. Total cost: ${total_cost:.4f}")
        # 6. Assemble and Restore
        self.logger.info("Phase 6: Assembling and Restoring...")
        t0 = time.time()
        restoration_result = self.restorer.assemble_and_restore(translated_chunks, masked.token_map)
        timing["stages"]["assemble_restore_sec"] = round(time.time() - t0, 6)
        
        if not restoration_result.success:
            self.logger.warning(f"Restoration had issues: {len(restoration_result.missing_tokens)} missing tokens")
            
        translated_text = restoration_result.full_text
        # 6.5. Восстановление потерянной преамбулы
        # GPT иногда теряет преамбулу при переводе больших чанков
        translated_text = self._restore_preamble_if_missing(translated_text, original_content)
        
        # 7. Cyrillization (Add Russian language support)
        if normalized_target_lang == "ru":
            self.logger.info("Phase 7: Adding Cyrillic support...")
            translated_text = self._add_cyrillic_support(translated_text)
        else:
            self.logger.info("Phase 7: Skipping Cyrillic support (target language is not ru)...")
        translated_text = self._add_arxiv_stamp(translated_text, article)
        
        # 8. Post-processing (Regex fixes + Bold formatting)
        self.logger.info("Phase 8: Post-processing...")
        t0 = time.time()
        translated_text = post_process_latex(translated_text, target_lang=normalized_target_lang)
        timing["stages"]["postprocess_sec"] = round(time.time() - t0, 6)

        # 8.5 Final agentic safeguard: remove any remaining English blocks (for any non-English target).
        if normalized_target_lang not in ("en",) and self._final_repair_agent_enabled():
            repair_stats: Dict[str, Any] = {}
            t0 = time.time()
            translated_text = self._final_repair_agent(translated_text, target_lang=normalized_target_lang, stats=repair_stats)
            timing["stages"]["final_repair_agent_sec"] = round(time.time() - t0, 6)
            timing["final"]["final_repair_agent"] = repair_stats
            t0 = time.time()
            translated_text = post_process_latex(translated_text, target_lang=normalized_target_lang)
            timing["stages"]["postprocess_after_repair_sec"] = round(time.time() - t0, 6)

        final_validation_enabled = str(os.environ.get("ROSETTA_FINAL_VALIDATION", "1") or "1").strip().lower() not in (
            "0",
            "false",
            "no",
        )
        final_validation_strict = str(os.environ.get("ROSETTA_FINAL_VALIDATION_STRICT", "0") or "0").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if final_validation_enabled and validate_for_lang:
            try:
                t0 = time.time()
                validation = validate_translation(
                    original_content,
                    translated_text,
                    target_lang=normalized_target_lang,
                    glossary=full_glossary,
                )
                timing["stages"]["final_validation_sec"] = round(time.time() - t0, 6)
                report_path = work_dir / "translation_validation.json"
                report_payload = {
                    "valid": bool(validation.valid),
                    "score": float(validation.score),
                    "issues": list(validation.issues),
                    "metrics": dict(validation.metrics),
                    "target_lang": normalized_target_lang,
                    "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                try:
                    report_path.write_text(
                        json.dumps(report_payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                except Exception as e:
                    self.logger.warning(f"Failed to write validation report: {e}")

                if validation.valid:
                    self.logger.info(f"Final validation passed. score={validation.score:.2f}")
                else:
                    self.logger.warning(
                        f"Final validation failed. score={validation.score:.2f} issues={len(validation.issues)} strict={final_validation_strict}"
                    )
                    for it in validation.issues[:12]:
                        self.logger.warning(f"Validation issue: {it}")
                    if final_validation_strict:
                        return False
            except Exception as e:
                self.logger.warning(f"Final validation error: {e}")
        
        # 9. Save Result
        output_path = work_dir / "translated.tex"
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(translated_text)
            self.logger.info(f"Saved translated LaTeX to {output_path}")
        except Exception as e:
            self.logger.error(f"Failed to save output: {e}")
            return False

        # 9. Compile
        compile_cache_enabled = str(os.environ.get("ROSETTA_COMPILE_CACHE", "0") or "0").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        translated_sha = self._sha256_text(translated_text)
        compile_cache_path = self._compile_cache_path(work_dir, normalized_target_lang)

        if compile_cache_enabled and compile_cache_path.exists():
            try:
                cached = json.loads(compile_cache_path.read_text(encoding="utf-8"))
            except Exception:
                cached = {}

            cached_sha = str((cached or {}).get("translated_sha256") or "")
            cached_success = bool((cached or {}).get("success"))
            cached_pdf_path = str((cached or {}).get("pdf_path") or "")
            cached_pdf_ok = bool(cached_pdf_path) and Path(cached_pdf_path).exists()
            if cached_success and cached_sha and cached_sha == translated_sha and cached_pdf_ok:
                self.logger.info("Phase 9: Compiling... (skipped; compile cache hit)")
                t0 = time.time()
                compile_result = CompilationResult(
                    success=True,
                    pdf_path=Path(cached_pdf_path),
                    log_path=Path(str((cached or {}).get("log_path") or "")) if (cached or {}).get("log_path") else None,
                    errors=[],
                    attempts=int((cached or {}).get("attempts") or 0),
                )
                timing["stages"]["compile_sec"] = round(time.time() - t0, 6)
            else:
                self.logger.info("Phase 9: Compiling...")
                t0 = time.time()
                compile_result = compile_pdf(output_path)
                timing["stages"]["compile_sec"] = round(time.time() - t0, 6)
        else:
            self.logger.info("Phase 9: Compiling...")
            t0 = time.time()
            compile_result = compile_pdf(output_path)
            timing["stages"]["compile_sec"] = round(time.time() - t0, 6)

        if (not compile_result.success) and self._is_resilient_mode() and self._has_table_structure_errors(compile_result.errors):
            restored_tables_text = self._restore_tables_from_original(original_content, translated_text)
            if restored_tables_text != translated_text:
                try:
                    with open(output_path, 'w', encoding='utf-8') as f:
                        f.write(restored_tables_text)
                    self.logger.info("Повторная компиляция: восстановлены таблицы из оригинала (fallback)")
                    translated_text = restored_tables_text
                    compile_result = compile_pdf(output_path)
                except Exception as e:
                    self.logger.warning(f"Не удалось перезаписать translated.tex для table-fallback: {e}")
                if compile_result.success:
                    self.logger.info(f"Compilation successful! PDF saved to {compile_result.pdf_path}")
                else:
                    self.logger.error(f"Compilation failed after {compile_result.attempts} attempts.")
                    self.logger.error(f"Errors: {compile_result.errors}")
                    # We still return True because we have the translated TeX
            else:
                self.logger.error(f"Compilation failed after {compile_result.attempts} attempts.")
                self.logger.error(f"Errors: {compile_result.errors}")
                # We still return True because we have the translated TeX
        
        if compile_cache_enabled:
            try:
                compile_cache_payload = {
                    "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "target_lang": normalized_target_lang,
                    "translated_sha256": translated_sha,
                    "success": bool(getattr(compile_result, "success", False)),
                    "attempts": int(getattr(compile_result, "attempts", 0) or 0),
                    "pdf_path": str(getattr(compile_result, "pdf_path", "") or ""),
                    "log_path": str(getattr(compile_result, "log_path", "") or ""),
                }
                compile_cache_path.write_text(
                    json.dumps(compile_cache_payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except Exception as e:
                self.logger.warning(f"Failed to write compile cache: {e}")

        timing["stages"]["pipeline_total_sec"] = round(time.time() - t_pipeline_start, 6)
        timing["final"]["total_cost_usd"] = round(float(total_cost or 0.0), 6)
        timing["final"]["compile"] = {
            "success": bool(getattr(compile_result, "success", False)),
            "attempts": int(getattr(compile_result, "attempts", 0) or 0),
            "pdf_path": str(getattr(compile_result, "pdf_path", "") or ""),
            "log_path": str(getattr(compile_result, "log_path", "") or ""),
            "errors_count": len(getattr(compile_result, "errors", []) or []),
        }

        if timing_path:
            try:
                timing_path.write_text(
                    json.dumps(timing, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except Exception as e:
                self.logger.warning(f"Failed to write timing report: {e}")
        
        return True

    def _final_repair_agent_enabled(self) -> bool:
        v = str(os.environ.get("ROSETTA_FINAL_REPAIR_AGENT", "1") or "1").strip().lower()
        return v not in ("0", "false", "no")

    def _final_repair_agent(self, latex_text: str, target_lang: str, stats: Optional[Dict[str, Any]] = None) -> str:
        # Keep conservative: only patch a few top suspicious blocks per run.
        max_blocks = int(os.environ.get("ROSETTA_FINAL_REPAIR_MAX_BLOCKS", "6") or "6")
        if max_blocks < 1:
            return latex_text

        if stats is None:
            stats = {}

        stats["max_blocks"] = int(max_blocks)

        normalized_target_lang = self._normalize_target_lang(target_lang)
        is_cjk = normalized_target_lang in ("ja", "zh", "ko")

        def strip_latex(text: str) -> str:
            t = re.sub(r"<<[A-Z_]+_\d+>>", "", text)
            t = re.sub(r"%.*", "", t)
            t = re.sub(r"\\(?!author\b|title\b|thanks\b)[a-zA-Z]+\*?(\[[^\]]*\])?(\{[^}]*\})*", " ", t)
            t = re.sub(r"\$\$.*?\$\$", " ", t, flags=re.DOTALL)
            t = re.sub(r"\$[^$]*\$", " ", t)
            t = re.sub(r"\\\[.*?\\\]", " ", t, flags=re.DOTALL)
            t = re.sub(r"\\begin\{equation\*?\}.*?\\end\{equation\*?\}", " ", t, flags=re.DOTALL)
            t = re.sub(r"\\begin\{align\*?\}.*?\\end\{align\*?\}", " ", t, flags=re.DOTALL)
            t = re.sub(r"[{}\\$%&]", " ", t)
            return re.sub(r"\s+", " ", t).strip()

        def extract_command_block_balanced(text: str, cmd: str) -> list[tuple[int, int, str]]:
            out: list[tuple[int, int, str]] = []
            needle = f"\\{cmd}"
            i = 0
            n = len(text)
            while i < n:
                j = text.find(needle, i)
                if j < 0:
                    break
                k = j + len(needle)
                while k < n and text[k].isspace():
                    k += 1
                if k >= n or text[k] != "{":
                    i = j + 1
                    continue
                depth = 0
                end = None
                for p in range(k, n):
                    ch = text[p]
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            end = p + 1
                            break
                if end is not None:
                    out.append((j, end, text[j:end]))
                    i = end
                else:
                    i = j + 1
            return out

        def is_balanced_braces(s: str) -> bool:
            depth = 0
            for ch in s:
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth < 0:
                        return False
            return depth == 0

        def count_english_words(text: str) -> int:
            words = [w.lower() for w in re.findall(r"\b[a-zA-Z]{2,}\b", text)]
            words = [w for w in words if not w.isupper()]
            en_stop = {
                "the", "and", "or", "of", "to", "in", "for", "with", "without", "from", "on", "at", "by",
                "as", "that", "this", "these", "those", "which", "who", "whom", "whose",
                "a", "an", "is", "are", "was", "were", "be", "been", "being",
                "it", "its", "we", "our", "us", "their", "they", "them",
                "can", "could", "may", "might", "will", "would", "should",
                "also", "such", "than", "then", "there", "here", "where", "when", "while",
            }
            return sum(1 for w in words if w in en_stop)

        def _fallback_translate_english_spans(block: str) -> tuple[str, int]:
            """Translate only plain-English spans inside a LaTeX block (no commands/braces/math).

            This is a safety net for cases where the LLM returns invalid LaTeX (e.g., unbalanced braces).
            It keeps LaTeX structure intact by replacing only detected English substrings.
            """
            try:
                # Exclude LaTeX-sensitive chars to avoid touching structure.
                rx = re.compile(r"[A-Za-z][^\\{}$\n]{60,}")
                spans = []
                seen = set()
                for m in rx.finditer(block):
                    s = re.sub(r"\s+", " ", m.group(0)).strip()
                    if count_english_words(s) < (10 if normalized_target_lang in ("ja", "zh", "ko") else 16):
                        continue
                    if s in seen:
                        continue
                    seen.add(s)
                    spans.append(s)
                if not spans:
                    return block, 0
                translated = self.client.translate_markdown_lines(spans, target_lang=normalized_target_lang)
                out_block = block
                replaced = 0
                for src, dst in zip(spans, translated):
                    if not (isinstance(dst, str) and dst.strip()) or dst.strip() == src:
                        continue
                    out_block = out_block.replace(src, dst.strip(), 1)
                    replaced += 1
                return out_block, replaced
            except Exception:
                return block, 0

        def has_non_ascii_letters(text: str) -> bool:
            for ch in text:
                if ord(ch) < 128:
                    continue
                if ch.isalpha():
                    return True
            return False

        def is_english_heavy_block(text: str) -> bool:
            t = re.sub(r"\s+", " ", text).strip()
            min_len = 90 if normalized_target_lang in ("ja", "zh", "ko") else 140
            if len(t) < min_len:
                return False
            if normalized_target_lang in ("ja", "zh", "ko"):
                cjk_chars = re.findall(r"[\u3040-\u30FF\u3400-\u4DBF\u4E00-\u9FFF\uAC00-\uD7AF]", t)
                if len(cjk_chars) >= 40:
                    return False
            letters = re.findall(r"[A-Za-z]", t)
            nonspace = re.findall(r"\S", t)
            if not nonspace:
                return False
            ascii_ratio = len(letters) / max(1, len(nonspace))
            en_hits = count_english_words(t)
            if ascii_ratio < 0.25:
                return False
            min_hits = 4 if normalized_target_lang in ("ja", "zh", "ko") else 6
            if en_hits >= min_hits:
                return True
            return False

        def _mask_inline_math(s: str) -> tuple[str, dict[str, str]]:
            mapping: dict[str, str] = {}
            idx = 0

            def repl(m: re.Match) -> str:
                nonlocal idx
                key = f"<<MATH_{idx}>>"
                idx += 1
                mapping[key] = m.group(0)
                return key

            # Mask common math forms so span-translation doesn't break them.
            s = re.sub(r"\$\$.*?\$\$", repl, s, flags=re.DOTALL)
            s = re.sub(r"\$[^$]*\$", repl, s)
            s = re.sub(r"\\\[.*?\\\]", repl, s, flags=re.DOTALL)
            return s, mapping

        def _unmask_math(s: str, mapping: dict[str, str]) -> str:
            for k, v in mapping.items():
                s = s.replace(k, v)
            return s

        def _translate_english_lines_global(text: str) -> tuple[str, int]:
            """Translate remaining English lines while preserving LaTeX structure.

            Many missed cases contain LaTeX commands like \ref{...}/\cite{...} inside English sentences.
            We mask inline math and selected LaTeX commands to <<MATH_N>>/<<CODE_N>> tokens,
            translate the whole line, then unmask.
            """

            default_max_lines = "25" if is_cjk else "80"
            max_lines = int(os.environ.get("ROSETTA_FINAL_REPAIR_MAX_LINES", default_max_lines) or default_max_lines)
            if max_lines < 1:
                return text, 0

            lines = text.splitlines(keepends=True)
            in_bib = False

            # Commands that should be preserved verbatim and not translated.
            cmd_names = (
                "cite|citep|citet|citealp|citeauthor|citenum|ref|eqref|pageref|label|url|href|"
                "footnote|footnotemark|footnotetext|includegraphics|begin|end|item|hspace|vspace"
            )
            cmd_rx = re.compile(
                rf"\\(?:{cmd_names})\*?(?:\[[^\]]*\])?(?:\{{[^{{}}]*\}})*"
            )

            candidates: list[tuple[int, str, dict[str, str], dict[str, str]]] = []
            for i, ln in enumerate(lines):
                raw = ln
                if raw.lstrip().startswith("%"):
                    continue
                if "\\begin{thebibliography}" in raw:
                    in_bib = True
                if in_bib:
                    if "\\end{thebibliography}" in raw:
                        in_bib = False
                    continue

                plain = strip_latex(raw)
                # Catch shorter English lines too (e.g., "The encoder is composed ...").
                if count_english_words(plain) < (4 if normalized_target_lang in ("ja", "zh", "ko") else 10):
                    continue

                masked, math_map = _mask_inline_math(raw)
                code_map: dict[str, str] = {}
                code_i = 0

                def cmd_repl(m: re.Match) -> str:
                    nonlocal code_i
                    key = f"<<CODE_{code_i}>>"
                    code_i += 1
                    code_map[key] = m.group(0)
                    return key

                masked2 = cmd_rx.sub(cmd_repl, masked)

                # If masking removed all English (e.g., line is only commands), skip.
                if count_english_words(strip_latex(masked2)) < (4 if normalized_target_lang in ("ja", "zh", "ko") else 10):
                    continue

                candidates.append((i, masked2.rstrip("\r\n"), math_map, code_map))

            if not candidates:
                return text, 0

            # Cap to avoid runaway cost.
            candidates = candidates[:max_lines]
            payload = [c[1] for c in candidates]
            try:
                translated_lines = self.client.translate_markdown_lines(payload, target_lang=normalized_target_lang)
            except Exception:
                return text, 0

            replaced = 0
            for (idx, _masked_line, math_map, code_map), out_line in zip(candidates, translated_lines):
                if not (isinstance(out_line, str) and out_line.strip()):
                    continue
                restored = out_line
                for k, v in code_map.items():
                    restored = restored.replace(k, v)
                restored = _unmask_math(restored, math_map)
                # Preserve original line ending
                ending = ""
                if lines[idx].endswith("\r\n"):
                    ending = "\r\n"
                elif lines[idx].endswith("\n"):
                    ending = "\n"
                if restored + ending != lines[idx]:
                    lines[idx] = restored + ending
                    replaced += 1

            return "".join(lines), replaced

        # Fast pre-sweep: translate remaining English lines first (structure-preserving).
        # This often removes the need for expensive block-level repairs.
        latex_text, global_replaced_pre = _translate_english_lines_global(latex_text)
        stats["global_span_replaced_pre"] = int(global_replaced_pre)
        if global_replaced_pre > 0:
            self.logger.info(f"Final repair agent pre_sweep_span_replaced={global_replaced_pre}")

        scored: list[tuple[int, str]] = []

        # Consider \author{...} only for non-CJK targets. For CJK, affiliations and proper names
        # are often expected to remain in English; repairing them is costly and may harm layout.
        author_blocks = extract_command_block_balanced(latex_text, "author")
        seen_blocks: set[str] = set()
        if not is_cjk:
            for _, _, b in author_blocks:
                plain = strip_latex(b)
                en_words = count_english_words(plain)
                if en_words >= 4:
                    scored.append((1000 + en_words, b))
                    seen_blocks.add(b)

        # Split into paragraph-like blocks, keep original blocks for exact replace.
        blocks_raw = [b for b in re.split(r"\n\s*\n", latex_text) if b.strip()]
        for b in blocks_raw:
            # Never touch bibliography: it legitimately contains English names/titles.
            if "\\begin{thebibliography}" in b or "\\bibitem" in b or "\\bibliography" in b:
                continue
            if b in seen_blocks:
                continue
            plain = strip_latex(b)
            if is_english_heavy_block(plain):
                scored.append((count_english_words(plain), b))

        scored.sort(key=lambda x: x[0], reverse=True)
        suspicious = [b for _, b in scored[:max_blocks]]

        stats["candidates"] = int(len(scored))

        if scored:
            top = scored[0][0]
            self.logger.info(f"Final repair agent candidates: {len(scored)} blocks (top english_words={top})")
        else:
            self.logger.info("Final repair agent candidates: 0 blocks")

        stats["selected_blocks"] = int(len(suspicious))

        if not suspicious:
            return latex_text

        self.logger.warning(
            f"Final repair agent: detected {len(suspicious)} English-heavy blocks; attempting targeted repair..."
        )

        try:
            repaired = self.client.repair_latex_blocks(suspicious, target_lang=target_lang)
        except Exception as e:
            self.logger.warning(f"Final repair agent failed: {e}")
            return latex_text

        out = latex_text
        to_retry: list[str] = []
        retry_indices: list[int] = []
        applied = 0
        skipped = 0
        for idx, (orig, new) in enumerate(zip(suspicious, repaired)):
            if not (isinstance(new, str) and new.strip() and new != orig):
                continue
            if not is_balanced_braces(new):
                skipped += 1
                self.logger.warning(
                    f"Final repair agent: skip idx={idx} reason=unbalanced_braces preview={orig[:120].replace(chr(10), ' ')}"
                )
                to_retry.append(orig)
                retry_indices.append(idx)
                continue
            if orig.lstrip().startswith("\\author") and not new.lstrip().startswith("\\author"):
                skipped += 1
                self.logger.warning(
                    f"Final repair agent: skip idx={idx} reason=author_structure preview={orig[:120].replace(chr(10), ' ')}"
                )
                to_retry.append(orig)
                retry_indices.append(idx)
                continue
            out = out.replace(orig, new, 1)
            applied += 1

        stats["applied"] = int(applied)
        stats["skipped"] = int(skipped)

        if to_retry:
            try:
                repaired2 = self.client.repair_latex_blocks(to_retry, target_lang=target_lang, strict_braces=True)
            except Exception as e:
                self.logger.warning(f"Final repair agent strict retry failed: {e}")
                repaired2 = []
            retried_applied = 0
            still_failed: list[str] = []
            for orig, new in zip(to_retry, repaired2):
                if not (isinstance(new, str) and new.strip() and new != orig):
                    continue
                if not is_balanced_braces(new):
                    still_failed.append(orig)
                    continue
                if orig.lstrip().startswith("\\author") and not new.lstrip().startswith("\\author"):
                    still_failed.append(orig)
                    continue
                out = out.replace(orig, new, 1)
                retried_applied += 1

            # Final fallback: translate only plain-English spans inside still-failing blocks.
            fallback_replaced_total = 0
            for orig in still_failed:
                fixed_block, replaced = _fallback_translate_english_spans(orig)
                if replaced > 0 and fixed_block != orig:
                    out = out.replace(orig, fixed_block, 1)
                    fallback_replaced_total += replaced
            stats["strict_retry_applied"] = int(retried_applied)
            stats["fallback_spans_replaced"] = int(fallback_replaced_total)

            self.logger.info(
                f"Final repair agent applied={applied} skipped={skipped} strict_retry_applied={retried_applied} fallback_spans_replaced={fallback_replaced_total}"
            )
        else:
            self.logger.info(f"Final repair agent applied={applied} skipped={skipped}")

        # Final global sweep: translate remaining English spans without touching LaTeX structure.
        out2, global_replaced = _translate_english_lines_global(out)
        if global_replaced > 0:
            out = out2
            self.logger.info(f"Final repair agent global_span_replaced={global_replaced}")

        stats["global_span_replaced"] = int(global_replaced)

        return out

    def _is_resilient_mode(self) -> bool:
        v = str(os.environ.get("ROSETTA_TRANSLATION_RESUME", "1") or "1").strip().lower()
        return v not in ("0", "false", "no")

    def _has_table_structure_errors(self, errors: List[str]) -> bool:
        if not errors:
            return False
        needles = (
            "\\begin{tabular}",
            "ended by \\end{tabularx}",
            "ended by \\end{adjustbox}",
            "Missing \\cr inserted",
            "Misplaced \\cr",
        )
        joined = "\n".join(errors)
        return all(n in joined for n in ("\\begin{tabular}",)) and any(n in joined for n in needles[1:])

    def _extract_env_blocks(self, text: str, env_names: List[str]) -> List[Tuple[int, int, str]]:
        blocks: List[Tuple[int, int, str]] = []
        for env in env_names:
            begin_pat = r"\\begin\{" + re.escape(env) + r"\}"
            end_pat = r"\\end\{" + re.escape(env) + r"\}"
            combined = re.compile(f"(?:{begin_pat})|(?:{end_pat})")
            stack: List[int] = []
            spans: List[Tuple[int, int]] = []
            for m in combined.finditer(text):
                tok = m.group(0)
                if tok.startswith("\\begin"):
                    stack.append(m.start())
                else:
                    if stack:
                        start_pos = stack.pop()
                        spans.append((start_pos, m.end()))
            spans.sort(key=lambda x: x[0])
            for start_pos, end_pos in spans:
                blocks.append((start_pos, end_pos, text[start_pos:end_pos]))
        blocks.sort(key=lambda x: x[0])
        return blocks

    def _restore_tables_from_original(self, original_text: str, translated_text: str) -> str:
        envs = ["table", "table*"]
        orig_blocks = self._extract_env_blocks(original_text, envs)
        trans_blocks = self._extract_env_blocks(translated_text, envs)
        if not orig_blocks or not trans_blocks:
            return translated_text
        n = min(len(orig_blocks), len(trans_blocks))
        if n <= 0:
            return translated_text

        out = translated_text
        for i in range(n - 1, -1, -1):
            t_start, t_end, _ = trans_blocks[i]
            _, _, o_block = orig_blocks[i]
            out = out[:t_start] + o_block + out[t_end:]
        return out

    def _flatten_latex(self, base_dir: Path, content: str, depth: int = 0) -> str:
        r"""
        Recursively replace \input{...} and \include{...} with file content.
        """
        if depth > 10: # Prevent infinite recursion
            self.logger.warning("Max recursion depth reached in flattening.")
            return content
            
        # Pattern for input/include
        # Matches \input{filename} or \include{filename}
        # Filename might not have extension
        pattern = re.compile(r'\\(?:input|include)\{([^}]+)\}')
        
        def replace_input(match):
            filename = match.group(1)
            if not filename.endswith('.tex'):
                filename += '.tex'
                
            file_path = base_dir / filename
            
            if not file_path.exists():
                self.logger.warning(f"Input file not found: {file_path}")
                return match.group(0) # Keep original command
                
            try:
                with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                    sub_content = f.read()
                # Recursively flatten
                return self._flatten_latex(base_dir, sub_content, depth + 1)
            except Exception as e:
                self.logger.error(f"Error reading input file {file_path}: {e}")
                return match.group(0)
                
        return pattern.sub(replace_input, content)
    
    def _restore_preamble_if_missing(self, translated_text: str, original_content: str) -> str:
        """
        Восстанавливает преамбулу из оригинального документа, если она потеряна при переводе.
        
        GPT иногда теряет преамбулу при переводе больших чанков (>2000 токенов).
        Эта функция проверяет наличие \\documentclass и восстанавливает преамбулу
        из оригинального документа.
        
        Args:
            translated_text: Переведённый текст (может быть без преамбулы)
            original_content: Оригинальный LaTeX контент с преамбулой
            
        Returns:
            Текст с восстановленной преамбулой (если она была потеряна)
        """
        # Проверяем наличие \documentclass в переводе
        if re.search(r'\\documentclass', translated_text):
            # Преамбула на месте
            return translated_text
        
        self.logger.warning("Преамбула потеряна при переводе. Восстанавливаем из оригинала...")
        
        # Извлекаем преамбулу из оригинала (до \title или \begin{document})
        title_match = re.search(r'\\title\s*\{', original_content)
        begin_doc_match = re.search(r'\\begin\s*\{document\}', original_content)
        
        if title_match:
            preamble_end = title_match.start()
        elif begin_doc_match:
            preamble_end = begin_doc_match.start()
        else:
            self.logger.error("Не удалось найти конец преамбулы в оригинале")
            return translated_text
        
        original_preamble = original_content[:preamble_end]
        
        # Добавляем преамбулу к переводу
        restored_text = original_preamble + translated_text
        
        self.logger.info(f"Преамбула восстановлена ({len(original_preamble)} символов)")
        
        return restored_text
    
    def _validate_and_retry_translation(
        self, 
        original_text: str, 
        translated_text: str, 
        chunk_type: str,
        chunk_index: int,
        max_retries: int = 2,
        target_lang: str = "ru",
        stats: Optional[Dict[str, Any]] = None,
        client: Optional[OpenAIClient] = None,
        full_glossary: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Валидация перевода: проверяет, что текстовые части действительно переведены.
        
        Если обнаружены большие блоки непереведённого текста (английский без кириллицы),
        пытается перевести повторно с более явными инструкциями.
        
        Args:
            original_text: Оригинальный текст чанка (может содержать маски)
            translated_text: Переведённый текст
            chunk_type: Тип чанка (intro, section, etc.)
            chunk_index: Индекс чанка
            max_retries: Максимальное количество повторных попыток
            
        Returns:
            Переведённый текст (возможно, после повторной попытки)
        """
        # Извлекаем чистый текст (без LaTeX команд и масок) для анализа
        def extract_plain_text(text: str) -> str:
            # Удаляем маски <<...>>
            text = re.sub(r'<<[A-Z_]+_\d+>>', '', text)
            # Удаляем LaTeX команды
            text = re.sub(r'\\[a-zA-Z]+\*?(\[[^\]]*\])?(\{[^}]*\})*', '', text)
            # Удаляем скобки и спецсимволы
            text = re.sub(r'[{}\\$%&]', '', text)
            return text.strip()

        def extract_structural_titles(text: str) -> str:
            """Извлекает аргументы структурных команд (section/subsection/...) для проверки перевода."""
            titles: List[str] = []
            for m in re.finditer(
                r"\\(?:part|chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?\{([^}]*)\}",
                text,
            ):
                titles.append(m.group(1))
            return "\n".join(titles)
        
        def count_english_words(text: str) -> int:
            """Подсчёт маркеров английского (stopwords), чтобы не путать fr/de/es с EN."""
            words = [w.lower() for w in re.findall(r"\b[a-zA-Z]{2,}\b", text)]
            words = [w for w in words if not w.isupper()]
            en_stop = {
                "the", "and", "or", "of", "to", "in", "for", "with", "without", "from", "on", "at", "by",
                "as", "that", "this", "these", "those", "which", "who", "whom", "whose",
                "a", "an", "is", "are", "was", "were", "be", "been", "being",
                "it", "its", "we", "our", "us", "their", "they", "them",
                "can", "could", "may", "might", "will", "would", "should",
                "also", "such", "than", "then", "there", "here", "where", "when", "while",
            }
            return sum(1 for w in words if w in en_stop)

        def has_non_ascii_letters(text: str) -> bool:
            for ch in text:
                if ord(ch) < 128:
                    continue
                if ch.isalpha():
                    return True
            return False
        
        normalized_target_lang = self._normalize_target_lang(target_lang)
        target_lang_desc = normalized_target_lang

        used_client = client or self.client
        if stats is not None:
            stats["max_retries"] = int(max_retries)

        def has_cyrillic(text: str) -> bool:
            return bool(re.search(r'[а-яА-ЯёЁ]', text))

        def is_english_heavy_block(text: str) -> bool:
            t = re.sub(r"\s+", " ", text).strip()
            min_len = 80 if normalized_target_lang in ("ja", "zh", "ko") else 120
            if len(t) < min_len:
                return False
            letters = re.findall(r"[A-Za-z]", t)
            nonspace = re.findall(r"\S", t)
            if not nonspace:
                return False
            ascii_ratio = len(letters) / max(1, len(nonspace))
            if ascii_ratio < 0.25:
                return False
            en_hits = count_english_words(t)
            min_hits = 4 if normalized_target_lang in ("ja", "zh", "ko") else 6
            if en_hits >= min_hits:
                return True
            return False

        def find_english_heavy_blocks(text: str) -> List[str]:
            blocks: List[str] = []
            for b in re.split(r"\n\s*\n", text):
                b2 = b.strip()
                if is_english_heavy_block(b2):
                    blocks.append(b2)
            return blocks

        def looks_like_untranslated_heading(title_text: str) -> bool:
            t = re.sub(r"\s+", " ", title_text).strip().lower()
            if not t:
                return False
            t2 = re.sub(r"^[\d\W_]+", "", t).strip()
            common = {
                "abstract",
                "introduction",
                "background",
                "methods",
                "materials and methods",
                "method",
                "results",
                "discussion",
                "conclusion",
                "conclusions",
                "related work",
                "experiments",
                "acknowledgements",
                "acknowledgments",
                "references",
                "appendix",
            }
            if t2 in common:
                return True
            if normalized_target_lang == "ru":
                ascii_letters = len(re.findall(r"[A-Za-z]", t2))
                if ascii_letters >= 4:
                    return not has_cyrillic(t2)
            return False
        
        plain_original = extract_plain_text(original_text)
        plain_translated = extract_plain_text(translated_text)

        # Japanese style gate: enforce academic register (である調) and avoid polite forms / first-person plural.
        if normalized_target_lang in ("ja", "jp", "jpn"):
            try:
                t_plain = re.sub(r"\s+", " ", plain_translated).strip()
                polite_rx = re.compile(r"(です|ます|でした|ません|でしょう|でした)")
                we_rx = re.compile(r"(私たち|我々)")
                has_polite = bool(polite_rx.search(t_plain))
                has_we = bool(we_rx.search(t_plain))
                if has_polite or has_we:
                    self.logger.warning(
                        f"Chunk {chunk_index} ({chunk_type}): Japanese style issues detected polite={has_polite} we={has_we}. Retrying with strict academic register..."
                    )
                    # Cap retries to avoid extra cost explosions.
                    style_retries = min(max_retries, 2)
                    for retry in range(style_retries):
                        try:
                            time.sleep(1)
                            enhanced_text = (
                                "IMPORTANT: Translate ALL text below to Japanese using academic/scientific register (である調). "
                                "DO NOT use polite forms (です/ます/でした/ません/でしょう). "
                                "Avoid first-person pronouns and 'we' (私たち/我々); use impersonal scientific phrasing. "
                                "Return LaTeX only.\n\n"
                                + original_text
                            )
                            result = used_client.translate_latex(
                                enhanced_text,
                                target_lang=normalized_target_lang,
                                full_glossary=full_glossary,
                            )
                            candidate = result.translated_content
                            cand_plain = extract_plain_text(candidate)
                            cand_plain = re.sub(r"\s+", " ", cand_plain).strip()
                            if (not polite_rx.search(cand_plain)) and (not we_rx.search(cand_plain)):
                                return candidate
                        except Exception as e:
                            self.logger.warning(f"Japanese style retry failed: {e}")

                sentences = [s.strip() for s in re.split(r"[。．\.]", t_plain) if s.strip()]
                dearu_count = len(re.findall(r"(である|であり|であった)", t_plain))
                starts_ha = 0
                for s in sentences[:200]:
                    if re.match(r"^[^\s]{1,24}は", s):
                        starts_ha += 1
                connector_count = len(re.findall(r"(したがって|従って|そのため|なぜなら|つまり)", t_plain))
                if (
                    len(sentences) >= 18
                    and dearu_count >= 10
                    and starts_ha >= 8
                    and connector_count >= 2
                    and max_retries >= 1
                ):
                    self.logger.warning(
                        f"Chunk {chunk_index} ({chunk_type}): Japanese translationese signals detected (dearu={dearu_count} starts_ha={starts_ha} connectors={connector_count}). Retrying with more natural Japanese scientific style..."
                    )
                    try:
                        time.sleep(1)
                        enhanced_text = (
                            "IMPORTANT: Translate ALL text below to Japanese in academic/scientific style (である調), but avoid literal English sentence rhythm. "
                            "Prefer nominalization (名詞化), passive/impersonal constructions, and compressed syntax (構文の圧縮). "
                            "Avoid repeating 'Xは...である' across consecutive sentences; vary with 〜とされる/〜と考えられる/〜が示される/〜となる. "
                            "Soften explicit connectors like 'therefore/because/in order to' using その結果/これにより/〜ことから or omit when clear. "
                            "Reduce redundant repetition when referent is clear (当該/同層/本手法/これら). "
                            "Keep LaTeX commands, math, and citations unchanged. Return LaTeX only.\n\n"
                            + original_text
                        )
                        result = used_client.translate_latex(
                            enhanced_text,
                            target_lang=normalized_target_lang,
                            full_glossary=full_glossary,
                        )
                        candidate = result.translated_content
                        cand_plain = extract_plain_text(candidate)
                        cand_plain = re.sub(r"\s+", " ", cand_plain).strip()
                        cand_sentences = [s.strip() for s in re.split(r"[。．\.]", cand_plain) if s.strip()]
                        cand_dearu = len(re.findall(r"(である|であり|であった)", cand_plain))
                        cand_starts_ha = 0
                        for s in cand_sentences[:200]:
                            if re.match(r"^[^\s]{1,24}は", s):
                                cand_starts_ha += 1
                        if (
                            (not polite_rx.search(cand_plain))
                            and (not we_rx.search(cand_plain))
                            and (cand_dearu < dearu_count or cand_starts_ha < starts_ha)
                        ):
                            return candidate
                    except Exception as e:
                        self.logger.warning(f"Japanese naturalness retry failed: {e}")
            except Exception as e:
                self.logger.warning(f"Japanese style gate failed: {e}")

        # Отдельно проверяем заголовки структурных команд — они часто короткие и пролезают мимо общей эвристики
        original_titles = extract_structural_titles(original_text)
        translated_titles = extract_structural_titles(translated_text)
        if original_titles:
            for title_line in translated_titles.splitlines():
                if looks_like_untranslated_heading(title_line):
                    self.logger.warning(
                        f"Чанк {chunk_index} ({chunk_type}): обнаружен непереведённый заголовок '{title_line}'. Повторяем перевод..."
                    )
                    for retry in range(max_retries):
                        try:
                            time.sleep(1)
                            enhanced_text = (
                                f"IMPORTANT: Translate ALL text below to {target_lang_desc}, including ALL section/subsection titles. "
                                "Do not leave English headings like Methods/Results unchanged.\n\n"
                                + original_text
                            )
                            result = used_client.translate_latex(
                                enhanced_text,
                                target_lang=normalized_target_lang,
                                full_glossary=full_glossary,
                            )
                            new_translated = result.translated_content
                            new_titles = extract_structural_titles(new_translated)
                            bad = any(looks_like_untranslated_heading(t) for t in new_titles.splitlines())
                            if not bad:
                                return new_translated
                        except Exception as e:
                            self.logger.warning(f"Ошибка при повторном переводе заголовков: {e}")
                    break
        
        original_english_words = count_english_words(plain_original)
        translated_english_words = count_english_words(plain_translated)
        translated_has_cyrillic = has_cyrillic(plain_translated)

        # Generic rule (any non-English target): if large English blocks remain, retry.
        if normalized_target_lang != "en":
            heavy_blocks = find_english_heavy_blocks(plain_translated)
            if heavy_blocks:
                # Stronger signal: leftover block appears verbatim (or near) in original.
                orig_norm = re.sub(r"\s+", " ", plain_original).strip()
                hit = False
                for b in heavy_blocks[:3]:
                    b_norm = re.sub(r"\s+", " ", b).strip()
                    if b_norm and b_norm[:80] in orig_norm:
                        hit = True
                        break

                if hit or (translated_english_words >= max(40, int(original_english_words * 0.6)) and original_english_words > 30):
                    self.logger.warning(
                        f"Чанк {chunk_index} ({chunk_type}): обнаружены большие англ. блоки ({len(heavy_blocks)}). Повторяем перевод..."
                    )
                    for retry in range(max_retries):
                        try:
                            time.sleep(1)
                            enhanced_text = (
                                f"IMPORTANT: Translate ALL human-readable text below to {target_lang_desc}. "
                                "Do not leave any English paragraphs or sentences untranslated. "
                                "Keep LaTeX commands, math, citations, and acronyms (GPU, Transformer) intact. "
                                "Return LaTeX only.\n\n"
                                + original_text
                            )
                            result = used_client.translate_latex(
                                enhanced_text,
                                target_lang=normalized_target_lang,
                                full_glossary=full_glossary,
                            )
                            new_translated = result.translated_content
                            new_plain = extract_plain_text(new_translated)
                            if not find_english_heavy_blocks(new_plain):
                                return new_translated
                        except Exception as e:
                            self.logger.warning(f"Ошибка при повторном переводе (англ. блоки): {e}")

        # Ловим случаи, когда большой кусок текста остался на английском, но общий счётчик слов не дотянул до порога
        # (часто 1-2 абзаца внутри русского текста)
        if normalized_target_lang == "ru" and original_english_words > 10 and translated_english_words >= 10 and not translated_has_cyrillic:
            self.logger.warning(
                f"Чанк {chunk_index} ({chunk_type}): похоже, перевод не выполнен (нет кириллицы, англ. слов={translated_english_words}). Повторяем перевод..."
            )
            for retry in range(max_retries):
                try:
                    time.sleep(1)
                    enhanced_text = (
                        f"IMPORTANT: Translate ALL text below to {target_lang_desc}. "
                        "Return LaTeX only.\n\n"
                        + original_text
                    )
                    result = used_client.translate_latex(
                        enhanced_text,
                        target_lang=normalized_target_lang,
                        full_glossary=full_glossary,
                    )
                    new_translated = result.translated_content
                    new_plain = extract_plain_text(new_translated)
                    if has_cyrillic(new_plain):
                        return new_translated
                except Exception as e:
                    self.logger.warning(f"Ошибка при повторном переводе: {e}")
        
        # Если в оригинале было много английского текста, а в переводе мало кириллицы
        # и много английского - вероятно, часть текста не переведена
        if normalized_target_lang == "ru" and original_english_words > 20 and translated_english_words > original_english_words * 0.7:
            if not translated_has_cyrillic or translated_english_words > 50:
                self.logger.warning(
                    f"Чанк {chunk_index} ({chunk_type}): обнаружен непереведённый текст "
                    f"({translated_english_words} англ. слов). Повторяем перевод..."
                )
                
                # Повторный перевод с усиленными инструкциями
                for retry in range(max_retries):
                    try:
                        time.sleep(1)  # Небольшая пауза перед повторной попыткой
                        
                        # Добавляем явную инструкцию
                        enhanced_text = (
                            f"IMPORTANT: Translate ALL text below to {target_lang_desc}. "
                            "Do not leave any English sentences untranslated.\n\n"
                            + original_text
                        )
                        
                        result = used_client.translate_latex(
                            enhanced_text,
                            target_lang=normalized_target_lang,
                            full_glossary=full_glossary,
                        )
                        new_translated = result.translated_content
                        
                        # Проверяем улучшение
                        new_plain = extract_plain_text(new_translated)
                        new_english_words = count_english_words(new_plain)
                        
                        if new_english_words < translated_english_words:
                            self.logger.info(
                                f"Повторный перевод чанка {chunk_index}: "
                                f"англ. слов {translated_english_words} -> {new_english_words}"
                            )
                            return new_translated
                        
                    except Exception as e:
                        self.logger.warning(f"Ошибка при повторном переводе: {e}")
                
                self.logger.warning(
                    f"Не удалось улучшить перевод чанка {chunk_index} после {max_retries} попыток"
                )
        
        return translated_text
    
    def _add_cyrillic_support(self, latex_content: str) -> str:
        """
        Добавляет поддержку русского языка в преамбулу LaTeX.
        
        Универсальный подход: не добавляет новые пакеты если они уже есть,
        а только помечает документ для модификации в постпроцессоре.
        Постпроцессор затем модифицирует существующие пакеты.
        
        Это позволяет работать с любыми шаблонами LaTeX.
        """
        # Проверяем наличие кириллических символов
        has_cyrillic = bool(re.search(r'[а-яА-ЯёЁ]', latex_content))
        
        if not has_cyrillic:
            self.logger.info("Кириллические символы не обнаружены, пропускаем добавление поддержки")
            return latex_content
            
        self.logger.info("Обнаружены кириллические символы, подготавливаем поддержку")
        
        # Находим строку \documentclass
        documentclass_match = re.search(r'\\documentclass(\[.*?\])?\{.*?\}', latex_content)
        if not documentclass_match:
            self.logger.warning("Не найдена строка \\documentclass")
            return latex_content
        
        # Позиция для вставки пакетов после \documentclass
        insert_pos = documentclass_match.end()
        
        # Проверяем существующие пакеты
        has_any_fontenc = bool(re.search(r'\\usepackage.*?\{fontenc\}', latex_content))
        has_any_babel = bool(re.search(r'\\usepackage.*?\{babel\}', latex_content))
        
        packages_to_add = []
        
        # Добавляем cmap (безопасно, не конфликтует)
        if r'\usepackage{cmap}' not in latex_content:
            packages_to_add.append(r'\usepackage{cmap}')
        
        # Если fontenc НЕ загружен вообще — добавляем с T2A
        if not has_any_fontenc:
            packages_to_add.append(r'\usepackage[T2A]{fontenc}')
        # Иначе — постпроцессор модифицирует существующий
        
        # Если babel НЕ загружен вообще — добавляем с russian
        if not has_any_babel:
            packages_to_add.append(r'\usepackage[russian]{babel}')
        # Иначе — постпроцессор добавит russian к существующему
        
        # НЕ добавляем sectsty автоматически — слишком много несовместимых шаблонов
        # Жирные заголовки будут работать через babel russian
        
        # Вставляем только те пакеты, которых нет вообще
        if packages_to_add:
            packages_block = '\n' + '\n'.join(packages_to_add) + '\n'
            latex_content = latex_content[:insert_pos] + packages_block + latex_content[insert_pos:]
            self.logger.info(f"Добавлено {len(packages_to_add)} базовых пакетов кириллицы")

        # babel(russian) makes '"' an active character (shorthand), which breaks tikz/tikzcd syntax.
        # Some classes may not load babel even if we pass options; make this safe.
        if r'\AtBeginDocument{\ifdefined\shorthandoff\shorthandoff{"}\fi}' not in latex_content and r'\shorthandoff{"}' not in latex_content:
            latex_content = self._insert_before_document(
                latex_content,
                '\\AtBeginDocument{\\ifdefined\\shorthandoff\\shorthandoff{"}\\fi}\n',
            )
            self.logger.info('Добавлено: \\AtBeginDocument{\\shorthandoff{"}} (совместимость с TikZ/tikzcd)')

        # listings does not handle UTF-8 reliably by default (especially external *.listing files).
        # If the document uses listings/tcolorbox listings, enable UTF-8 support.
        uses_listings = bool(
            re.search(r"\\usepackage(?:\[[^\]]*\])?\{listings\}", latex_content)
            or re.search(r"\\tcbuselibrary\{listings\}", latex_content)
            or re.search(r"\\begin\{lstlisting\}", latex_content)
            or re.search(r"\\lstset\s*\{", latex_content)
            or re.search(r"\\lstinputlisting\b", latex_content)
        )

        if uses_listings and r'\usepackage{listingsutf8}' not in latex_content:
            latex_content = self._insert_before_document(latex_content, "\\usepackage{listingsutf8}\n")
            self.logger.info('Добавлено: \\usepackage{listingsutf8} (UTF-8 для listings)')

        if uses_listings:
            if "\\lstset{inputencoding=utf8" not in latex_content and "\\lstset{ inputencoding=utf8" not in latex_content:
                latex_content = self._insert_before_document(
                    latex_content,
                    "\\lstset{inputencoding=utf8,extendedchars=true}\n",
                )
                self.logger.info('Добавлено: \\lstset{inputencoding=utf8,...} (UTF-8 для listings)')

            # Intentionally do not inject tcolorbox listing-options keys here: they are version-dependent
            # and can break if the listings library is disabled later by surgical fixes.

        if has_any_fontenc:
            self.logger.info("fontenc уже загружен — будет модифицирован в постпроцессоре")
        if has_any_babel:
            self.logger.info("babel уже загружен — будет модифицирован в постпроцессоре")

        return latex_content

    def _add_arxiv_stamp(self, latex_content: str, article: Optional[ArxivArticle]) -> str:
        """
        Insert a vertical arXiv stamp (ID, category, date) similar to the original PDF.
        """
        if not article:
            return latex_content

        stamp_text = self._build_arxiv_stamp_text(article)
        if not stamp_text:
            return latex_content

        content = latex_content
        preamble_snippets = []

        if r'\usepackage{eso-pic}' not in content:
            preamble_snippets.append(r'\usepackage{eso-pic}')
        if r'\usepackage{xcolor}' not in content:
            preamble_snippets.append(r'\usepackage{xcolor}')

        if r'\ArxivStamp' not in content:
            stamp_macro = (
                r'\newcommand{\ArxivStamp}[1]{%' + "\n"
                r'  \AddToShipoutPictureBG*{%' + "\n"
                r'    \AtPageLowerLeft{%' + "\n"
                r'      \put(28,0){%' + "\n"
                r'        \raisebox{0.5\paperheight}{\makebox(0,0){\rotatebox{90}{\rmfamily\huge\color{gray} #1}}}%' + "\n"
                r'      }%' + "\n"
                r'    }%' + "\n"
                r'  }%' + "\n"
                r'}' + "\n"
            )
            preamble_snippets.append(stamp_macro)

        if preamble_snippets:
            content = self._insert_before_document(content, "\n".join(preamble_snippets) + "\n")

        target_call = f"\\ArxivStamp{{{stamp_text}}}"
        if target_call not in content:
            content = self._insert_before_document(content, target_call + "\n")

        return content

    def _build_arxiv_stamp_text(self, article: ArxivArticle) -> Optional[str]:
        base_id = article.arxiv_id or ""
        version = article.version or ""
        if not base_id:
            return None

        if version and not base_id.endswith(version):
            arxiv_part = f"{base_id}{version}"
        else:
            arxiv_part = base_id

        category = article.primary_category or (article.categories[0] if article.categories else "")
        date_obj = article.updated_date or article.published_date
        date_text = self._format_stamp_date(date_obj)

        parts = [f"arXiv:{arxiv_part}"]
        if category:
            parts.append(f"[{category}]")
        if date_text:
            parts.append(date_text)

        return " ".join(parts).strip()

    def _format_stamp_date(self, dt: Optional[datetime]) -> str:
        if not dt:
            return ""
        date_part = dt.date() if hasattr(dt, "date") else dt
        month_part = date_part.strftime("%b %Y")
        return f"{date_part.day} {month_part}"

    def _patch_style_files(self, work_dir: Path):
        """
        Patch .sty files to remove forced font overrides that break Cyrillic.
        Specifically targets nips_2017.sty and similar.
        """
        self.logger.info("Scanning for style files to patch...")
        for sty_file in work_dir.glob("*.sty"):
            try:
                with open(sty_file, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                
                modified = False
                
                # Comment out font overrides
                # \renewcommand{\rmdefault}{ptm} -> %\renewcommand{\rmdefault}{ptm}
                if r'\renewcommand{\rmdefault}{ptm}' in content:
                    content = content.replace(r'\renewcommand{\rmdefault}{ptm}', r'%\renewcommand{\rmdefault}{ptm}')
                    modified = True
                    
                if r'\renewcommand{\sfdefault}{phv}' in content:
                    content = content.replace(r'\renewcommand{\sfdefault}{phv}', r'%\renewcommand{\sfdefault}{phv}')
                    modified = True
                    
                if modified:
                    self.logger.info(f"Patching style file: {sty_file.name}")
                    with open(sty_file, 'w', encoding='utf-8') as f:
                        f.write(content)
            except Exception as e:
                self.logger.warning(f"Failed to patch {sty_file.name}: {e}")

    def _insert_before_document(self, latex_content: str, snippet: str) -> str:
        """
        Insert snippet right before \begin{document}. If not found, append at end.
        """
        doc_start = latex_content.find(r'\begin{document}')
        if doc_start == -1:
            separator = "" if latex_content.endswith("\n") else "\n"
            return latex_content + separator + snippet
        return latex_content[:doc_start] + snippet + latex_content[doc_start:]
