"""Typed results for every tool. Each result carries provenance so the agent can cite it."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

Level = Literal["desk", "book", "counterparty"]
AssetClass = Literal["IR", "FX", "EQ", "CO", "CR"]
SensMeasure = Literal["delta", "vega", "cs01"]


class Provenance(BaseModel):
    source: str = Field(description="System of record the numbers come from")
    as_of: date | None = Field(default=None, description="Business date of the figures (end date for ranges)")
    run_ids: list[str] = Field(description="Official run identifiers used")
    unit: str
    sign_convention: str
    generated_at: datetime


class SourceStatus(BaseModel):
    source: str
    run_id: str | None
    loaded_at: datetime | None
    checks_passed: bool
    notes: str | None = None


class DataStatus(BaseModel):
    as_of: date
    all_passed: bool
    sources: list[SourceStatus]
    missing_sources: list[str]
    previous_business_date: date | None
    month_start_reference: date | None = Field(
        description="Last business date of the prior month, the base for month-to-date figures"
    )
    latest_clean_date: date | None = Field(description="Most recent date on which every source passed")


class XvaRow(BaseModel):
    key: str
    label: str
    cva: float
    dva: float
    fva: float
    total: float


class XvaSummary(BaseModel):
    as_of: date
    level: Level
    rows: list[XvaRow]
    totals: XvaRow = Field(description="Sum over all groups, not only the rows returned")
    truncated: bool
    provenance: Provenance


class MoverRow(BaseModel):
    key: str
    label: str
    total_start: float
    total_end: float
    change: float
    cva_change: float
    dva_change: float
    fva_change: float


class XvaMovers(BaseModel):
    start: date
    end: date
    level: Level
    rows: list[MoverRow] = Field(description="Sorted by absolute total change, largest first")
    total_change: float
    truncated: bool
    provenance: Provenance


class XvaPoint(BaseModel):
    as_of: date
    cva: float
    dva: float
    fva: float
    total: float


class CounterpartyTimeseries(BaseModel):
    counterparty_id: str
    name: str
    points: list[XvaPoint]
    provenance: Provenance


class SensRow(BaseModel):
    key: str
    label: str
    value: float


class SensitivityReport(BaseModel):
    as_of: date
    asset_class: AssetClass
    measure: SensMeasure
    group_by: Literal["counterparty", "risk_factor", "bucket"]
    counterparty_id: str | None
    rows: list[SensRow]
    total: float
    truncated: bool
    provenance: Provenance


class DriverRow(BaseModel):
    driver: str
    value: float


class PnlAttribution(BaseModel):
    start: date = Field(description="Base date (exclusive)")
    end: date = Field(description="End date (inclusive)")
    level: Level
    key: str | None
    xva_change: float = Field(description="Change in total XVA between start and end from the official values")
    drivers: list[DriverRow] = Field(description="Explain by driver, including 'unexplained'")
    unexplained: float
    unexplained_flag: bool = Field(description="True when unexplained breaches the dictionary thresholds")
    reconciliation_gap: float = Field(
        description="xva_change minus the sum of drivers; non-zero means the explain does not tie to the values"
    )
    provenance: Provenance


class CounterpartyInfo(BaseModel):
    counterparty_id: str
    name: str
    rating: str
    sector: str
    region: str
    book: str


class CounterpartySearch(BaseModel):
    query: str
    matches: list[CounterpartyInfo]


class DefinitionResult(BaseModel):
    term: str
    found: bool
    entry: dict | None = None
    available_terms: list[str] = []
