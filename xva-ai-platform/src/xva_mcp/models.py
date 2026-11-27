"""Typed results for every tool. Each result carries provenance so the agent can cite it."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

Level = Literal["desk", "entity", "book", "counterparty"]
AssetClass = Literal["IR", "FX", "EQ", "CO", "CR", "CARRY"]
# Sensitivity measures and XVA components come from the data dictionary, so they are plain strings here
# and are checked against the dictionary when a tool runs.
SensMeasure = str


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
    components: dict[str, float] = Field(description="Value per XVA component, e.g. CVA, DVA or DVACO, FVA")
    total: float = Field(description="Sum of the components that make up total XVA (see the data dictionary)")


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
    component_changes: dict[str, float] = Field(description="Change per XVA component")


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
    components: dict[str, float]
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
    component: str = Field(description="Which XVA component the sensitivity is of, e.g. XVA, CVA or FVA")
    group_by: Literal["counterparty", "entity", "risk_factor", "bucket"]
    counterparty_id: str | None
    rows: list[SensRow]
    total: float
    truncated: bool
    provenance: Provenance


class DriverRow(BaseModel):
    driver: str
    value: float
    group: str | None = Field(default=None, description="Driver group from the data dictionary, e.g. Interest rates")


class PnlAttribution(BaseModel):
    start: date = Field(description="Base date (exclusive)")
    end: date = Field(description="End date (inclusive)")
    level: Level
    key: str | None
    component: str | None = Field(default=None, description="XVA (total), CVA or FVA; None = as stored")
    xva_change: float = Field(description="Change in the component (or total XVA) between start and end")
    actual_pl: float | None = Field(
        default=None, description="Actual P&L from the P&L system for the period (None when it is not loaded)"
    )
    expected_pl: float | None = Field(
        default=None, description="Total expected P&L from the P&L system: the drivers except temporary items and unexplained"
    )
    groups: list[DriverRow] = Field(
        default_factory=list, description="Explain by driver group (sums of the drivers), in the dictionary's order"
    )
    drivers: list[DriverRow] = Field(description="Explain by driver, including 'unexplained'; zero drivers are omitted")
    unexplained: float
    unexplained_flag: bool = Field(description="True when unexplained breaches the dictionary thresholds")
    reconciliation_basis: Literal["actual_pl", "xva_change"] = Field(
        default="xva_change", description="What the drivers are tied to: actual_pl when loaded, else xva_change"
    )
    reconciliation_gap: float = Field(
        description="actual_pl (or xva_change when no actual P&L is loaded) minus the sum of drivers; "
        "non-zero means the explain does not tie out"
    )
    provenance: Provenance


class CounterpartyInfo(BaseModel):
    counterparty_id: str
    name: str
    rating: str | None = None
    sector: str | None = None
    region: str | None = None
    book: str | None = None
    segment: str | None = Field(default=None, description="Desk segment, e.g. SSA")
    credit_curve: str | None = Field(default=None, description="Credit curve used for the counterparty in XVA")
    customer_type: str | None = None
    cpty_type: str | None = None


class CounterpartySearch(BaseModel):
    query: str
    matches: list[CounterpartyInfo]


class DefinitionResult(BaseModel):
    term: str
    found: bool
    entry: dict | None = None
    available_terms: list[str] = []


class XvaHistory(BaseModel):
    start: date
    end: date
    basis: str = Field(description="Where the daily desk totals come from")
    points: list[XvaPoint]
    first: XvaPoint | None = None
    last: XvaPoint | None = None
    change: float | None = Field(default=None, description="Total XVA change from the first to the last point")
    provenance: Provenance



VARIANT_NOTE = (
    "Variants are test calculations of the same counterparty (codes containing XVATEST). Only the official "
    "counterparty is part of desk, entity and book totals; a variant's figures are never added to any total."
)


class VariantValueRow(BaseModel):
    counterparty_id: str
    role: Literal["official", "variant"]
    method: str | None = Field(default=None, description="What the variant calculates differently (variants.yaml)")
    owner: str | None = None
    loaded: bool = Field(description="False when no values are loaded for this counterparty on the date")
    components: dict[str, float] = Field(description="XVA by component")
    total: float = Field(description="Sum of the in-total components, worked out as for official figures")
    total_vs_official: float | None = Field(
        default=None, description="This row's total minus the official counterparty's total"
    )


class VariantSensRow(BaseModel):
    asset_class: str
    measure: str
    risk_factor: str | None = Field(default=None, description="Set in risk-factor rows; None in asset-class totals")
    unit: str | None = None
    values: dict[str, float] = Field(
        description="Counterparty ID -> value; a counterparty without this measure loaded is left out (not zero)"
    )
    largest_difference: float | None = Field(
        default=None, description="Largest absolute difference between a variant and the official value"
    )


class VariantPnlRow(BaseModel):
    group: str
    values: dict[str, float] = Field(description="Counterparty ID -> P&L")


class VariantComparison(BaseModel):
    as_of: date
    pnl_start: date | None = Field(description="P&L covers after pnl_start up to as_of")
    base_id: str = Field(description="The official counterparty")
    component: str = Field(description="XVA component for the sensitivities and P&L")
    counterparties: list[str] = Field(description="Official counterparty first, then its variants")
    values: list[VariantValueRow]
    sensitivities: list[VariantSensRow] = Field(description="By asset class and measure")
    risk_factor_differences: list[VariantSensRow] = Field(
        description="Risk factors where the variants differ most from the official counterparty"
    )
    pnl_groups: list[VariantPnlRow]
    actual_pl: dict[str, float] = Field(description="Counterparty ID -> actual P&L for the period")
    missing: list[str] = Field(description="What is not loaded for this date (per counterparty and dataset)")
    variant_run_ids: list[str]
    note: str = VARIANT_NOTE
    provenance: Provenance
