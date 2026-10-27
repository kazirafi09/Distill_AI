"""The default invitation is shared by the recruiter preview and sent email."""
import re

from app.services.interviews.scheduling import schedule_label


def invitation_subject(job_title):
    return ("Interview invitation | " + " ".join(job_title.split()))[:250]


def invitation_body(job_title, candidate_name="{candidate_name}", schedule=None,
                    link="{interview_link}", *, include_calendar=True):
    when = schedule_label(schedule) if schedule else "{interview_schedule}"
    body = (f"Dear {candidate_name},\n\n"
        f"Thank you for your interest in the {job_title} position. We are pleased to invite you "
        "to an online interview with our recruitment team.\n\n"
        f"Interview schedule: {when}\n"
        "Location: Distill AI online interview\n\n"
        f"Your personal interview link:\n{link}\n\n"
        "You may join from 10 minutes before the scheduled start until the scheduled end. "
        "Please check your camera and microphone in advance and wait for the interviewer to admit you. "
        "Choose a quiet location with a reliable internet connection.\n\n")
    return body + ("This invitation link is personal to you; please do not share it. "
        "If you need a different interview time, reply to this email to contact the recruitment team.\n\n"
        "We look forward to speaking with you.\n\nBest regards,\nRecruitment Team\nDistill AI")


def private_interview_link(payload):
    # Old queue messages stored the link only in the body. Keep those sendable.
    link = payload.get("interview_link")
    if link is None:
        match = re.search(r"https?://[^\s]+/interview#[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])", payload["body"])
        link = match[0] if match else ""
    if not re.fullmatch(r"https?://[^\s]+/interview#[A-Za-z0-9_-]{43}", link):
        raise ValueError("Stored interview link is invalid")
    return link
