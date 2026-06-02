from sqlalchemy import Column, Date, DateTime, Float, String, Text

from .database import Base


class AppMetadata(Base):
    __tablename__ = "app_metadata"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)


class Opportunity(Base):
    __tablename__ = "opportunities"

    id = Column(String, primary_key=True, index=True)
    source = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    url = Column(String, nullable=False)
    opened_date = Column(Date, nullable=True)
    closes_date = Column(Date, nullable=True)
    funding_min = Column(Float, nullable=True)
    funding_max = Column(Float, nullable=True)
    funding_currency = Column(String, nullable=True)
    funding_min_native = Column(Float, nullable=True)
    funding_max_native = Column(Float, nullable=True)
    exchange_rate = Column(Float, nullable=True)
    exchange_rate_date = Column(Date, nullable=True)
    sector_tags = Column(String, nullable=True)
    niche_tags = Column(String, nullable=True)
    summary = Column(Text, nullable=True)
    description = Column(Text, nullable=False)
    status = Column(String, nullable=False, index=True)
    last_seen = Column(DateTime, nullable=False, index=True)
