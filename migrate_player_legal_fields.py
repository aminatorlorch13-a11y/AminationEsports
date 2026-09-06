from app import app, db
from sqlalchemy import inspect, text

print("===== AMINATION ESPORTS PLAYER LEGAL MIGRATION =====")

with app.app_context():
    backend = db.engine.url.get_backend_name()

    print("Database:", backend)

    if backend != "postgresql":
        raise RuntimeError(
            "Migration refused: this script must run against PostgreSQL."
        )

    inspector = inspect(db.engine)

    existing = {
        column["name"]
        for column in inspector.get_columns("player")
    }

    migrations = {
        "date_of_birth": (
            "DATE"
        ),
        "competent_person_consent_status": (
            'VARCHAR(30) NOT NULL DEFAULT \'unknown\''
        ),
        "competent_person_consent_at": (
            "TIMESTAMP"
        ),
    }

    print()
    print("===== PLAYER TABLE =====")

    for name, sql_type in migrations.items():
        if name in existing:
            print(f"{name}: EXISTS")
            continue

        print(f"{name}: ADDING")

        db.session.execute(
            text(
                f'ALTER TABLE player '
                f'ADD COLUMN "{name}" {sql_type}'
            )
        )

    db.session.commit()

    print()
    print("===== MIGRATION COMPLETE =====")

    inspector = inspect(db.engine)

    print()
    print("===== PLAYER LEGAL COLUMNS NOW =====")

    for column in inspector.get_columns("player"):
        if column["name"] in migrations:
            print(
                f'{column["name"]} '
                f'({column["type"]}) '
                f'nullable={column["nullable"]}'
            )

print("===== DONE =====")
