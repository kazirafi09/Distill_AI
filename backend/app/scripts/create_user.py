import argparse
from getpass import getpass

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.security import hash_password
from app.models.user import User


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a staff account")
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--role",
        required=True,
        choices=["recruiter", "interviewer"],
    )
    args = parser.parse_args()

    try:
        email = str(
            TypeAdapter(EmailStr).validate_python(args.email.strip())
        ).lower()
    except ValidationError:
        parser.error("Enter a valid email address.")

    password = getpass("Password: ")
    confirmation = getpass("Confirm password: ")

    if len(password) < 12:
        parser.error("Use a password with at least 12 characters.")

    if password != confirmation:
        parser.error("Passwords do not match.")

    user = User(
        email=email,
        password_hash=hash_password(password),
        role=args.role,
    )

    script_engine = create_engine(
        get_settings().database_url,
        poolclass=NullPool,
        connect_args={"connect_timeout": 5},
    )

    try:
        with Session(script_engine) as session:
            session.add(user)
            session.commit()
    except IntegrityError:
        raise SystemExit(
            "Account creation failed: check whether the email already exists."
        ) from None
    except SQLAlchemyError:
        raise SystemExit(
            "Account creation failed: check database availability."
        ) from None
    finally:
        script_engine.dispose()

    print(f"Created {args.role} account: {email}")


if __name__ == "__main__":
    main()