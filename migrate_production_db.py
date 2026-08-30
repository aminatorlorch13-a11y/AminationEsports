from app import app, db
from sqlalchemy import inspect, text

print("===== AMINATION ESPORTS DATABASE MIGRATION =====")

with app.app_context():
    print("Database:", db.engine.url.get_backend_name())

    if db.engine.url.get_backend_name() != "postgresql":
        raise RuntimeError(
            "Migration refused: this script must run against PostgreSQL."
        )

    inspector = inspect(db.engine)

    tournament_columns = {
        "payment_enabled": (
            'BOOLEAN NOT NULL DEFAULT FALSE'
        ),
        "currency": (
            "VARCHAR(10) NOT NULL DEFAULT 'ZAR'"
        ),
        "international_enabled": (
            'BOOLEAN NOT NULL DEFAULT TRUE'
        ),
        "season_number": (
            "INTEGER"
        ),
        "whatsapp_group_link": (
            "VARCHAR(500)"
        ),
        "payment_instructions": (
            "TEXT"
        ),
        "payment_deadline": (
            "TIMESTAMP"
        ),
        "availability_deadline": (
            "TIMESTAMP"
        ),
        "completed_at": (
            "TIMESTAMP"
        ),
        "champion_id": (
            "INTEGER"
        ),
        "runner_up_id": (
            "INTEGER"
        ),
    }

    existing = {
        column["name"]
        for column in inspector.get_columns("tournament")
    }

    print()
    print("===== TOURNAMENT TABLE =====")

    for name, sql_type in tournament_columns.items():

        if name in existing:
            print(f"{name}: EXISTS")
            continue

        print(f"{name}: ADDING")

        db.session.execute(
            text(
                f'ALTER TABLE tournament '
                f'ADD COLUMN "{name}" {sql_type}'
            )
        )

    db.session.commit()

    print()
    print("===== MIGRATION COMPLETE =====")

    inspector = inspect(db.engine)

    print()
    print("===== TOURNAMENT COLUMNS NOW =====")

    for column in inspector.get_columns("tournament"):
        print(
            f'{column["name"]} '
            f'({column["type"]})'
        )

print()
print("===== DONE =====")
