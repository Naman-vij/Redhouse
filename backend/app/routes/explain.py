"""
explain.py (route)

GET /api/explain?site=<id>&archetype=opportunist

A plain-language summary of the best-setup plan. Written by Amazon Bedrock when it is configured, with every
number checked against the simulator's figures (see app/explain.py); otherwise the built-in sentences.
Cached with the plan, so the model is asked once per site and attacker type.
"""

from fastapi import APIRouter, Depends, Query, Response

from app import engine_bridge  # noqa: F401
from app import explain as explainer
from app.routes.plan import _fingerprint, get_plan
from app.routes.ring_status import get_site

router = APIRouter(prefix="/api", tags=["explain"])
_CACHE: dict = {}


def explanation_for(archetype: str, site) -> tuple[dict, bool]:
    """(the explanation, whether it came from the cache or may be remembered). Used by this route and the PDF report."""
    twin, cameras = site
    key = (archetype, _fingerprint(twin, cameras))
    if key in _CACHE:
        return _CACHE[key], True
    plan = get_plan(archetype=archetype, site=site)
    out = explainer.explain(plan, camera_names=[c.name for c in cameras])
    if out["source"] == "bedrock" or out.get("note") == "Bedrock is not configured.":
        _CACHE[key] = out                              # a failed call is retried next time, not remembered
        if len(_CACHE) > 64:
            _CACHE.pop(next(iter(_CACHE)))
        return out, True
    return out, False


@router.get("/explain")
def get_explanation(response: Response, archetype: str = Query(default="opportunist"), site=Depends(get_site)):
    out, remembered = explanation_for(archetype, site)
    if not remembered:
        response.headers["Cache-Control"] = "no-store"
    return out
