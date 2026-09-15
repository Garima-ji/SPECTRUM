from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database.models import ClaimRecord
from app.utils.constants import VERDICT_ALIASES

# Update DB URL if necessary
DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/spectrum"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def migrate_verdicts():
    db = SessionLocal()
    try:
        claims = db.query(ClaimRecord).all()
        updated_count = 0
        for claim in claims:
            old_verdict = claim.verdict
            if old_verdict in VERDICT_ALIASES:
                new_verdict = VERDICT_ALIASES[old_verdict]
                if new_verdict != old_verdict:
                    claim.verdict = new_verdict
                    updated_count += 1
        db.commit()
        print(f"Migrated {updated_count} legacy verdicts.")
    finally:
        db.close()

if __name__ == "__main__":
    migrate_verdicts()
