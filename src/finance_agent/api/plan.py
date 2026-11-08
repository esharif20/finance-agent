"""Allocation Plans (#267): next month's expected income split across bills,
goals and Categories under the user's confirmed constraints, beside a 50/30/20
split. A thin adapter over `assistant.tools.plan.allocation_plan`, which the
Assistant's Plan tool shares. Read-only: nothing is scheduled, moved or paid."""

from datetime import date

from fastapi import APIRouter

from finance_agent.api.guidance import Conn
from finance_agent.assistant.tools.plan import (
    AllocationPlanIn,
    AllocationPlanOut,
    allocation_plan,
)
from finance_agent.assistant.tools.read import Source
from finance_agent.identity import current_user

router = APIRouter(prefix="/api/plan")


@router.get("/allocation")
def allocation(conn: Conn) -> AllocationPlanOut:
    return allocation_plan(
        Source(conn, current_user(), date.today()), AllocationPlanIn()
    )
