"""Conservative local routing for explicit JD commands and requirement replies."""

import re


JD_NAME = r"(?:jd|job\s+description)"


def requests_jd_creation(message: str) -> bool:
    # Inspect the opening request, not instructions quoted later in a document.
    opening = message.strip().split("\n", 1)[0]
    return bool(re.match(
        rf"^(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?"
        rf"(?:create|generate|draft|write|prepare|make)\b[^\n.!?]{{0,180}}\b{JD_NAME}\b",
        opening, re.I,
    ))


def explicit_jd_approval(message: str) -> bool:
    # Full matching excludes questions, negations, and edits before saving.
    return bool(re.fullmatch(
        rf"\s*(?:please\s+)?(?:i\s+)?(?:approve|accept|save)"
        rf"(?:\s+and\s+(?:approve|accept|save))?"
        rf"(?:\s+(?:(?:this|the)\s+)?(?:current\s+)?(?:{JD_NAME}|draft|it))?"
        rf"(?:\s+(?:now|please))?[.!]?\s*",
        message, re.I,
    ))


def explicit_requirement_reply(message: str) -> bool:
    return bool(re.match(
        r"^\s*(?:job\s+title|role|responsibilities|technical\s+skills|"
        r"work\s+experience|qualifications|additional\s+criteria|"
        r"application\s+(?:url|link)|google\s+form\s+(?:url|link))"
        r"\s*(?::|is\b|are\b|=)", message, re.I,
    ))


def recruiter_context(messages: list) -> list:
    """Keep recruiter facts from the latest creation request, excluding AI text."""
    human = [m for m in messages if m.type == "human" and isinstance(m.content, str)]
    start = next((i for i in range(len(human) - 1, -1, -1)
                  if requests_jd_creation(human[i].content)), 0)
    return human[start:]


def unverified_jd_claim(content: str) -> bool:
    claims = re.finditer(
        r"\b(?:i(?:['’]ve| have)?\s+(?:(?:successfully|already)\s+)?"
        r"(?:saved|approved|marked\b[^\n.!?]{0,80}\bapproved)|"
        r"(?:jd|job description|draft)\s+(?:has been|is|was)\s+"
        r"(?:(?:successfully|now)\s+)?(?:saved|approved))\b", content, re.I,
    )
    for claim in claims:
        # "Once a JD is approved, I can post it" explains a prerequisite;
        # it does not claim approval happened. Require the condition directly
        # before the subject; a later "I saved it" still requires a receipt.
        prefix = content[:claim.start()]
        if re.search(
            r"\b(?:if|when|once|after|before|until|unless)\s+"
            r"(?:(?:a|an|the|this|that|your|our|current|new|existing|selected)\s+)*$",
            prefix, re.I,
        ):
            continue
        return True
    return False
