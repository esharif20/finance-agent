"""The built-in Manage links (#174): the reviewed list of where to manage a
subscription. Read-only and the same for every user; the web app matches a
Merchant name against it and never takes a link from anywhere else."""

from fastapi import APIRouter
from pydantic import BaseModel

from finance_agent.categorise.manage_links import MANAGE_LINKS

router = APIRouter(prefix="/api/manage-links")


class ManageLinkOut(BaseModel):
    names: list[str]
    domain: str
    url: str


@router.get("")
def manage_links() -> list[ManageLinkOut]:
    return [
        ManageLinkOut(names=list(e.names), domain=e.domain, url=e.url)
        for e in MANAGE_LINKS
    ]
