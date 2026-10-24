"""Short deterministic English proposals using only recognized requirement cues.

Project descriptions are untrusted data. No project text is interpreted as an instruction,
and arbitrary titles, links, contact details, or claims are never copied into a proposal.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Project
    from .scoring import Assessment


def _join(items: list[str]) -> str:
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _requested(text: str, pattern: str) -> bool:
    """Ignore recognizable explicit exclusions such as 'no captions' or 'without music'."""
    for match in re.finditer(pattern, text):
        prefix = text[max(0, match.start() - 35) : match.start()]
        if not re.search(
            r"\b(?:no|without|exclude|avoid|not include|don't add|do not add|"
            r"don't need|do not need|no need for)\s+(?:any\s+)?$",
            prefix,
        ):
            return True
    return False


def generate_proposal(project: Project, assessment: Assessment) -> str:
    """Generate a scoped offer, not a claim about credentials or an unconditional promise."""
    text = f"{project.title}\n{project.description}".lower()
    services = set(assessment.matched_services)
    platforms = [
        name
        for pattern, name in (
            (r"\byoutube\s+shorts?\b", "YouTube Shorts"),
            (r"\b(?:instagram\s+)?reels?\b", "Instagram Reels"),
            (r"\btik\s*tok\b", "TikTok"),
        )
        if _requested(text, pattern)
    ]

    if "ai_video" in services:
        target = "AI animation" if re.search(r"\bai\s+animation\b", text) else "AI video"
        if re.search(r"\bads?\b|\badvertis", text):
            target = "AI video ad"
        opening = f"Hi, I can create your {target} around your message and visual style."
    elif "motion_graphics" in services:
        opening = (
            "Hi, I can create your motion graphics in After Effects with clear visual storytelling."
        )
    elif "product_video" in services:
        target = "product video" if re.search(r"\bproducts?\b", text) else "promotional video"
        opening = f"Hi, I can edit your {target} to present the key benefits clearly."
    elif "short_form" in services:
        target = _join(platforms) if platforms else "short-form videos"
        opening = f"Hi, I can edit your {target} with a strong opening and clean pacing."
    else:
        opening = "Hi, I can edit your video with a clear structure and clean pacing."

    cues = (
        (r"\b(?:captions?|subtitles?)\b", "readable captions"),
        (r"\b(?:9\s*:\s*16|vertical)\b", "9:16 formatting"),
        (r"\b(?:colou?r\s+grad(?:ing|e)|colou?r\s+correct(?:ion|ing))\b", "color grading"),
        (r"\b(?:audio\s+mix(?:ing)?|sound\s+(?:design|mix(?:ing)?))\b", "balanced audio"),
        (r"\bkinetic\s+typography\b", "kinetic typography"),
        (r"\btransitions?\b", "smooth transitions"),
        (r"\b(?:brand|branding)\b", "brand-aligned styling"),
        (r"\bvoice\s*over\b", "voiceover timing"),
    )
    requirements = [label for pattern, label in cues if _requested(text, pattern)][:4]
    duration = re.search(r"\b(15|30|45|60|90)\s*[- ]?\s*(?:seconds?|secs?|s)\b", text)
    detail = ""
    if requirements:
        detail = f"I'll focus on {_join(requirements)}. "
    if duration:
        detail += f"I can structure a {duration.group(1)}-second cut. "
    if _requested(text, r"\b(?:adobe\s+)?premiere(?:\s+pro)?\b"):
        detail += "I can work in Adobe Premiere Pro. "
    elif _requested(text, r"\b(?:davinci\s+)?resolve\b"):
        detail += "I can work in DaVinci Resolve. "
    if platforms and not (
        "short_form" in services
        and not services.intersection({"ai_video", "motion_graphics", "product_video"})
    ):
        detail += f"The edit can be tailored for {_join(platforms)}. "

    if _requested(text, r"\bvoice\s*over\b"):
        question = "Will you provide the voiceover and source footage?"
    elif "ai_video" in services:
        question = "Do you have a preferred visual style and approved assets?"
    elif "product_video" in services:
        question = "Can you share the product assets and key selling points?"
    elif "motion_graphics" in services:
        question = "Can you share your brand assets and a style reference?"
    elif duration:
        question = "Can you share your source footage and a style reference?"
    else:
        question = "Can you share your source footage and target duration?"

    return f"{opening} {detail}{question}"
