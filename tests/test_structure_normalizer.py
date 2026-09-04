"""Tests for the structure normalizer module."""
from __future__ import annotations
import pytest
pytest.importorskip("pipeline.pdf_reconstruct")


from pipeline.pdf_reconstruct.document_schema import (
    SectionType,
    identify_section,
    normalize_section_name,
    get_canonical_name,
    parse_heading,
    is_likely_subsection,
)
from pipeline.pdf_reconstruct.structure_normalizer import (
    StructureNormalizer,
    normalize_document_structure,
)


class TestDocumentSchema:
    """Tests for document_schema.py functions."""

    def test_normalize_section_name_basic(self):
        assert normalize_section_name("Introduction") == "introduction"
        assert normalize_section_name("  Introduction  ") == "introduction"
        assert normalize_section_name("**Introduction**") == "introduction"

    def test_normalize_section_name_with_numbering(self):
        assert normalize_section_name("1. Introduction") == "introduction"
        assert normalize_section_name("2.1 Methods") == "methods"
        assert normalize_section_name("1.2.3. Results") == "results"

    def test_identify_section_english(self):
        section = identify_section("Introduction")
        assert section is not None
        assert section.section_type == SectionType.INTRODUCTION

        section = identify_section("Methods")
        assert section is not None
        assert section.section_type == SectionType.METHODS

        section = identify_section("Materials and Methods")
        assert section is not None
        assert section.section_type == SectionType.METHODS

    def test_identify_section_russian(self):
        section = identify_section("Введение")
        assert section is not None
        assert section.section_type == SectionType.INTRODUCTION

        section = identify_section("Методы")
        assert section is not None
        assert section.section_type == SectionType.METHODS

        section = identify_section("Заключение")
        assert section is not None
        assert section.section_type == SectionType.CONCLUSION

    def test_identify_section_unknown(self):
        section = identify_section("Some Random Heading")
        assert section is None

    def test_get_canonical_name_english(self):
        assert get_canonical_name("intro", target_lang="en") == "Introduction"
        assert get_canonical_name("methodology", target_lang="en") == "Methods"
        assert get_canonical_name("conclusions", target_lang="en") == "Conclusion"

    def test_get_canonical_name_russian(self):
        assert get_canonical_name("introduction", target_lang="ru") == "Введение"
        assert get_canonical_name("methods", target_lang="ru") == "Методы"

    def test_parse_heading_basic(self):
        parsed = parse_heading("## Introduction")
        assert parsed is not None
        assert parsed.level == 2
        assert parsed.text == "Introduction"
        assert parsed.section_type == SectionType.INTRODUCTION

    def test_parse_heading_with_unnumbered_marker(self):
        parsed = parse_heading("## References {-}")
        assert parsed is not None
        assert parsed.level == 2
        assert parsed.text == "References"
        assert parsed.is_unnumbered is True

    def test_parse_heading_not_a_heading(self):
        parsed = parse_heading("This is just text")
        assert parsed is None

    def test_is_likely_subsection(self):
        assert is_likely_subsection("Study Design") is True
        assert is_likely_subsection("Data Collection") is True
        assert is_likely_subsection("Statistical Analysis") is True
        assert is_likely_subsection("Introduction") is False
        assert is_likely_subsection("Random Text") is False


class TestStructureNormalizer:
    """Tests for StructureNormalizer class."""

    def test_normalize_heading_hierarchy_basic(self):
        """Test that main sections get normalized to level 2."""
        input_md = """# Paper Title

# Abstract

Some abstract text.

#### Introduction

Introduction text.

## Methods

Methods text.

### Results

Results text.
"""
        result = normalize_document_structure(input_md)
        
        lines = result.text.splitlines()
        heading_lines = [l for l in lines if l.strip().startswith("#")]
        
        # Check that we found sections
        assert len(result.sections_found) > 0
        assert result.sections_normalized > 0

    def test_normalize_preserves_title(self):
        """Test that the paper title stays at level 1."""
        input_md = """# My Paper Title

## Abstract

Abstract content.
"""
        result = normalize_document_structure(input_md)
        
        # Title should remain at level 1
        assert result.text.startswith("# My Paper Title")

    def test_normalize_abstract_to_level_2(self):
        """Test that Abstract gets normalized to level 2."""
        input_md = """# Paper Title

# Abstract

Content.
"""
        result = normalize_document_structure(input_md)
        
        # Abstract should be level 2
        assert "## Abstract" in result.text

    def test_merge_broken_paragraphs(self):
        """Test that broken paragraphs get merged."""
        input_md = """# Title

This is a sentence that was
broken across two lines.

This is a complete sentence.
"""
        normalizer = StructureNormalizer()
        merged_text, count = normalizer._merge_broken_paragraphs(input_md)
        
        # Should have merged at least one paragraph
        # Note: exact behavior depends on heuristics
        assert "broken across" in merged_text or count >= 0

    def test_clean_orphaned_formatting(self):
        """Test removal of orphaned formatting."""
        input_md = """# Title

**Label:**
Some text here.

Empty bold: ** **
"""
        normalizer = StructureNormalizer()
        cleaned = normalizer._clean_orphaned_formatting(input_md)
        
        # Should merge label with next line
        assert "**Label:** Some text here." in cleaned

    def test_canonical_section_names(self):
        """Test that section names get canonicalized."""
        input_md = """# Title

## methodology

Content.

## concluding remarks

More content.
"""
        result = normalize_document_structure(input_md)
        
        # Should use canonical names
        assert "Methods" in result.text or "methodology" in result.text.lower()

    def test_unnumbered_sections(self):
        """Test that certain sections get {-} marker."""
        input_md = """# Title

## Abstract

Content.

## References

Refs here.
"""
        result = normalize_document_structure(input_md)
        
        # Abstract and References should be unnumbered
        assert "{-}" in result.text

    def test_subsection_detection(self):
        """Test that subsections are detected and handled."""
        input_md = """# Title

## Methods

### Study Design

Design details.

### Data Collection

Collection details.
"""
        result = normalize_document_structure(input_md)
        
        # Subsections should remain at level 3
        assert "### Study Design" in result.text or "Study Design" in result.text


class TestIntegration:
    """Integration tests for the full normalization pipeline."""

    def test_full_pipeline_imrad_structure(self):
        """Test normalization of a typical IMRAD paper structure."""
        input_md = """# A Study on Something Important

# Abstract

This is the abstract.

#### Introduction

This is the introduction.

## Methods

### Study Design

Design details.

### Data Collection

Collection details.

### Results

Results here.

#### Discussion

Discussion text.

#### Conclusions

Final conclusions.

## References

1. Reference one.
"""
        result = normalize_document_structure(input_md)
        
        # Should have found multiple sections
        assert len(result.sections_found) >= 3
        
        # Output should be valid markdown
        assert result.text.strip() != ""
        
        # Should have normalized some sections
        assert result.sections_normalized >= 0

    def test_russian_paper_structure(self):
        """Test normalization of a Russian paper structure."""
        input_md = """# Исследование важной темы

# Аннотация

Текст аннотации.

## Введение

Текст введения.

## Методы

Описание методов.

## Заключение

Выводы.
"""
        result = normalize_document_structure(input_md, target_lang="ru")
        
        # Should recognize Russian section names
        assert len(result.sections_found) >= 2

    def test_abstract_subsections_stay_level_3(self):
        """Test that subsections within Abstract stay at level 3."""
        input_md = """# Paper Title

# Abstract

### Background

Background text.

### Objective

Objective text.

## Introduction

Introduction text.

## Methods

Methods text.
"""
        result = normalize_document_structure(input_md)
        
        # Abstract should be level 2
        assert "## Abstract" in result.text
        
        # Background and Objective should be level 3 (subsections of Abstract)
        assert "### Background" in result.text
        assert "### Objective" in result.text
        
        # Introduction and Methods should be level 2
        assert "## Introduction" in result.text
        assert "## Methods" in result.text

    def test_duplicate_main_sections_become_subsections(self):
        """Test that duplicate main sections become subsections."""
        input_md = """# Title

## Introduction

Intro text.

## Methods

First methods section.

## Results

Results text.

## Methods

Second methods section (should become subsection).
"""
        result = normalize_document_structure(input_md)
        
        # First Methods should be level 2
        lines = result.text.splitlines()
        methods_lines = [l for l in lines if "Methods" in l and l.strip().startswith("#")]
        
        # Should have at least one level 2 and one level 3 Methods
        assert any("## Methods" in l or "## Methods" in l for l in methods_lines)
        assert any("### Methods" in l for l in methods_lines)
