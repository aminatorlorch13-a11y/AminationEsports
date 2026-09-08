import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
ENV_FILE = os.path.join(BASE_DIR, ".env")


def load_env_file():
    """
    Load simple KEY=VALUE entries from the local .env file.
    Existing environment variables take priority.
    """
    if not os.path.exists(ENV_FILE):
        return

    with open(ENV_FILE, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            if key and key not in os.environ:
                os.environ[key] = value


load_env_file()


class Config:
    # Use PostgreSQL in production when DATABASE_URL is provided.
    # Keep the existing SQLite database for local development.
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "sqlite:///" + os.path.join(
            BASE_DIR,
            "instance",
            "amination_esports.db"
        )
    )

    # Some hosting providers use the postgres:// prefix.
    # SQLAlchemy expects postgresql://.
    if SQLALCHEMY_DATABASE_URI.startswith("postgres://"):
        SQLALCHEMY_DATABASE_URI = (
            "postgresql://"
            + SQLALCHEMY_DATABASE_URI[len("postgres://"):]
        )

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Maximum HTTP request size for uploads.
    # 100 MB is large enough for high-quality FC Mobile
    # highlight clips while preventing unexpectedly large
    # requests from reaching the application.
    MAX_CONTENT_LENGTH = 100 * 1024 * 1024

    # File extensions accepted for Amination Esports highlights.
    # Actual video/container validation is performed separately.
    ALLOWED_HIGHLIGHT_VIDEO_EXTENSIONS = {
        ".mp4",
        ".mov",
        ".webm",
        ".m4v",
    }

    SECRET_KEY = os.environ.get(
        "SECRET_KEY",
        "amination-development-key"
    )

    FOUNDER_NAME = os.environ.get(
        "FOUNDER_NAME",
        "Amin Shabangu"
    )

    FOUNDER_EMAIL = os.environ.get(
        "FOUNDER_EMAIL",
        "aminatorlorch13@gmail.com"
    )

    FOUNDER_PASSWORD = os.environ.get(
        "FOUNDER_PASSWORD",
        ""
    )


    CLOUDINARY_CLOUD_NAME = os.environ.get(
        "CLOUDINARY_CLOUD_NAME",
        ""
    )

    CLOUDINARY_API_KEY = os.environ.get(
        "CLOUDINARY_API_KEY",
        ""
    )

    CLOUDINARY_API_SECRET = os.environ.get(
        "CLOUDINARY_API_SECRET",
        ""
    )
