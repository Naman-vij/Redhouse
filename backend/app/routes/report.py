"""
report.py (route)

    GET /api/report?site=<id>&archetype=opportunist      ->  a PDF

The report is built from the same answers the page shows: the plan, the scene today and after the suggested
changes, the camera-failure table, and the plain-words summary (Amazon Bedrock when it is set up). See app/report.py.
"""

import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from app import engine_bridge  # noqa: F401
from app import mailer
from app.report import build_report
from app.routes.explain import explanation_for
from app.routes.plan import get_plan
from app.routes.ring_status import get_site
from app.routes.scene import get_outage, get_scene

router = APIRouter(prefix="/api", tags=["report"])
_PER_ADDRESS = mailer.RateLimit(3, 3600)        # one address: 3 an hour
_PER_VISITOR = mailer.RateLimit(6, 3600)        # one visitor: 6 an hour
_ALL = mailer.RateLimit(60, 3600)               # everyone, per server process: 60 an hour


def _file_name(site_name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9]+", "-", site_name or "").strip("-").lower()[:40]
    return f"redhouse-report-{stem}.pdf" if stem else "redhouse-report.pdf"


def _make(archetype: str, site):
    """(site name, the PDF bytes, the plain-words text)"""
    twin, _ = site
    plan = get_plan(archetype=archetype, site=site)
    explained, _remembered = explanation_for(archetype, site)

    def scene(rotate, add):
        return get_scene(door=None, archetype=archetype, offline=None, rotate=rotate or None, add=add or None, site=site)

    today = scene(None, None)
    after = scene(plan["best_existing"].get("rotate_param"), plan["more_cameras"].get("add_param"))
    outage = get_outage(archetype=archetype, site=site)
    name = twin.name or "your site"
    pdf = build_report(site_name=name, archetype=archetype, plan=plan, explained=explained,
                       today=today, after=after, outage=outage)
    return name, pdf, explained["text"]


@router.get("/report")
def get_report(archetype: str = Query(default="opportunist"), site=Depends(get_site)):
    name, pdf, _ = _make(archetype, site)
    return Response(content=pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{_file_name(name)}"',
        "Cache-Control": "no-store",
    })


class EmailBody(BaseModel):
    email: str


def _visitor(request: Request) -> str:
    # behind CloudFront the real address is the first entry of X-Forwarded-For
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


@router.post("/report/email")
def email_report(body: EmailBody, request: Request, archetype: str = Query(default="opportunist"),
                 site=Depends(get_site)):
    """Send the same PDF to the address given. Changes nothing on the site."""
    try:
        if not mailer.sender():
            raise mailer.MailError(503, "Email is not set up on this site. Use the download button instead.")
        to = mailer.clean_address(body.email)
        if not (_PER_ADDRESS.allow(to.lower()) and _PER_VISITOR.allow(_visitor(request)) and _ALL.allow("all")):
            raise mailer.MailError(429, "Too many reports were requested. Please try again in an hour, or download it.")
        name, pdf, text = _make(archetype, site)
        mailer.send_report(to, name, text, pdf, _file_name(name))
    except mailer.MailError as err:
        raise HTTPException(status_code=err.status, detail=err.message)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
