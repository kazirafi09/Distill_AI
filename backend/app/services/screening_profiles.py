"""JD screening contracts and acceptance checks (no candidate scoring)."""
import re
from decimal import Decimal
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool

DIMENSIONS = ("technical_skills", "work_experience", "qualifications", "additional_criteria")
Dimension = Literal["technical_skills", "work_experience", "qualifications", "additional_criteria"]
FORM_URL = re.compile(
    r"https://(?:forms\.gle/[A-Za-z0-9_-]+|docs\.google\.com/forms/d/(?:e/)?[A-Za-z0-9_-]+/viewform)(?:\?[^\s#]*)?\Z"
)


class Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str  # Empty for a new criterion; the server assigns and preserves it.
    dimension: Dimension
    text: str = Field(min_length=1, max_length=2000)
    priority: Literal["Must have", "Preferred"] | None
    source_quote: str = Field(min_length=1)
    priority_quote: str | None


class DimensionWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    technical_skills: float
    work_experience: float
    qualifications: float
    additional_criteria: float


class ScreeningProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criteria: list[Criterion]
    addressed_dimensions: list[Dimension]
    # Verbatim recruiter wording avoids changing ranges, units or eligibility.
    minimum_years_experience: str | None
    fresher_eligibility: str | None
    dimension_weights: DimensionWeights
    weights_confirmed: StrictBool
    weights_confirmation_quote: str | None
    google_form_url: str | None


class ProfileValidationError(ValueError):
    def __init__(self, field: str, error_type: str):
        super().__init__(f"{field}: {error_type}")
        self.field = field
        self.error_type = error_type


def supported_form_url(value: str | None) -> bool:
    """Format only; intentionally no network request or accessibility claim."""
    return isinstance(value, str) and FORM_URL.fullmatch(value) is not None


def contains_internal_metadata(content: str) -> bool:
    return re.search(
        r"\b(?:(?:screening|scoring|dimension)[ _-]+weights?|screening_profile|dimension_weights|criterion-[0-9a-f-]+)\b",
        content, re.I,
    ) is not None


def normalize_profile(incoming: ScreeningProfile, previous: dict | None, messages: list) -> dict:
    """Retain IDs and require recruiter evidence, never model suggestions alone."""
    sources = [m.content for m in messages if m.type == "human" and isinstance(m.content, str)]
    def supplied(quote):
        return bool(quote and any(quote in source for source in sources))

    def exact_text(value: str) -> tuple[str, str] | None:
        """Return the source and exact source substring, allowing case changes only."""
        for source in sources:
            start = source.casefold().find(value.casefold())
            if start >= 0:
                return source, source[start:start + len(value)]
        return None

    def explicit_priority(priority: str, text: str) -> str | None:
        evidence = exact_text(text)
        if evidence is None:
            return None
        source, exact = evidence
        position = source.find(exact)
        markers = {
            "Must have": r"\b(must[ -]?haves?|required|mandatory|essential)\b",
            "Preferred": r"\b(preferred|optional|nice[ -]?to[ -]?have)\b",
        }
        preceding = [
            (match.start(), label, match.group(0))
            for label, pattern in markers.items()
            for match in re.finditer(pattern, source[:position], re.I)
        ]
        if not preceding:
            return None
        _, label, quote = max(preceding, key=lambda item: item[0])
        return quote if label == priority else None

    def explicit_weight_quote(weights: dict[str, float], active: set[str]) -> str | None:
        if not active:
            return None
        aliases = {
            "technical_skills": r"technical skills?",
            "work_experience": r"(?:relevant |work )?experience(?:,?\s+including projects)?",
            "qualifications": r"qualifications?",
            "additional_criteria": r"additional (?:selection )?criteria",
        }
        for source in sources:
            matches = []
            for dimension in active:
                number = re.escape(format(weights[dimension], "g"))
                match = re.search(
                    rf"{aliases[dimension]}\s*[:=-]?\s*{number}\s*%",
                    source,
                    re.I,
                )
                if match is None:
                    break
                matches.append(match)
            else:
                return source[min(match.start() for match in matches):max(match.end() for match in matches)]
        return None

    def listed_criteria() -> list[tuple[str, str, str]]:
        """Read exact bullets beneath explicit recruiter priority headings."""
        listed = []
        for source in sources:
            priority = None
            priority_quote = None
            for line in source.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                bullet = re.match(r"^[-*•]\s+(.+)$", stripped)
                if bullet and priority:
                    listed.append((bullet.group(1).strip(), priority, priority_quote))
                    continue
                if bullet:
                    continue
                if len(stripped) <= 100 and re.search(
                    r"\bmust[ -]?haves?\b|\brequired\b|\bmandatory\b|\bessential\b",
                    stripped,
                    re.I,
                ):
                    priority, priority_quote = "Must have", stripped
                elif len(stripped) <= 100 and re.search(
                    r"\bpreferred\b|\boptional\b|\bnice[ -]?to[ -]?have\b",
                    stripped,
                    re.I,
                ):
                    priority, priority_quote = "Preferred", stripped
                elif stripped.endswith(":"):
                    priority = priority_quote = None
        return listed

    def tokens(value: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", value.casefold()))

    explicit_list = listed_criteria()
    if explicit_list:
        remaining = list(incoming.criteria)
        grounded = []
        for exact, priority, priority_quote in explicit_list:
            exact_tokens = tokens(exact)
            ranked = []
            for candidate in remaining:
                candidate_tokens = tokens(candidate.text)
                union = exact_tokens | candidate_tokens
                overlap = len(exact_tokens & candidate_tokens) / len(union) if union else 0
                if candidate.priority == priority:
                    overlap += 0.15
                ranked.append((overlap, candidate))
            if not ranked:
                continue
            score, criterion = max(ranked, key=lambda item: item[0])
            if score < 0.4:
                continue
            remaining.remove(criterion)
            criterion.text = exact
            criterion.source_quote = exact
            criterion.priority = priority
            criterion.priority_quote = priority_quote
            grounded.append(criterion)
        incoming.criteria = grounded

    old = (previous or {}).get("criteria", [])
    by_id = {c["id"]: c for c in old}
    by_text = {(c["dimension"], c["text"]): c["id"] for c in old}
    used = set()
    for index, criterion in enumerate(incoming.criteria):
        evidence = exact_text(criterion.text)
        if evidence is not None:
            criterion.text = evidence[1]
            criterion.source_quote = evidence[1]
        elif not supplied(criterion.source_quote):
            raise ProfileValidationError(
                f"criteria.{index}.source_quote",
                "recruiter_evidence_missing",
            )
        priority_markers = {
            "Must have": r"\b(must[ -]?have|required|mandatory|essential)\b",
            "Preferred": r"\b(preferred|optional|nice[ -]?to[ -]?have)\b",
        }
        if criterion.priority is not None:
            priority_quote = criterion.priority_quote or ""
            if supplied(priority_quote) and re.search(
                priority_markers[criterion.priority], priority_quote, re.I,
            ):
                pass
            else:
                criterion.priority_quote = explicit_priority(
                    criterion.priority,
                    criterion.text,
                )
                if criterion.priority_quote is None:
                    criterion.priority = None
        identifier = criterion.id if criterion.id in by_id else by_text.get((criterion.dimension, criterion.text))
        criterion.id = identifier if identifier and identifier not in used else f"criterion-{uuid4()}"
        used.add(criterion.id)
    for value in (incoming.minimum_years_experience, incoming.fresher_eligibility, incoming.google_form_url):
        if value is not None and not supplied(value):
            raise ProfileValidationError("screening_profile", "verbatim_recruiter_evidence_missing")

    addressed = {criterion.dimension for criterion in incoming.criteria}
    if incoming.minimum_years_experience is not None or incoming.fresher_eligibility is not None:
        addressed.add("work_experience")
    waiver_patterns = {
        "technical_skills": r"\b(?:no|none)\b.{0,30}\btechnical skills?\b|\btechnical skills?\b.{0,15}\b(?:none|not required)\b",
        "work_experience": r"\bno (?:minimum |relevant |professional )?(?:years? of )?(?:work )?experience\b|\bwork experience\b.{0,15}\b(?:none|not required)\b",
        "qualifications": r"\b(?:no|none)\b.{0,30}\bqualifications?\b|\bqualifications?\b.{0,15}\b(?:none|not required)\b",
        "additional_criteria": r"\b(?:no|none)\b.{0,30}\badditional (?:selection )?criteria\b|\badditional (?:selection )?criteria\b.{0,20}\b(?:none|no others?|not required)\b",
    }
    for dimension, pattern in waiver_patterns.items():
        if any(re.search(pattern, source, re.I) for source in sources):
            addressed.add(dimension)
    incoming.addressed_dimensions = [
        dimension for dimension in DIMENSIONS if dimension in addressed
    ]
    incoming_weights = incoming.dimension_weights.model_dump()
    active_dimensions = {criterion.dimension for criterion in incoming.criteria}
    direct_weight_quote = explicit_weight_quote(incoming_weights, active_dimensions)
    # Confirmation is recruiter evidence, not a model-generated boolean. Bind a
    # short reply to the unchanged profile and the preceding weight question.
    same_weights = incoming_weights == (previous or {}).get("dimension_weights")
    contextual_confirmation = None
    if same_weights:
        last_human_index = next((
            index for index in range(len(messages) - 1, -1, -1)
            if messages[index].type == "human"
            and isinstance(messages[index].content, str)
        ), None)
        if last_human_index is not None:
            reply = messages[last_human_index].content.strip()
            prior_question = next((
                message.content for message in reversed(messages[:last_human_index])
                if message.type == "ai" and isinstance(message.content, str)
            ), "")
            if (
                re.fullmatch(
                    r"(?:yes[,.]?\s*)?(?:i\s+)?(?:confirm(?:ed)?|agree|accept|approved|"
                    r"(?:the\s+)?weights?\s+(?:are\s+)?confirmed|yes)[.!]?",
                    reply, re.I,
                )
                and re.search(r"\bweights?\b", prior_question, re.I)
            ):
                contextual_confirmation = reply
    if direct_weight_quote is not None:
        incoming.weights_confirmed = True
        incoming.weights_confirmation_quote = direct_weight_quote
    elif contextual_confirmation is not None:
        incoming.weights_confirmed = True
        incoming.weights_confirmation_quote = contextual_confirmation
    elif same_weights and previous.get("weights_confirmed") and supplied(previous.get("weights_confirmation_quote")):
        incoming.weights_confirmed = True
        incoming.weights_confirmation_quote = previous["weights_confirmation_quote"]
    elif not supplied(incoming.weights_confirmation_quote):
        incoming.weights_confirmed = False
        incoming.weights_confirmation_quote = None
    if incoming.weights_confirmed and direct_weight_quote is None and contextual_confirmation is None and not (
        same_weights and previous.get("weights_confirmed")
        and incoming.weights_confirmation_quote == previous.get("weights_confirmation_quote")
    ):
        quote = incoming.weights_confirmation_quote or ""
        numbers = {Decimal(n) for n in re.findall(r"\d+(?:\.\d+)?", quote)}
        explicit_weights = all(
            Decimal(str(weight)) in numbers
            for dimension, weight in incoming_weights.items()
            if dimension in active_dimensions
        )
        proposal_confirmed = False
        for index, message in enumerate(messages):
            if message.type != "human" or not isinstance(message.content, str) or quote not in message.content:
                continue
            prior = next((m.content for m in reversed(messages[:index]) if m.type == "ai" and isinstance(m.content, str)), "")
            proposed_numbers = {Decimal(n) for n in re.findall(r"\d+(?:\.\d+)?", prior)}
            if re.search(r"\bweights?\b", prior, re.I) and all(
                Decimal(str(weight)) in proposed_numbers
                for dimension, weight in incoming_weights.items()
                if dimension in active_dimensions
            ):
                proposal_confirmed = True
        accepted_proposal = (
            incoming_weights == (previous or {}).get("dimension_weights")
            and bool(re.search(r"\b(yes|confirm|confirmed|accept|accepted|agree|approved|use|keep)\b", quote, re.I))
            and (proposal_confirmed or bool(re.search(r"\bweights?\b", quote, re.I)))
        )
        if not explicit_weights and not accepted_proposal:
            incoming.weights_confirmed = False
        if (
            previous and incoming_weights != previous.get("dimension_weights")
            and quote == previous.get("weights_confirmation_quote")
        ):
            incoming.weights_confirmed = False
    return incoming.model_dump(mode="json")


def profile_errors(profile: dict | None) -> list[str]:
    if profile is None:
        return ["A recruiter-confirmed screening profile is required. Revise this JD to gather it."]
    try:
        parsed = ScreeningProfile.model_validate(profile)
    except ValueError:
        return ["The screening profile is invalid. Revise it before acceptance."]
    errors = []
    if set(parsed.addressed_dimensions) != set(DIMENSIONS):
        errors.append("Gather technical skills, work experience, qualifications and additional selection criteria (or explicitly specify none).")
    ids = [c.id for c in parsed.criteria]
    if any(not identifier for identifier in ids) or len(ids) != len(set(ids)):
        errors.append("Every criterion must have a unique stable ID.")
    if any(c.priority is None or not c.priority_quote for c in parsed.criteria):
        errors.append("Confirm Must have or Preferred for every criterion.")
    active = {c.dimension for c in parsed.criteria}
    weights = parsed.dimension_weights.model_dump()
    if not active or not active.issubset(weights) or any(weight != 0 for dimension, weight in weights.items() if dimension not in active):
        errors.append("Supply weights for active screening dimensions; unused dimensions may only have zero weight.")
    elif any(not 0 <= weight <= 100 for weight in weights.values()) or sum(Decimal(str(w)) for w in weights.values()) != Decimal("100"):
        errors.append("Active dimension weights must be between 0 and 100 and total 100%.")
    if not parsed.weights_confirmed or not parsed.weights_confirmation_quote:
        errors.append("Recruiter confirmation of dimension weights is required.")
    if not supported_form_url(parsed.google_form_url):
        errors.append("Supply a supported Google Form URL before accepting (format validation only).")
    return errors


def public_profile_section(profile: dict) -> str:
    """One deterministic public section shared by draft and acceptance checks."""
    lines = ["## Selection Criteria"]
    for priority in ("Must have", "Preferred", None):
        criteria = [c for c in profile["criteria"] if c["priority"] == priority]
        if criteria:
            lines.extend(["", f"### {priority or 'Priority to confirm'}"])
            lines.extend(f"- {c['text']}" for c in criteria)
    if profile.get("minimum_years_experience") is not None:
        lines.extend(["", f"Minimum experience: {profile['minimum_years_experience']}"])
    if profile.get("fresher_eligibility") is not None:
        lines.extend(["", f"Fresher eligibility: {profile['fresher_eligibility']}"])
    if supported_form_url(profile.get("google_form_url")):
        lines.extend(["", "## Application Information", "", f"Apply: {profile['google_form_url']}"])
    return "\n".join(lines)


def acceptance_errors(state: dict) -> list[str]:
    errors = profile_errors(state.get("screening_profile"))
    profile = state.get("screening_profile")
    if not isinstance(state.get("profile_version"), int) or state["profile_version"] < 1:
        errors.append("A screening profile version is required.")
    draft = state.get("approved_jd") if state.get("status") == "approved" else state.get("draft_jd")
    try:
        section = public_profile_section(profile) if profile else None
    except (KeyError, TypeError):
        section = None
    if not section or section not in (draft or ""):
        errors.append("The draft and screening profile must be updated together before acceptance.")
    if contains_internal_metadata(draft or ""):
        errors.append("Remove internal screening weights and IDs from the public advertisement.")
    if state.get("draft_profile_version") != state.get("profile_version"):
        errors.append("The draft does not reflect the current screening profile version.")
    return errors
