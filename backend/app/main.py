"""
main.py

The RedHouse API. Run locally with:
    uvicorn app.main:app --reload
then open http://127.0.0.1:8000/docs for an interactive page that
lists every endpoint and lets you try them in the browser.
"""

import hmac
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mangum import Mangum

from app.routes import demo, plan, ring_cameras, ring_status, scene, sites, explain

app = FastAPI(title="RedHouse API", version="0.1.0")

# NOTE: CORS is the browser rule that blocks a web page on one address
# (our React app, localhost:5173) from calling an API on another
# (localhost:8000). We explicitly allow the React dev server.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# NOTE: on AWS the server sits behind CloudFront, which adds a secret header to every request it forwards.
# When REDHOUSE_ORIGIN_SECRET is set, anything without that header (someone calling the server's address
# directly, around CloudFront) is refused. Locally the variable is not set and this does nothing.
@app.middleware("http")
async def only_from_cloudfront(request: Request, call_next):
    secret = os.environ.get("REDHOUSE_ORIGIN_SECRET")
    if secret and not hmac.compare_digest(request.headers.get("x-origin-verify", ""), secret):
        return JSONResponse({"detail": "Not found."}, status_code=403)
    return await call_next(request)


app.include_router(demo.router)
app.include_router(ring_cameras.router)
app.include_router(ring_status.router)
app.include_router(scene.router)
app.include_router(plan.router)
app.include_router(sites.router)
app.include_router(explain.router)


# NOTE: the first "best setup" answer takes about ten seconds to work out, and is then remembered. On AWS the
# server works out the demo house's answer as soon as it starts, so the first visitor does not wait for it.
# (REDHOUSE_WARMUP=1 turns this on; it is off locally and in tests.)
def _warm_up():
    try:
        from app.routes.plan import get_plan
        from app.routes.ring_status import get_site
        get_plan(archetype="opportunist", site=get_site(site=None))
    except Exception:
        pass                                            # warming is only a speed-up; never stop the server over it


if os.environ.get("REDHOUSE_WARMUP"):
    import threading
    threading.Thread(target=_warm_up, daemon=True).start()


@app.get("/health")
def health():
    """Tiny check that the server is up. AWS will use this later too."""
    return {"status": "ok"}


# NOTE: Mangum is the adapter that lets this same FastAPI app run on
# AWS Lambda. Locally it does nothing; on AWS, Lambda calls `handler`.
handler = Mangum(app)
