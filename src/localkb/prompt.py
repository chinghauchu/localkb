"""Prompt assembled for assistants that have no MCP connector."""

from __future__ import annotations

from localkb.models import Hit


def build_prompt(question: str, hits: list[Hit]) -> str:
    if hits:
        blocks = [
            f"[{index}] {hit.path} ({hit.location})\n{hit.text.strip()}"
            for index, hit in enumerate(hits, start=1)
        ]
        excerpts = "\n\n".join(blocks)
    else:
        excerpts = "(no excerpts found)"
    return (
        "You are answering from a private local knowledge base. "
        "Use only the excerpts below. If they do not contain the answer, say so.\n\n"
        "Cite every claim you take from an excerpt as [n] and name the file and location "
        "(page, slide, sheet, or section).\n\n"
        f"Question:\n{question.strip()}\n\n"
        f"Excerpts:\n{excerpts}\n"
    )
