"""
Translation Validator module for Rosetta v3.

Responsible for programmatic validation of the translation quality.
Checks for:
- Preservation of structure (sections, paragraphs)
- Preservation of formulas and commands
- Length consistency
"""

import os
import re
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

from config import Config
from utils.glossary import load_glossary, find_relevant_terms, select_glossary_for_language
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ValidationResult:
    """Result of the validation process."""
    valid: bool
    score: float  # 0.0 to 1.0
    issues: List[str]
    metrics: Dict[str, Any]


class Validator:
    """
    Validates translation by comparing original and translated content.
    """
    
    def __init__(self):
        """Initialize the validator."""
        self.logger = get_logger(__name__)
        
    def validate_translation(
        self,
        original: str,
        translated: str,
        target_lang: str = "ru",
        glossary: Optional[Dict[str, Any]] = None,
    ) -> ValidationResult:
        """
        Validate translation quality.
        
        Args:
            original: Original LaTeX content
            translated: Translated LaTeX content
            
        Returns:
            ValidationResult object
        """
        self.logger.info("Starting translation validation...")
        
        issues: List[str] = []
        metrics: Dict[str, Any] = {}

        target_lang_norm = str(target_lang or "ru").strip().lower() or "ru"
        min_score_env = (os.environ.get("ROSETTA_VALIDATION_MIN_SCORE", "") or "").strip()
        try:
            min_score = float(min_score_env) if min_score_env else 0.8
        except Exception:
            min_score = 0.8

        def strip_latex(s: str) -> str:
            t = re.sub(r"%.*", " ", s)
            t = re.sub(r"\$\$.*?\$\$", " ", t, flags=re.DOTALL)
            t = re.sub(r"\$[^$]*\$", " ", t)
            t = re.sub(r"\\\[.*?\\\]", " ", t, flags=re.DOTALL)
            t = re.sub(r"\\begin\{equation\*?\}.*?\\end\{equation\*?\}", " ", t, flags=re.DOTALL)
            t = re.sub(r"\\begin\{align\*?\}.*?\\end\{align\*?\}", " ", t, flags=re.DOTALL)
            t = re.sub(r"\\[a-zA-Z]+\*?", " ", t)
            t = t.replace("{", " ").replace("}", " ")
            t = t.replace("[", " ").replace("]", " ")
            t = re.sub(r"\s+", " ", t).strip()
            return t

        orig_plain = strip_latex(original or "")
        trans_plain = strip_latex(translated or "")

        # 1. Check Length Consistency
        # Translated text (Russian) is usually longer than English (1.1x - 1.3x)
        # But in LaTeX, with commands, it might vary.
        len_orig = len(original)
        len_trans = len(translated)
        ratio = len_trans / len_orig if len_orig > 0 else 0
        
        metrics["length_ratio"] = ratio
        
        if ratio < 0.5:
            issues.append(f"Translation is suspiciously short (ratio {ratio:.2f})")
        elif ratio > 2.0:
            issues.append(f"Translation is suspiciously long (ratio {ratio:.2f})")
            
        # 2. Check Structure Preservation
        # Count sections
        sections_orig = len(re.findall(r'\\section\{', original))
        sections_trans = len(re.findall(r'\\section\{', translated))
        metrics["sections_orig"] = sections_orig
        metrics["sections_trans"] = sections_trans
        
        if sections_orig != sections_trans:
            issues.append(f"Section count mismatch: {sections_orig} vs {sections_trans}")
            
        # 3. Check Math Preservation
        # Count display math $$...$$ or \[...\]
        # This is rough because of masking, but we expect similar counts
        math_orig = len(re.findall(r'\$\$', original)) + len(re.findall(r'\\\[', original))
        math_trans = len(re.findall(r'\$\$', translated)) + len(re.findall(r'\\\[', translated))
        metrics["math_display_orig"] = math_orig
        metrics["math_display_trans"] = math_trans
        
        if abs(math_orig - math_trans) > 2: # Allow small deviation
            issues.append(f"Display math count mismatch: {math_orig} vs {math_trans}")
            
        # 4. Check for Hallucinations
        # Check for common English phrases that shouldn't be there?
        # Or check for broken commands
        
        try:
            glossary_dict: Dict[str, Any] = glossary if glossary is not None else load_glossary(str(Config.GLOSSARY_PATH))
            relevant_any = find_relevant_terms(glossary_dict or {}, orig_plain)
            expected_map = select_glossary_for_language(relevant_any or {}, target_lang_norm)

            missing = 0
            untranslated = 0
            for term, expected in (expected_map or {}).items():
                term_l = (term or "").strip().lower()
                expected_l = (expected or "").strip().lower()
                if term_l and re.search(r"\b" + re.escape(term_l) + r"\b", trans_plain.lower()):
                    untranslated += 1
                    issues.append(f"Glossary term left untranslated: '{term}'")
                if expected_l and expected_l not in trans_plain.lower():
                    missing += 1
                    issues.append(f"Glossary translation missing for '{term}' -> '{expected}'")

            metrics["glossary_relevant_terms"] = len(expected_map or {})
            metrics["glossary_relevant_terms_total"] = len(relevant_any or {})
            metrics["glossary_missing_translations"] = missing
            metrics["glossary_untranslated_terms"] = untranslated
        except Exception as e:
            issues.append(f"Terminology validation failed: {e}")

        if target_lang_norm == "ru":
            try:
                colloquial = (
                    "типа",
                    "короче",
                    "в общем",
                    "ну ",
                    "ладно",
                    "окей",
                    "круто",
                    "супер",
                    "просто ",
                    "реально",
                )
                trans_l = trans_plain.lower()
                hits = [w for w in colloquial if w in trans_l]
                if hits:
                    issues.append(f"Colloquial register detected: {', '.join(sorted(set(hits))) }")
                exclam = trans_plain.count("!")
                if exclam >= 2:
                    issues.append(f"Excessive exclamation marks: {exclam}")
                if "!!" in trans_plain or "???" in trans_plain:
                    issues.append("Informal punctuation detected")
                if re.search(r"(^|\s)я(\s|$)", trans_l):
                    issues.append("First-person singular detected ('я')")

                metrics["style_colloquial_hits"] = len(hits)
                metrics["style_exclamation_marks"] = exclam
            except Exception as e:
                issues.append(f"Style validation failed: {e}")

        score = 1.0
        for issue in issues:
            issue_l = issue.lower()
            if "mismatch" in issue_l:
                score -= 0.3
            elif "glossary term left untranslated" in issue_l:
                score -= 0.2
            elif "glossary translation missing" in issue_l:
                score -= 0.15
            elif "colloquial" in issue_l or "informal" in issue_l or "excessive" in issue_l:
                score -= 0.1
            else:
                score -= 0.05
        
        score = max(0.0, score)
        valid = score >= min_score
        
        self.logger.info(f"Validation complete. Score: {score:.2f}. Issues: {len(issues)}")
        
        return ValidationResult(
            valid=valid,
            score=score,
            issues=issues,
            metrics=metrics
        )

# Convenience function
def validate_translation(
    original: str,
    translated: str,
    target_lang: str = "ru",
    glossary: Optional[Dict[str, Any]] = None,
) -> ValidationResult:
    validator = Validator()
    return validator.validate_translation(original, translated, target_lang=target_lang, glossary=glossary)
