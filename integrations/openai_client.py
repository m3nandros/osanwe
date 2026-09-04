"""
OpenAI API client for Rosetta v2.

Handles interaction with GPT-4o-mini for translation:
- Optimized prompt construction
- Glossary filtering and formatting
- Document preprocessing (removing tikz, etc.)
- Token counting and cost monitoring
- Error handling and retries
"""

import json
import os
import re
import time
import hashlib
from pathlib import Path
from typing import Dict, Optional, Any, Tuple, List
from dataclasses import dataclass, field

from openai import OpenAI
from openai import APIError, RateLimitError, APITimeoutError, APIConnectionError

from config import Config
from utils.logger import get_logger
from utils.glossary import find_relevant_terms, format_glossary_compact, select_glossary_for_language

logger = get_logger(__name__)


@dataclass
class TranslationResult:
    """
    Result of LaTeX translation.
    
    Attributes:
        translated_content: Translated LaTeX content
        input_tokens: Number of input tokens used
        output_tokens: Number of output tokens generated
        cost_usd: Total cost in USD
        model: Model used for translation
        metadata: Additional metadata about the translation
    """
    translated_content: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class OpenAIClient:
    """
    OpenAI API client for translating LaTeX documents.
    
    Part 1: Basic structure with initialization, translation, and error handling.
    """
    
    # Pricing for GPT-4o-mini (as of 2024)
    INPUT_PRICE_PER_1K_TOKENS = 0.15 / 1000  # $0.15 per 1M tokens = $0.00015 per 1K
    OUTPUT_PRICE_PER_1K_TOKENS = 0.60 / 1000  # $0.60 per 1M tokens = $0.0006 per 1K
    
    def __init__(self):
        """Initialize OpenAI client with configuration."""
        self.config = Config.get_openai_config()

        self.cache_mode = (os.getenv("ROSETTA_TRANSLATION_CACHE_MODE", "off") or "off").strip().lower()
        cache_dir_env = os.getenv("ROSETTA_TRANSLATION_CACHE_DIR", "")
        if cache_dir_env:
            self.cache_dir = Path(cache_dir_env)
        else:
            self.cache_dir = Config.OUTPUT_DIR / "translation_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        api_key = self.config.get("api_key")
        if self.cache_mode != "replay" and not api_key:
            raise ValueError(
                "OPENAI_API_KEY is not set. Create a file '.openai_key' in the project root "
                "or export OPENAI_API_KEY before running the pipeline."
            )

        self.client = OpenAI(api_key=api_key) if api_key else None
        self.model = self.config["model"]
        self.temperature = self.config["temperature"]

        # Handle max_tokens type conversion
        max_tokens_config = self.config["max_tokens"]
        if max_tokens_config and max_tokens_config != "auto":
            try:
                val = int(max_tokens_config)
                # Cap at 16000 for gpt-4o-mini to avoid errors
                if val > 16000:
                    get_logger(__name__).warning(f"max_tokens {val} is too large, capping at 16000")
                    val = 16000
                self.max_tokens = val
            except ValueError:
                get_logger(__name__).warning(f"Invalid max_tokens: {max_tokens_config}, defaulting to auto")
                self.max_tokens = "auto"
        else:
            self.max_tokens = "auto"

        self.logger = get_logger(__name__)
        self.logger.info(f"OpenAI client initialized with model: {self.model}")

        # Part 3: Caching
        self._system_prompt_cache: Dict[str, str] = {}
        self._glossary_format_cache: Dict[str, str] = {}

        # Part 3: Retry configuration
        self.max_retries = 6  # Increased from 3 to 6 for better reliability
        self.initial_retry_delay = 2.0  # Increased from 1.0 to 2.0 seconds
        self.max_retry_delay = 120.0  # Increased from 60.0 to 120.0 seconds
        self.timeout = 600  # Increased from 300 to 600 seconds (10 minutes) for large documents

        v = (os.getenv("ROSETTA_OPENAI_TIMEOUT_SEC", "") or "").strip()
        if v:
            try:
                self.timeout = max(1, int(float(v)))
            except Exception:
                pass

        v = (os.getenv("ROSETTA_OPENAI_MAX_RETRIES", "") or "").strip()
        if v:
            try:
                self.max_retries = max(0, int(float(v)))
            except Exception:
                pass

        v = (os.getenv("ROSETTA_OPENAI_INITIAL_RETRY_DELAY_SEC", "") or "").strip()
        if v:
            try:
                self.initial_retry_delay = max(0.0, float(v))
            except Exception:
                pass

        v = (os.getenv("ROSETTA_OPENAI_MAX_RETRY_DELAY_SEC", "") or "").strip()
        if v:
            try:
                self.max_retry_delay = max(0.0, float(v))
            except Exception:
                pass


    def build_pdf_text_system_prompt(self, target_lang: str = "ru") -> str:
        lang_name = self._normalize_target_lang_for_prompt(target_lang)
        is_ja = str(target_lang or "").strip().lower() in ("ja", "jp", "jpn")
        ja_style = (
            "\nJAPANESE ACADEMIC STYLE:\n"
            "- Use academic/scientific register (である調).\n"
            "- DO NOT use polite forms (です/ます/でした/ません).\n"
            "- Avoid first-person pronouns and 'we' (私たち/我々); prefer impersonal phrasing.\n"
        ) if is_ja else ""
        return (
            f"You are a professional scientific translator. Translate the provided PDF-extracted text blocks into {lang_name}.\n"
            "CRITICAL RULES:\n"
            "1. Preserve meaning and academic tone. Do NOT summarize or shorten.\n"
            "2. Preserve citations like [12], (Smith et al., 2020), and reference tokens exactly.\n"
            "3. Do NOT translate or transliterate proper names of people, organizations, companies, labs, universities, datasets, software/tool names. Keep them EXACTLY.\n"
            "4. Keep acronyms like GPU, BLEU, WMT, Transformer unchanged.\n"
            "5. Do NOT change URLs, DOIs, emails, arXiv IDs, code identifiers.\n"
            "6. Preserve numbers, units, and math symbols.\n"
            "7. You will receive multiple blocks wrapped with markers @@@NNNN@@@ ... @@@ENDNNNN@@@.\n"
            "8. Return the SAME markers and the SAME number of blocks, in any order.\n"
            "9. Output ONLY the marked blocks, nothing else."
            + ja_style
        )


    def translate_text_blocks(self, blocks: List[str], target_lang: str = "ru") -> List[str]:
        if not blocks:
            return []

        system_prompt = self.build_pdf_text_system_prompt(target_lang=target_lang)

        parts: list[str] = []
        for i, b in enumerate(blocks):
            parts.append(f"@@@{i:04d}@@@\n{b}\n@@@END{i:04d}@@@")
        user_message = "Translate these PDF text blocks:\n\n" + "\n\n".join(parts)

        cache_key = self._cache_key(system_prompt, user_message)
        if self.cache_mode == "replay":
            cached = self._load_cached_result(cache_key)
            if cached is None:
                raise ValueError(f"Translation cache miss for key={cache_key}")
            translated_content = cached.translated_content
        else:
            if self.cache_mode == "record":
                cached = self._load_cached_result(cache_key)
                if cached is not None:
                    translated_content = cached.translated_content
                else:
                    if self.client is None:
                        raise ValueError("OpenAI client is not initialized (missing API key)")
                    response = self._make_api_request_with_retry(system_prompt, user_message)
                    translated_content = self._extract_translated_content(response)
                    input_tokens = response.usage.prompt_tokens
                    output_tokens = response.usage.completion_tokens
                    cost = self._calculate_cost(input_tokens, output_tokens)
                    result = TranslationResult(
                        translated_content=translated_content,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cost_usd=cost,
                        model=self.model,
                        metadata={"format": "pdf_text_blocks", "target_lang": target_lang, "blocks": len(blocks)},
                    )
                    self._save_cached_result(cache_key, result, system_prompt, user_message)
            else:
                if self.client is None:
                    raise ValueError("OpenAI client is not initialized (missing API key)")
                response = self._make_api_request_with_retry(system_prompt, user_message)
                translated_content = self._extract_translated_content(response)

        parsed: dict[int, str] = {}
        rx = re.compile(r"@@@(\d{4})@@@\s*(.*?)\s*@@@END\1@@@", re.DOTALL)
        for m in rx.finditer(translated_content or ""):
            parsed[int(m.group(1))] = m.group(2)

        out: list[str] = []
        for i, original in enumerate(blocks):
            v = parsed.get(i)
            out.append(v.strip() if isinstance(v, str) and v.strip() else original)
        return out

    def _cache_key(self, system_prompt: str, user_message: str) -> str:
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "system": system_prompt,
            "user": user_message,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _load_cached_result(self, key: str) -> Optional[TranslationResult]:
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return TranslationResult(
                translated_content=str(data.get("translated_content", "")),
                input_tokens=int(data.get("input_tokens", 0) or 0),
                output_tokens=int(data.get("output_tokens", 0) or 0),
                cost_usd=float(data.get("cost_usd", 0.0) or 0.0),
                model=str(data.get("model", self.model)),
                metadata=dict(data.get("metadata", {}) or {}),
            )
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Failed to read translation cache {path}: {e}")
            return None

    def _save_cached_result(self, key: str, result: TranslationResult, system_prompt: str, user_message: str):
        path = self._cache_path(key)
        payload = {
            "translated_content": result.translated_content,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cost_usd": result.cost_usd,
            "model": result.model,
            "metadata": result.metadata,
            "request": {
                "model": self.model,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            },
            "messages": {
                "system": system_prompt,
                "user": user_message,
            },
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Failed to write translation cache {path}: {e}")

    def _normalize_target_lang_for_prompt(self, target_lang: str) -> str:
        t = (target_lang or "").strip().lower()
        if not t:
            return "Russian"
        mapping = {
            "ru": "Russian",
            "en": "English",
            "fr": "French",
            "de": "German",
            "es": "Spanish",
            "it": "Italian",
            "pt": "Portuguese",
            "nl": "Dutch",
            "pl": "Polish",
            "tr": "Turkish",
            "uk": "Ukrainian",
            "cs": "Czech",
            "sv": "Swedish",
            "no": "Norwegian",
            "da": "Danish",
            "fi": "Finnish",
            "el": "Greek",
            "zh": "Chinese",
            "ja": "Japanese",
            "jp": "Japanese",
            "jpn": "Japanese",
            "ko": "Korean",
        }
        if t in mapping:
            return mapping[t]
        return target_lang
    
    def translate_latex(
        self,
        latex_content: str,
        glossary: Optional[Dict[str, Any]] = None,
        full_glossary: Optional[Dict[str, Any]] = None,
        target_lang: str = "ru",
    ) -> TranslationResult:
        """
        Translate LaTeX content using GPT-4o-mini.
        
        Args:
            latex_content: LaTeX content to translate (already masked if needed)
            glossary: Optional pre-filtered dictionary of terms
            full_glossary: Optional full glossary dictionary
            
        Returns:
            TranslationResult with translated content and metadata
        """
        self.logger.info("Starting LaTeX translation...")
        
        # Filter glossary if full glossary provided
        if full_glossary and not glossary:
            self.logger.info("Filtering glossary for relevant terms...")
            glossary = find_relevant_terms(full_glossary, latex_content)

        glossary_for_prompt = select_glossary_for_language(glossary or {}, target_lang)
        
        # Build optimized system prompt (with caching)
        system_prompt = self._build_optimized_system_prompt(glossary_for_prompt, target_lang=target_lang)
        
        # Build user message
        user_message = self._build_user_message(latex_content, target_lang=target_lang)

        cache_key = self._cache_key(system_prompt, user_message)
        if self.cache_mode == "replay":
            cached = self._load_cached_result(cache_key)
            if cached is None:
                raise ValueError(f"Translation cache miss for key={cache_key}")
            if self.logger:
                self.logger.info(f"Using cached translation (replay) key={cache_key}")
            return cached

        if self.cache_mode == "record":
            cached = self._load_cached_result(cache_key)
            if cached is not None:
                if self.logger:
                    self.logger.info(f"Using cached translation (record) key={cache_key}")
                return cached

        # Detailed token counting
        token_breakdown = self._estimate_token_breakdown(system_prompt, user_message, glossary_for_prompt)
        
        if self.client is None:
            raise ValueError("OpenAI client is not initialized (missing API key)")

        response = self._make_api_request_with_retry(system_prompt, user_message)
        
        # Extract translated content
        translated_content = self._extract_translated_content(response)
        
        # Calculate costs
        input_tokens = response.usage.prompt_tokens
        output_tokens = response.usage.completion_tokens
        cost = self._calculate_cost(input_tokens, output_tokens)
        
        # Update token breakdown
        token_breakdown["actual_input_tokens"] = input_tokens
        token_breakdown["actual_output_tokens"] = output_tokens
        token_breakdown["actual_cost_usd"] = cost
        
        self.logger.info(
            f"Translation complete: {input_tokens} input tokens, "
            f"{output_tokens} output tokens, ${cost:.4f}"
        )
        
        if self.logger:
            self._log_token_breakdown(token_breakdown)
        
        result = TranslationResult(
            translated_content=translated_content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            model=self.model,
            metadata={
                "token_breakdown": token_breakdown,
                "glossary_terms": len(glossary_for_prompt) if glossary_for_prompt else 0,
            }
        )

        if self.cache_mode == "record":
            self._save_cached_result(cache_key, result, system_prompt, user_message)

        return result

    def build_latex_repair_system_prompt(self, target_lang: str = "ru", strict_braces: bool = False) -> str:
        lang_name = self._normalize_target_lang_for_prompt(target_lang)
        is_ja = str(target_lang or "").strip().lower() in ("ja", "jp", "jpn")
        ja_style = (
            "\nJAPANESE ACADEMIC STYLE:\n"
            "- Use academic/scientific register (である調).\n"
            "- DO NOT use polite forms (です/ます/でした/ません).\n"
            "- Avoid first-person pronouns and 'we' (私たち/我々); prefer impersonal phrasing.\n"
            "- Reduce translationese: prefer nominalization (名詞化), passive/impersonal constructions, and compressed syntax (構文の圧縮).\n"
            "- Avoid repetitive cadence like 'XはYである' in consecutive sentences; vary with 〜とされる/〜と考えられる/〜が示される.\n"
            "- Soften explicit English-style connectors (therefore/because/in order to): prefer その結果/これにより/〜ことから or omit when clear.\n"
            "- Avoid excessive term repetition when reference is clear (当該/同層/本手法/これら).\n"
        ) if is_ja else ""
        strict = (
            "\nSTRICT BRACES MODE:\n"
            "- Do NOT add/remove any '{' or '}' characters.\n"
            "- Do NOT change brace nesting; keep braces exactly as in the input snippet.\n"
            "- Your output MUST have balanced braces.\n"
        ) if strict_braces else ""
        return (
            f"You are a repair agent. Your task is to rewrite LaTeX snippets so that ALL human-readable English text is translated to {lang_name}.\n"
            "CRITICAL RULES:\n"
            "1. Preserve LaTeX syntax exactly: commands, environments, braces, optional args.\n"
            "2. Do NOT change math ($...$, \\[...\\], \\begin{equation}...\\end{equation}) or citation/ref commands.\n"
            "3. Do NOT change file paths, labels, keys, or URLs.\n"
            "4. Translate text inside: section titles, captions, footnotes/\\thanks{}, author affiliations/organizations, itemize/enumerate text, normal paragraphs.\n"
            "5. Keep ALL proper names unchanged, including person names, organization/company/university/lab names, and emails. Do NOT translate or transliterate organization names (e.g., keep 'Google Brain', 'Google Research', 'University of Toronto' exactly as-is).\n"
            "6. Keep acronyms like GPU/Transformer unchanged.\n"
            "6. You will receive multiple snippets wrapped with markers @@@NNNN@@@ ... @@@ENDNNNN@@@.\n"
            "7. Return the SAME markers and the SAME number of snippets.\n"
            "8. Output ONLY the snippets with markers, nothing else."
            + ja_style
            + strict
        )

    def repair_latex_blocks(self, blocks: list[str], target_lang: str = "ru", strict_braces: bool = False) -> list[str]:
        if not blocks:
            return []

        system_prompt = self.build_latex_repair_system_prompt(target_lang=target_lang, strict_braces=strict_braces)

        parts: list[str] = []
        for i, b in enumerate(blocks):
            parts.append(f"@@@{i:04d}@@@\n{b}\n@@@END{i:04d}@@@")
        user_message = "Repair these LaTeX snippets:\n\n" + "\n\n".join(parts)

        cache_key = self._cache_key(system_prompt, user_message)
        if self.cache_mode == "replay":
            cached = self._load_cached_result(cache_key)
            if cached is None:
                raise ValueError(f"Translation cache miss for key={cache_key}")
            translated_content = cached.translated_content
        else:
            if self.cache_mode == "record":
                cached = self._load_cached_result(cache_key)
                if cached is not None:
                    translated_content = cached.translated_content
                else:
                    if self.client is None:
                        raise ValueError("OpenAI client is not initialized (missing API key)")
                    response = self._make_api_request_with_retry(system_prompt, user_message)
                    translated_content = self._extract_translated_content(response)
                    input_tokens = response.usage.prompt_tokens
                    output_tokens = response.usage.completion_tokens
                    cost = self._calculate_cost(input_tokens, output_tokens)
                    result = TranslationResult(
                        translated_content=translated_content,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cost_usd=cost,
                        model=self.model,
                        metadata={"format": "latex_repair_blocks", "target_lang": target_lang, "strict_braces": strict_braces},
                    )
                    self._save_cached_result(cache_key, result, system_prompt, user_message)
            else:
                if self.client is None:
                    raise ValueError("OpenAI client is not initialized (missing API key)")
                response = self._make_api_request_with_retry(system_prompt, user_message)
                translated_content = self._extract_translated_content(response)

        parsed: dict[int, str] = {}
        rx = re.compile(r"@@@(\d{4})@@@\s*(.*?)\s*@@@END\1@@@", re.DOTALL)
        for m in rx.finditer(translated_content or ""):
            parsed[int(m.group(1))] = m.group(2)

        if not parsed:
            raw = (translated_content or "").strip()
            segments = re.split(r"@@@END\d{4}@@@", raw)
            segments = [s for s in segments if s.strip()]
            if len(segments) == len(blocks):
                fixed: list[str] = []
                for i, original in enumerate(blocks):
                    fixed.append(segments[i].strip() or original)
                return fixed

        fixed: list[str] = []
        for i, original in enumerate(blocks):
            v = parsed.get(i)
            fixed.append(v if isinstance(v, str) and v.strip() != "" else original)
        return fixed

    def build_markdown_system_prompt(self, target_lang: str = "ru") -> str:
        return (
            f"Translate scientific Markdown from English to {target_lang}. Preserve Markdown syntax exactly.\n"
            "CRITICAL RULES:\n"
            "1. TRANSLATE ALL text content including:\n"
            "   - Headings (# Title, ## Section, ### Subsection)\n"
            "   - Table headers and cell content\n"
            "   - Figure captions\n"
            "   - All prose and descriptions\n"
            "2. DO NOT translate or modify:\n"
            "   - Fenced code blocks (```...```)\n"
            "   - Inline code (`...`)\n"
            "   - Math blocks ($$...$$) and inline math ($...$)\n"
            "   - URLs, DOIs, email addresses\n"
            "   - Image/file paths\n"
            "   - Author names, journal names, and ALL organization/company/university/lab names (keep as-is)\n"
            "   - Abbreviations that are standard (e.g., MAP, HRUHC, CVD, ED)\n"
            "3. Keep Markdown structure intact (|, #, *, -, etc.)\n"
            "4. For tables: translate ALL cell content, including column headers\n\n"
            "SPECIAL CASES:\n"
            "- In search-strategy / query-term contexts (e.g., lists of database search queries), translate query terms fully to the target language.\n"
            "  Preserve boolean query operators and syntax as-is (AND, OR, parentheses) and keep wildcard '*' attached to the same token.\n"
            "- Translate guideline titles / expansions like 'Preferred Reporting Items for Systematic Reviews and Meta-Analyses' to the target language while keeping the acronym 'PRISMA'.\n\n"
            "Return ONLY the translated Markdown, no explanations or comments."
        )

    def build_markdown_lines_system_prompt(self, target_lang: str = "ru") -> str:
        return (
            f"Translate scientific Markdown lines from English to {target_lang}.\n"
            "You will receive multiple independent lines. Each line begins with a prefix like @@@0001@@@.\n"
            "CRITICAL RULES:\n"
            "1. Keep each prefix @@@NNNN@@@ EXACTLY unchanged.\n"
            "2. Return EXACTLY the same number of lines as input, one per line, preserving order.\n"
            "3. Preserve Markdown syntax and punctuation exactly, including |, [], (), #, *, -, _, :, ., and quotes.\n"
            "4. DO NOT add code fences, bullet points, numbering, or any extra text.\n"
            "5. DO NOT translate or modify tokens like <<CODE_0>>, <<MATH_1>>, <<URL_2>>, <<DOI_3>>.\n"
            "6. Translate ALL human-readable English words in each line (including 'Table', column headers, captions).\n"
            "6a. Keep ALL proper names unchanged, including organization/company/university/lab names (e.g., 'Google Brain').\n"
            "7. In search-strategy / query-term contexts (database search queries), translate query terms fully to the target language. Preserve AND/OR and parentheses exactly. Keep wildcard '*' attached to the same token.\n"
            "8. Translate guideline title expansions like 'Preferred Reporting Items for Systematic Reviews and Meta-Analyses' to the target language while keeping the acronym 'PRISMA'.\n"
            "Return ONLY the translated lines."
        )

    def translate_markdown_lines(self, lines: list[str], target_lang: str = "ru") -> list[str]:
        if not lines:
            return []

        system_prompt = self.build_markdown_lines_system_prompt(target_lang=target_lang)
        prefixed = [f"@@@{i:04d}@@@ {line}" for i, line in enumerate(lines)]
        user_message = "Translate these lines:\n" + "\n".join(prefixed)

        cache_key = self._cache_key(system_prompt, user_message)
        if self.cache_mode == "replay":
            cached = self._load_cached_result(cache_key)
            if cached is None:
                raise ValueError(f"Translation cache miss for key={cache_key}")
            translated_content = cached.translated_content
        else:
            if self.cache_mode == "record":
                cached = self._load_cached_result(cache_key)
                if cached is not None:
                    translated_content = cached.translated_content
                else:
                    if self.client is None:
                        raise ValueError("OpenAI client is not initialized (missing API key)")
                    response = self._make_api_request_with_retry(system_prompt, user_message)
                    translated_content = self._extract_translated_content(response)
                    input_tokens = response.usage.prompt_tokens
                    output_tokens = response.usage.completion_tokens
                    cost = self._calculate_cost(input_tokens, output_tokens)
                    result = TranslationResult(
                        translated_content=translated_content,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cost_usd=cost,
                        model=self.model,
                        metadata={"format": "markdown_lines", "target_lang": target_lang},
                    )
                    self._save_cached_result(cache_key, result, system_prompt, user_message)
            else:
                if self.client is None:
                    raise ValueError("OpenAI client is not initialized (missing API key)")
                response = self._make_api_request_with_retry(system_prompt, user_message)
                translated_content = self._extract_translated_content(response)

        parsed: dict[int, str] = {}
        for out_line in (translated_content or "").splitlines():
            m = re.match(r"^@@@(\d{4})@@@\s*(.*)$", out_line)
            if not m:
                continue
            parsed[int(m.group(1))] = m.group(2)

        # Fallback: some models sometimes drop the prefix despite instructions.
        if not parsed:
            raw_lines = [ln.rstrip("\r") for ln in (translated_content or "").splitlines()]
            # Strip empty leading/trailing lines.
            while raw_lines and not raw_lines[0].strip():
                raw_lines.pop(0)
            while raw_lines and not raw_lines[-1].strip():
                raw_lines.pop()
            if len(raw_lines) == len(lines):
                fixed: list[str] = []
                for i, original in enumerate(lines):
                    candidate = raw_lines[i].strip()
                    fixed.append(candidate if candidate else original)
                return fixed

        fixed: list[str] = []
        for i, original in enumerate(lines):
            v = parsed.get(i)
            fixed.append(v if isinstance(v, str) and v != "" else original)
        return fixed

    def translate_markdown(self, markdown_content: str, target_lang: str = "ru") -> TranslationResult:
        """Translate Markdown content while preserving Markdown syntax.

        This is an optional extension used by the PDF reconstruction pipeline.
        It does not affect the existing LaTeX pipeline.
        """
        self.logger.info("Starting Markdown translation...")

        system_prompt = self.build_markdown_system_prompt(target_lang=target_lang)
        user_message = f"Translate the following Markdown:\n\n{markdown_content}"

        cache_key = self._cache_key(system_prompt, user_message)
        if self.cache_mode == "replay":
            cached = self._load_cached_result(cache_key)
            if cached is None:
                raise ValueError(f"Translation cache miss for key={cache_key}")
            if self.logger:
                self.logger.info(f"Using cached translation (replay) key={cache_key}")
            return cached

        if self.cache_mode == "record":
            cached = self._load_cached_result(cache_key)
            if cached is not None:
                if self.logger:
                    self.logger.info(f"Using cached translation (record) key={cache_key}")
                return cached

        if self.client is None:
            raise ValueError("OpenAI client is not initialized (missing API key)")

        response = self._make_api_request_with_retry(system_prompt, user_message)
        translated_content = self._extract_translated_content(response)
        input_tokens = response.usage.prompt_tokens
        output_tokens = response.usage.completion_tokens
        cost = self._calculate_cost(input_tokens, output_tokens)

        result = TranslationResult(
            translated_content=translated_content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            model=self.model,
            metadata={"format": "markdown", "target_lang": target_lang},
        )

        if self.cache_mode == "record":
            self._save_cached_result(cache_key, result, system_prompt, user_message)

        return result
    
    def _build_system_prompt(self, glossary: Dict[str, str], target_lang: str = "ru") -> str:
        """
        Build system prompt for translation.
        
        Part 1: Basic prompt. Use _build_optimized_system_prompt for optimized version.
        
        Args:
            glossary: Dictionary of terms for translation
            
        Returns:
            System prompt string
        """
        lang_name = self._normalize_target_lang_for_prompt(target_lang)
        ja_style = ""
        if str(target_lang or "").strip().lower() in ("ja", "jp", "jpn"):
            ja_style = (
                "\nJAPANESE ACADEMIC STYLE:\n"
                "- Use academic/scientific register (である調).\n"
                "- DO NOT use polite forms (です/ます/でした/ません).\n"
                "- Avoid first-person pronouns and 'we' (私たち/我々); prefer impersonal phrasing (本研究では…, 提案手法は…, と考えられる).\n"
                "- Reduce translationese: prefer nominalization (名詞化), passive/impersonal constructions, and compressed syntax (構文の圧縮).\n"
                "- Avoid repetitive cadence like 'XはYである' in consecutive sentences; vary with 〜とされる/〜と考えられる/〜が示される.\n"
                "- Soften explicit English-style connectors (therefore/because/in order to): prefer その結果/これにより/〜ことから or omit when clear.\n"
                "- Avoid excessive term repetition when reference is clear (当該/同層/本手法/これら).\n"
            )

        prompt_parts = [
            f"You are a professional translator specializing in translating scientific papers from English to {lang_name}.",
            "",
            "Your task is to translate LaTeX documents while preserving all formatting, structure, and technical elements.",
            "",
            "CRITICAL RULES:",
            "1. Preserve ALL LaTeX commands exactly as they are (\\section, \\cite, \\ref, etc.)",
            "2. Do NOT translate LaTeX commands",
            "3. Preserve ALL mathematical formulas exactly as they are",
            "4. Preserve ALL images and graphics commands (\\includegraphics, etc.)",
            "5. Preserve bibliography section completely",
            "6. Translate text content, section titles, captions, and footnotes",
            "7. Do NOT skip any paragraphs, sections, or chapters",
            "8. Maintain exact document structure",
            "9. Keep proper names unchanged: person names and ALL organization/company/university/lab names. Do NOT translate or transliterate organization names.",
            ja_style.strip("\n") if ja_style else "",
            "",
            "SPECIALIZED PACKAGES:",
            "- Do NOT translate or modify tikz, xy-pic, qtree, pstricks, circuitikz code",
            "- Keep all specialized package code completely unchanged",
            "",
            "FOOTNOTES:",
            "- Translate content of \\footnote{}, \\marginpar{}, and \\thanks{} commands",
            "- Keep the commands themselves unchanged",
            "",
            "TABLES:",
            "- Translate table text content",
            "- Keep table structure and formatting unchanged",
            "",
        ]
        
        # Add glossary if provided
        if glossary:
            prompt_parts.append("")
            prompt_parts.append("TERMINOLOGY GLOSSARY:")
            for term, translation in glossary.items():
                prompt_parts.append(f"- {term} → {translation}")
        
        prompt_parts.append("")
        prompt_parts.append("Return ONLY the translated LaTeX code, without any explanations or markdown formatting.")
        
        prompt_parts = [p for p in prompt_parts if p != ""]
        return "\n".join(prompt_parts)
    
    def _build_optimized_system_prompt(self, glossary: Dict[str, str], target_lang: str = "ru") -> str:
        """
        Build optimized system prompt (Part 2: 30-50% reduction).
        
        Part 3: With caching for repeated use.
        
        Compact format while preserving all critical instructions.
        
        Args:
            glossary: Dictionary of terms for translation (should be pre-filtered)
            
        Returns:
            Optimized system prompt string
        """
        # Part 3: Check cache
        glossary_key = str(sorted(glossary.items())) if glossary else "empty"
        cache_key = f"prompt_{str(target_lang or 'ru').strip().lower()}_{glossary_key}"
        
        if cache_key in self._system_prompt_cache:
            if self.logger:
                self.logger.debug("Using cached system prompt")
            return self._system_prompt_cache[cache_key]
        
        # Use compact format with minimal separators
        lang_name = self._normalize_target_lang_for_prompt(target_lang)
        ja_style = ""
        if str(target_lang or "").strip().lower() in ("ja", "jp", "jpn"):
            ja_style = (
                "• Japanese: use である調; avoid です/ます; avoid 私たち/我々; prefer impersonal phrasing; reduce translationese via 名詞化/受身/圧縮; soften connectors; avoid repetitive 'XはYである'; reduce repetition (当該/同層/本手法/これら)"
            )

        prompt_parts = [
            f"Translate scientific LaTeX from English to {lang_name}. Preserve ALL formatting, structure, and technical elements.",
            "",
            "CRITICAL: TRANSLATE EVERY SINGLE SENTENCE. Do NOT skip any text, including:",
            "• Abstract content (even in custom environments like leapabstract, abstract, etc.)",
            "• Long paragraphs without LaTeX commands",
            "• Figure/table captions",
            "• All plain text between LaTeX commands",
            "",
            "RULES:",
            "• Keep ALL LaTeX commands unchanged (\\section, \\cite, \\ref, etc.)",
            "• Keep ALL formulas, images (\\includegraphics), bibliography unchanged",
            "• Translate: text, titles, captions, footnotes (\\footnote{}, \\marginpar{}, \\thanks{})",
            "• Do NOT skip paragraphs/sections/chapters - translate EVERYTHING",
            "• Keep proper names unchanged: person names and ALL organization/company/university/lab names. Do NOT translate or transliterate organization names",
            (ja_style if ja_style else ""),
            "• Specialized packages (tikz, xy-pic, qtree, pstricks, circuitikz): keep completely unchanged",
            "• Tables: translate text, keep structure",
            "",
        ]
        
        # Add glossary in compact JSON format if provided
        if glossary:
            # Part 3: Check glossary format cache
            glossary_key_str = str(sorted(glossary.items()))
            if glossary_key_str in self._glossary_format_cache:
                glossary_str = self._glossary_format_cache[glossary_key_str]
            else:
                glossary_str = format_glossary_compact(glossary)
                self._glossary_format_cache[glossary_key_str] = glossary_str
            
            prompt_parts.append(f"Glossary: {glossary_str}")
            prompt_parts.append("")
        
        prompt_parts.append("Return ONLY translated LaTeX, no explanations or markdown.")
        
        prompt_parts = [p for p in prompt_parts if p != ""]
        prompt = "\n".join(prompt_parts)
        
        # Part 3: Cache the prompt
        self._system_prompt_cache[cache_key] = prompt
        
        return prompt
    
    def _build_user_message(self, latex_content: str, target_lang: str = "ru") -> str:
        """
        Build user message with LaTeX content.
        
        Part 1: Simple message. Will be optimized in later parts.
        
        Args:
            latex_content: LaTeX content to translate
            
        Returns:
            User message string
        """
        lang_name = self._normalize_target_lang_for_prompt(target_lang)
        return f"""Translate the following LaTeX document from English to {lang_name}:

{latex_content}"""
    
    def _make_api_request(self, system_prompt: str, user_message: str):
        """
        Make API request to OpenAI.
        
        Part 1: Basic request with error handling.
        
        Args:
            system_prompt: System prompt
            user_message: User message with LaTeX content
            
        Returns:
            OpenAI API response
            
        Raises:
            APIError: If API request fails
            RateLimitError: If rate limit is exceeded
            APITimeoutError: If request times out
        """
        try:
            if self.logger:
                self.logger.debug("Making API request to OpenAI...")
            
            # GPT-5 models require max_completion_tokens instead of max_tokens
            is_gpt5 = "gpt-5" in self.model.lower()
            
            request_params = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                "timeout": self.timeout,
            }
            
            # GPT-5 models only support default temperature (1)
            if not is_gpt5:
                request_params["temperature"] = self.temperature
            
            # Add token limit parameter based on model type
            if self.max_tokens != "auto":
                if is_gpt5:
                    request_params["max_completion_tokens"] = self.max_tokens
                else:
                    request_params["max_tokens"] = self.max_tokens
            
            response = self.client.chat.completions.create(**request_params)
            
            if self.logger:
                self.logger.debug("API request successful")
            return response
            
        except RateLimitError as e:
            if self.logger:
                self.logger.error(f"Rate limit exceeded: {e}")
            raise
        except APITimeoutError as e:
            if self.logger:
                self.logger.error(f"API timeout: {e}")
            raise
        except APIConnectionError as e:
            if self.logger:
                self.logger.error(f"API error: Connection error.")
            raise
        except APIError as e:
            if self.logger:
                self.logger.error(f"API error: {e}")
            raise
        except Exception as e:
            # Catch any other exceptions (including network errors)
            # Check if it's a connection-related error
            error_msg = str(e).lower()
            if "connection" in error_msg or "network" in error_msg:
                if self.logger:
                    self.logger.error(f"API error: Connection error.")
                # Convert to APIConnectionError for proper retry handling
                raise APIConnectionError("Connection error") from e
            else:
                if self.logger:
                    self.logger.error(f"Unexpected error: {e}")
            raise
    
    def _make_api_request_with_retry(
        self, system_prompt: str, user_message: str
    ):
        """
        Make API request with retry logic (Part 3).
        
        Implements exponential backoff for rate limits and timeouts.
        
        Args:
            system_prompt: System prompt
            user_message: User message with LaTeX content
            
        Returns:
            OpenAI API response
            
        Raises:
            APIError: If API request fails after all retries
        """
        last_exception = None
        
        for attempt in range(self.max_retries + 1):
            try:
                return self._make_api_request(system_prompt, user_message)
                
            except RateLimitError as e:
                last_exception = e
                if attempt < self.max_retries:
                    # Exponential backoff: 1s, 2s, 4s, 8s, etc., capped at max_retry_delay
                    delay = min(
                        self.initial_retry_delay * (2 ** attempt),
                        self.max_retry_delay
                    )
                    if self.logger:
                        self.logger.warning(
                            f"Rate limit hit, retrying in {delay:.1f}s "
                            f"(attempt {attempt + 1}/{self.max_retries + 1})"
                        )
                    time.sleep(delay)
                else:
                    if self.logger:
                        self.logger.error(
                            f"Rate limit exceeded after {self.max_retries + 1} attempts"
                        )
                    raise
                    
            except APITimeoutError as e:
                last_exception = e
                if attempt < self.max_retries:
                    delay = min(
                        self.initial_retry_delay * (2 ** attempt),
                        self.max_retry_delay
                    )
                    if self.logger:
                        self.logger.warning(
                            f"Timeout, retrying in {delay:.1f}s "
                            f"(attempt {attempt + 1}/{self.max_retries + 1})"
                        )
                    time.sleep(delay)
                else:
                    if self.logger:
                        self.logger.error(
                            f"Timeout after {self.max_retries + 1} attempts"
                        )
                    raise
                    
            except APIConnectionError as e:
                # Connection errors - always retry with longer delays
                last_exception = e
                if attempt < self.max_retries:
                    # For connection errors, use longer delays: 3s, 6s, 12s, 24s, 48s, 96s
                    # This gives more time for network issues to resolve
                    delay = min(
                        self.initial_retry_delay * 1.5 * (2 ** attempt),
                        self.max_retry_delay
                    )
                    if self.logger:
                        self.logger.warning(
                            f"Connection error, retrying in {delay:.1f}s "
                            f"(attempt {attempt + 1}/{self.max_retries + 1})"
                        )
                    time.sleep(delay)
                else:
                    if self.logger:
                        self.logger.error(
                            f"Connection failed after {self.max_retries + 1} attempts"
                        )
                    raise
                    
            except APIError as e:
                # For other API errors, check if they're retryable
                error_code = getattr(e, 'code', None)
                if error_code in ['500', '502', '503', '504'] and attempt < self.max_retries:
                    # Server errors - retry
                    delay = min(
                        self.initial_retry_delay * (2 ** attempt),
                        self.max_retry_delay
                    )
                    if self.logger:
                        self.logger.warning(
                            f"Server error ({error_code}), retrying in {delay:.1f}s "
                            f"(attempt {attempt + 1}/{self.max_retries + 1})"
                        )
                    time.sleep(delay)
                    last_exception = e
                else:
                    # Non-retryable errors (e.g., invalid API key, insufficient funds)
                    if self.logger:
                        self.logger.error(f"Non-retryable API error: {e}")
                    raise
        
        # Should not reach here, but just in case
        if last_exception:
            raise last_exception
        raise APIError("Failed to make API request after retries")
    
    def _extract_translated_content(self, response) -> str:
        """
        Extract translated LaTeX content from API response.
        
        Part 1: Basic extraction. Will handle markdown wrappers in later parts.
        
        Args:
            response: OpenAI API response
            
        Returns:
            Translated LaTeX content
        """
        if not response.choices:
            raise ValueError("No response from API")
        
        content = response.choices[0].message.content
        
        # Remove markdown code blocks if present (basic handling)
        if content.startswith("```"):
            # Try to extract content from markdown code block
            lines = content.split("\n")
            if len(lines) > 2 and lines[0].startswith("```"):
                # Remove first and last line (markdown markers)
                content = "\n".join(lines[1:-1])
        
        return content.strip()
    
    def _calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """
        Calculate cost of API request.
        
        Args:
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            
        Returns:
            Cost in USD
        """
        input_cost = (input_tokens / 1000) * self.INPUT_PRICE_PER_1K_TOKENS
        output_cost = (output_tokens / 1000) * self.OUTPUT_PRICE_PER_1K_TOKENS
        return input_cost + output_cost
    

    
    def _estimate_token_breakdown(
        self,
        system_prompt: str,
        user_message: str,
        glossary: Dict[str, str]
    ) -> Dict[str, Any]:
        """
        Estimate token breakdown by component (Part 3).
        
        Provides detailed breakdown of tokens by component for monitoring.
        
        Args:
            system_prompt: System prompt
            user_message: User message
            glossary: Glossary dictionary
            
        Returns:
            Dictionary with token estimates by component
        """
        # Rough estimation: ~4 characters per token for English/Russian
        # This is approximate, actual tokenization is more complex
        chars_per_token = 4.0
        
        system_tokens = len(system_prompt) / chars_per_token
        glossary_tokens = len(format_glossary_compact(glossary)) / chars_per_token if glossary else 0
        document_tokens = len(user_message) / chars_per_token
        
        # System prompt includes glossary, so subtract to avoid double counting
        system_tokens_net = system_tokens - glossary_tokens
        
        total_estimated = system_tokens_net + glossary_tokens + document_tokens
        
        return {
            "system_prompt_tokens_est": int(system_tokens_net),
            "glossary_tokens_est": int(glossary_tokens),
            "document_tokens_est": int(document_tokens),
            "total_input_tokens_est": int(total_estimated),
            "glossary_terms_count": len(glossary),
        }
    
    def _log_token_breakdown(self, breakdown: Dict[str, Any]):
        """
        Log detailed token breakdown (Part 3).
        
        Args:
            breakdown: Token breakdown dictionary
        """
        if not self.logger:
            return
        
        self.logger.info("Token breakdown:")
        self.logger.info(
            f"  System prompt: ~{breakdown.get('system_prompt_tokens_est', 0)} tokens"
        )
        self.logger.info(
            f"  Glossary: ~{breakdown.get('glossary_tokens_est', 0)} tokens "
            f"({breakdown.get('glossary_terms_count', 0)} terms)"
        )
        self.logger.info(
            f"  Document: ~{breakdown.get('document_tokens_est', 0)} tokens"
        )
        
        if "actual_input_tokens" in breakdown:
            self.logger.info(
                f"  Actual input: {breakdown['actual_input_tokens']} tokens"
            )
            self.logger.info(
                f"  Actual output: {breakdown['actual_output_tokens']} tokens"
            )
            self.logger.info(
                f"  Actual cost: ${breakdown['actual_cost_usd']:.6f}"
            )


def translate_latex(latex_content: str, glossary: Optional[Dict[str, str]] = None) -> TranslationResult:
    """
    Convenience function to translate LaTeX content.
    
    Args:
        latex_content: LaTeX content to translate
        glossary: Optional dictionary of terms for translation
        
    Returns:
        TranslationResult with translated content and metadata
    """
    client = OpenAIClient()
    return client.translate_latex(latex_content, glossary)

