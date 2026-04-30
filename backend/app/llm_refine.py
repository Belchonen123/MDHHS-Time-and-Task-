"""Optional LLM refinement of parsed PDF fields (no-op unless implemented)."""

from __future__ import annotations

from .extract import ExtractedForm


def refine_extracted_form(form: ExtractedForm, use_llm: bool) -> ExtractedForm:
    """
    When ``use_llm`` is false, return the form unchanged.
    When true, a future implementation may call an LLM; today this is a no-op.
    """
    if not use_llm:
        return form
    return form
