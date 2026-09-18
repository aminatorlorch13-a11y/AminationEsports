from sqlalchemy import text
from app import app, db

with app.app_context():
    engine = db.engine

    if engine.dialect.name != "postgresql":
        raise SystemExit(
            f"Refusing migration: expected PostgreSQL, got {engine.dialect.name}"
        )

    with engine.begin() as conn:
        conn.execute(text("""
            ALTER TABLE match
            ALTER COLUMN player1_id DROP NOT NULL
        """))

        conn.execute(text("""
            ALTER TABLE match
            ALTER COLUMN player2_id DROP NOT NULL
        """))

    print("SUCCESS: match.player1_id and match.player2_id are now nullable.")
