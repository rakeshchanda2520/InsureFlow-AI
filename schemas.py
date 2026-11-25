"""Pydantic models for what enters and leaves the pipeline."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

IncidentType = Literal["accident", "theft", "fire", "flood", "vandalism"]
DOCS_ALL = ["claim_form", "rc_copy", "driving_licence", "policy_copy", "photos", "repair_estimate"]


class ClaimRequest(BaseModel):
    """A claim as submitted by the customer. Validated before any agent sees it."""

    customer_id: str
    policy_id: str
    incident_type: IncidentType
    incident_date: date
    reported_date: date
    location: str = Field(min_length=3)
    description: str = Field(min_length=10)
    estimated_damage: float = Field(gt=0)
    damaged_parts: list[str] = []
    third_party_involved: bool = False
    alcohol_involved: bool = False
    driver_licence_valid: bool = True
    documents: list[str] = []

    @field_validator("customer_id", "policy_id")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("damaged_parts", "documents")
    @classmethod
    def _clean(cls, values: list[str]) -> list[str]:
        return [v.strip().lower() for v in values if v.strip()]

    @model_validator(mode="after")
    def _dates(self) -> "ClaimRequest":
        if self.reported_date < self.incident_date:
            raise ValueError("reported_date cannot be before incident_date")
        if self.reported_date > date.today():
            raise ValueError("reported_date cannot be in the future")
        return self


class ClaimResult(BaseModel):
    """What process_claim hands back to the caller."""

    submission_id: str
    claim_id: str | None
    status: str
    recommendation: str | None = None
    payout: float = 0.0
    summary: str = ""
