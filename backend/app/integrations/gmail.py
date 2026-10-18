"""Send one email through Gmail SMTP with an app password; never retry here."""
import smtplib
import ssl
from email.message import EmailMessage
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, TypeAdapter, field_validator
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings


class EmailMessageRequest(BaseModel):
    recipient: EmailStr = Field(repr=False)
    subject: str = Field(min_length=1, max_length=250, repr=False)
    body: str = Field(min_length=1, max_length=100_000, repr=False)

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value):
        if not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Subject must be nonempty and contain no control characters.")
        return value


class EmailSendResult(BaseModel):
    status: Literal["provider_accepted", "failed", "uncertain"]
    provider_message_id: str | None = None
    error_code: str | None = None


def send_smtp(settings, email):
    client = None
    send_started = False
    try:
        client = smtplib.SMTP(settings.smtp_host, settings.smtp_port,
                              timeout=settings.smtp_timeout_seconds)
        client.starttls(context=ssl.create_default_context())
        client.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        send_started = True
        refused = client.send_message(
            email, from_addr=str(email["From"]), to_addrs=[str(email["To"])],
        )
        if refused:
            return EmailSendResult(status="failed", error_code="SMTP_RECIPIENT_REJECTED")
        return EmailSendResult(status="provider_accepted")
    except smtplib.SMTPAuthenticationError:
        return EmailSendResult(status="failed", error_code="SMTP_AUTHENTICATION_FAILED")
    except smtplib.SMTPRecipientsRefused:
        return EmailSendResult(status="failed", error_code="SMTP_RECIPIENT_REJECTED")
    except smtplib.SMTPResponseException as exc:
        return EmailSendResult(status="failed", error_code=f"SMTP_REJECTED_{exc.smtp_code}")
    except (smtplib.SMTPNotSupportedError, UnicodeError):
        return EmailSendResult(status="failed", error_code="SMTP_MESSAGE_OR_TLS_UNSUPPORTED")
    except ValueError:
        return EmailSendResult(status="failed", error_code="SMTP_CONFIGURATION_INVALID")
    except OSError:
        # A lost connection during send may occur after Gmail accepted the email.
        status = "uncertain" if send_started else "failed"
        return EmailSendResult(status=status, error_code="SMTP_CONNECTION_ERROR")
    finally:
        if client is not None:
            try:
                client.close()
            except OSError:
                pass  # Closing a local socket must not change the sending result.


async def send_email(message: EmailMessageRequest) -> EmailSendResult:
    settings = get_settings()
    if not settings.smtp_username or not settings.smtp_password or not settings.smtp_password.get_secret_value():
        return EmailSendResult(status="failed", error_code="SMTP_NOT_CONFIGURED")
    try:
        sender = TypeAdapter(EmailStr).validate_python(settings.smtp_username)
        email = EmailMessage()
        email["From"] = sender
        email["To"] = str(message.recipient)
        email["Subject"] = message.subject
        email.set_content(message.body)
    except ValueError:
        return EmailSendResult(status="failed", error_code="EMAIL_CONFIGURATION_INVALID")
    # smtplib is synchronous; keep blocking SMTP operations off the async loop.
    return await run_in_threadpool(send_smtp, settings, email)
