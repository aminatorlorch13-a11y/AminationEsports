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
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(
        BASE_DIR,
        "instance",
        "amination_esports.db"
    )

    SQLALCHEMY_TRACK_MODIFICATIONS = False

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
