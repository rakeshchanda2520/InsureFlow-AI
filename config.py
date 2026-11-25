"""Settings shared by every module."""
import os
import re
from pathlib import Path

from dotenv import load_dotenv

# Loaded before Andromeda is imported: its Langfuse tracing reads LANGFUSE_* at import time.
load_dotenv()

from andromeda.config import ModelConfig  # noqa: E402

ROOT = Path(__file__).parent
DATA = ROOT / "data"
DB_URL = f"sqlite:///{DATA / 'insureflow.db'}"

MODEL = ModelConfig(name=os.getenv("INSUREFLOW_MODEL", "gpt-4o-mini"), provider="openai", temperature=0)

DUPLICATE_WINDOW_DAYS = 3  # same vehicle, incident dates this close together = suspected duplicate
FREQUENCY_WINDOW_DAYS = 365
FREQUENCY_LIMIT = 3  # prior claims in the window at which frequency is flagged
LATE_REPORT_DAYS = 7
EARLY_CLAIM_DAYS = 30  # claim this soon after the policy started
FRAUD_MEDIUM, FRAUD_HIGH = 30, 60


def inr(amount: float) -> str:
    """Format as Indian rupees with lakh/crore grouping, e.g. 120000 -> ₹1,20,000."""
    digits = f"{round(amount):d}"
    head, tail = digits[:-3], digits[-3:]
    if not head:
        return f"₹{tail}"
    return "₹" + re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head) + "," + tail
