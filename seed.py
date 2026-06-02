from datetime import date, datetime, timezone

from app.database import Base, DATABASE_URL, SessionLocal, engine
from app.models import Opportunity


def seed() -> None:
    if not DATABASE_URL.startswith("sqlite"):
        raise RuntimeError(
            "seed.py is local-development fixtures only and must not run against DATABASE_URL. "
            "Populate production with app.ingest_all or the protected /api/ingest endpoint."
        )

    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        db.query(Opportunity).delete()

        opportunities = [
            Opportunity(
                id="IUK-2026-ADV-MAN-001",
                source="Innovate UK",
                title="Advanced Manufacturing Scale-Up Competition",
                url="https://apply-for-innovation-funding.service.gov.uk/competition/1832/overview",
                opened_date=date(2026, 1, 8),
                closes_date=date(2026, 4, 24),
                funding_min=100000,
                funding_max=1500000,
                sector_tags="Manufacturing, Productivity",
                niche_tags="Automation, Robotics",
                description="Supports UK SMEs developing production-ready automation technologies to improve factory throughput and energy efficiency.",
                status="open",
                last_seen=datetime.now(timezone.utc),
            ),
            Opportunity(
                id="IUK-2026-NETZERO-004",
                source="Innovate UK",
                title="Net Zero Heat for Commercial Buildings",
                url="https://apply-for-innovation-funding.service.gov.uk/competition/1845/overview",
                opened_date=date(2026, 2, 3),
                closes_date=date(2026, 5, 29),
                funding_min=75000,
                funding_max=900000,
                sector_tags="Energy, Built Environment",
                niche_tags="Heat Pumps, Retrofit",
                description="Funds SME-led projects that demonstrate low-carbon heating solutions suitable for offices, retail units, and mixed-use assets.",
                status="open",
                last_seen=datetime.now(timezone.utc),
            ),
            Opportunity(
                id="KONFER-ROLL-DIGITAL-021",
                source="Konfer",
                title="Digital Adoption Grant Calls (Rolling)",
                url="https://www.konfer.online/funding/digital-adoption-grants-rolling",
                opened_date=date(2025, 10, 1),
                closes_date=None,
                funding_min=10000,
                funding_max=120000,
                sector_tags="Technology, Business Support",
                niche_tags="Cloud, Cyber Security",
                description="Rolling opportunities surfaced on Konfer for SMEs implementing digital systems, cyber resilience, and process automation.",
                status="rolling",
                last_seen=datetime.now(timezone.utc),
            ),
            Opportunity(
                id="KONFER-ROLL-LIFESCI-034",
                source="Konfer",
                title="Life Sciences Collaborative R&D (Rolling)",
                url="https://www.konfer.online/funding/life-sciences-collaborative-r-and-d",
                opened_date=date(2025, 11, 12),
                closes_date=None,
                funding_min=50000,
                funding_max=500000,
                sector_tags="Health, Life Sciences",
                niche_tags="Diagnostics, MedTech",
                description="Rolling UK opportunities for SME-led diagnostic and medical device feasibility studies with academic or NHS partners.",
                status="rolling",
                last_seen=datetime.now(timezone.utc),
            ),
            Opportunity(
                id="IUK-2026-AI-SUPPLY-009",
                source="Innovate UK",
                title="AI for Resilient Supply Chains",
                url="https://apply-for-innovation-funding.service.gov.uk/competition/1861/overview",
                opened_date=date(2026, 6, 15),
                closes_date=date(2026, 9, 30),
                funding_min=150000,
                funding_max=2000000,
                sector_tags="AI, Logistics",
                niche_tags="Demand Forecasting, Risk Analytics",
                description="Upcoming competition for UK SMEs applying artificial intelligence to improve supply chain forecasting, traceability, and disruption planning.",
                status="upcoming",
                last_seen=datetime.now(timezone.utc),
            ),
        ]

        db.add_all(opportunities)
        db.commit()
        print("Seeded 5 opportunities into find_a_grant.db")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
