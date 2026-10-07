from uuid import UUID, uuid4

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from app.core.config import get_settings
from app.services.discord_webhook import (
    DiscordWebhookError,
    get_webhook_url,
    send_discord_job,
)


def post_job_to_discord(
    recruiter_id: UUID,
    *,
    job_description_id: UUID | None = None,
    job_number: int | None = None,
) -> dict[str, object]:
    """Post an owned, approved JD once to the configured Discord channel."""
    if job_description_id is None and job_number is None:
        return {
            "status": "failed",
            "error_code": "JOB_SELECTION_REQUIRED",
        }

    if job_number is not None and job_number < 1:
        return {
            "status": "failed",
            "error_code": "INVALID_JOB_NUMBER",
        }

    # Check configuration before creating a posting record.
    try:
        get_webhook_url()
    except DiscordWebhookError as exc:
        return {
            "status": "failed",
            "error_code": exc.code,
        }

    settings = get_settings()
    connection_info = make_conninfo(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        connect_timeout=5,
    )

    conditions = [
        "conversation.recruiter_id = %s",
        "recruiter.role = 'recruiter'",
        "recruiter.is_active = TRUE",
    ]
    parameters: list[object] = [recruiter_id]

    if job_description_id is not None:
        conditions.append("jd.id = %s")
        parameters.append(job_description_id)

    if job_number is not None:
        conditions.append("jd.number = %s")
        parameters.append(job_number)

    send_started = False
    receipt: dict[str, str] | None = None

    try:
        with psycopg.connect(
            connection_info,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT jd.id, jd.number, jd.title, jd.content
                    FROM job_descriptions AS jd
                    JOIN jd_conversations AS conversation
                      ON conversation.id = jd.conversation_id
                    JOIN users AS recruiter
                      ON recruiter.id = conversation.recruiter_id
                    WHERE
                    """ + " AND ".join(conditions),
                    parameters,
                )
                document = cursor.fetchone()

                if document is None:
                    return {
                        "status": "failed",
                        "error_code": "JOB_NOT_FOUND",
                    }

                cursor.execute(
                    """
                    INSERT INTO discord_job_posts (
                        id, job_description_id, status
                    )
                    VALUES (%s, %s, 'sending')
                    ON CONFLICT (job_description_id) DO NOTHING
                    RETURNING id
                    """,
                    (uuid4(), document["id"]),
                )
                reserved = cursor.fetchone()

                if reserved is None:
                    cursor.execute(
                        """
                        SELECT status, message_url, error_code
                        FROM discord_job_posts
                        WHERE job_description_id = %s
                        """,
                        (document["id"],),
                    )
                    existing = cursor.fetchone()

                    return {
                        "status": existing["status"],
                        "job_number": document["number"],
                        "title": document["title"],
                        "message_url": existing["message_url"],
                        "error_code": existing["error_code"],
                        "already_recorded": True,
                        "message": (
                            "No new message was sent. An existing posting "
                            "record blocks automatic reposting."
                        ),
                    }

                post_id = reserved["id"]

                # Persist the reservation before contacting Discord.
                # Concurrent calls and restart recovery see this record.
                connection.commit()

                try:
                    send_started = True
                    receipt = send_discord_job(
                        number=document["number"],
                        title=document["title"],
                        content=document["content"],
                    )
                except DiscordWebhookError as exc:
                    status = "unknown" if exc.uncertain else "failed"

                    cursor.execute(
                        """
                        UPDATE discord_job_posts
                        SET status = %s,
                            error_code = %s,
                            updated_at = now()
                        WHERE id = %s
                        """,
                        (status, exc.code, post_id),
                    )
                    connection.commit()

                    return {
                        "status": status,
                        "job_number": document["number"],
                        "title": document["title"],
                        "error_code": exc.code,
                    }

                cursor.execute(
                    """
                    UPDATE discord_job_posts
                    SET status = 'posted',
                        channel_id = %s,
                        message_id = %s,
                        message_url = %s,
                        error_code = NULL,
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (
                        receipt["channel_id"],
                        receipt["message_id"],
                        receipt["message_url"],
                        post_id,
                    ),
                )
                connection.commit()

                return {
                    "status": "posted",
                    "job_number": document["number"],
                    "title": document["title"],
                    **receipt,
                    "already_recorded": False,
                }

    except psycopg.Error:
        # Never expose connection details or exception text.
        if receipt is not None:
            return {
                "status": "posted",
                **receipt,
                "error_code": "DISCORD_POST_RECORD_SAVE_FAILED",
                "message": (
                    "Discord confirmed the post, but saving its receipt "
                    "failed. Do not repost; inspect the saved record."
                ),
            }

        return {
            "status": "unknown" if send_started else "failed",
            "error_code": "DISCORD_POST_DATABASE_ERROR",
        }