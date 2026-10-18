"""
report.py

The downloadable PDF report: what the simulation found, what to change, and what that changes.

WHAT IT HOLDS (about three pages)
  - the plain-words summary (written by Amazon Bedrock when it is set up, labelled either way)
  - three headline numbers: today, after turning the cameras you have, after adding cameras
  - the same numbers door by door (or spot by spot, indoors)
  - exactly what to do: which camera to turn and by how much, where each new camera goes
  - two maps: today, and after the suggested changes
  - what happens if a camera fails
  - how the numbers were worked out, and their limits

NOTHING IS INVENTED HERE. Every number comes from the same answers the page shows (plan, scene, outage).
This file only lays them out. The maps are drawn from the scene data as SVG, turned into pictures,
and placed in the page with PyMuPDF (already used for PDF uploads), so no extra library is needed.
"""

import html
import io
import math
import re
from datetime import datetime, timezone

import pymupdf

TEAL, RED, INK, INK2, INK3 = "#05968f", "#d43b2b", "#14232b", "#44585f", "#6c7f86"
PAPER, GROUND, HOUSE = "#f5f8f4", "#eaf1eb", "#cbd5cf"

COMPASS = ["east", "north-east", "north", "north-west", "west", "south-west", "south", "south-east"]


# ----------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------

def esc(x) -> str:
    return html.escape(str(x), quote=True)


def pct(x) -> int:
    return round(float(x) * 100)


def nice_name(ident: str) -> str:
    """front_door -> Front door"""
    text = str(ident).replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def _words(text: str) -> str:
    """front_door -> front door, in a sentence the engine wrote."""
    return re.sub(r"\b([A-Za-z]+)_([A-Za-z]+)\b", r"\1 \2", text)


def compass(deg: float) -> str:
    return COMPASS[round((((deg % 360) + 360) % 360) / 45) % 8]


# ----------------------------------------------------------------------
# the maps
# ----------------------------------------------------------------------

def _nice_bar(span: float) -> int:
    return 2 if span < 14 else 5 if span < 40 else 10 if span < 90 else 20


def map_png(scene: dict, width_px: int = 1300) -> bytes:
    """One scene (from /api/scene) -> a PNG picture of the plan with coverage, cameras, doors and, indoors, blind spots."""
    site = scene["site"]
    indoor = site["mode"] == "indoor"
    xs = [p[0] for p in site["boundary"]]
    ys = [p[1] for p in site["boundary"]]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    span = max(maxx - minx, maxy - miny)
    k = max(0.45, min(3.0, span / 24)) * (0.7 if indoor else 1.0)          # markers sized like the page's
    pad = 2.0 * k + 1.2
    bottom = pad + 2.2 * k
    vx, vy = minx - pad, -pad
    vw, vh = (maxx - minx) + 2 * pad, (maxy - miny) + pad + bottom
    Y = lambda y: maxy - y                                                   # the plan's y points north

    g = site["grid_m"]
    o = site["origin"]
    out = []
    add = out.append
    add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vx:.2f} {vy:.2f} {vw:.2f} {vh:.2f}" '
        f'width="{width_px}" height="{round(width_px * vh / vw)}" font-family="sans-serif">')
    add(f'<rect x="{vx:.2f}" y="{vy:.2f}" width="{vw:.2f}" height="{vh:.2f}" fill="{PAPER}"/>')
    pts = lambda poly: " ".join(f"{x:.2f},{Y(y):.2f}" for x, y in poly)
    add(f'<polygon points="{pts(site["boundary"])}" fill="{GROUND}"/>')

    # where the cameras can see: teal, paler = less likely to be seen
    cov = scene["coverage"]
    for r, row in enumerate(cov):
        for c, p in enumerate(row):
            if p >= 0.03:
                add(f'<rect x="{o[0] + c * g - 0.01:.2f}" y="{Y(o[1] + (r + 1) * g) - 0.01:.2f}" '
                    f'width="{g + 0.02:.2f}" height="{g + 0.02:.2f}" fill="{TEAL}" fill-opacity="{min(0.85, p) * 0.8:.2f}"/>')

    # indoors: the floor nobody watches, tinted red
    if indoor and scene.get("floor"):
        for r, c0, c1 in scene["floor"]["blind_runs"]:
            add(f'<rect x="{o[0] + c0 * g - 0.01:.2f}" y="{Y(o[1] + (r + 1) * g) - 0.01:.2f}" '
                f'width="{(c1 - c0) * g + 0.02:.2f}" height="{g + 0.02:.2f}" fill="{RED}" fill-opacity="0.22"/>')

    # the house (outdoors) or walls and furniture (indoors)
    for ob in site["obstacles"]:
        if indoor:
            add(f'<polygon points="{pts(ob)}" fill="{INK}" stroke="{INK}" stroke-width="0.04"/>')
        else:
            add(f'<polygon points="{pts(ob)}" fill="{HOUSE}" stroke="{INK}" stroke-width="{0.16 * k:.2f}"/>')
    add(f'<polygon points="{pts(site["boundary"])}" fill="none" stroke="{INK}" stroke-width="{0.12 * k:.2f}"/>')

    def halo_text(x, y, text, size, colour, bold=False):
        """Text with a pale outline so it stays readable on the heat map: an outlined copy, then the plain text on top."""
        weight = ' font-weight="bold"' if bold else ""
        base = f'x="{x:.2f}" y="{y:.2f}" font-family="sans-serif" font-size="{size:.2f}"{weight} text-anchor="middle"'
        add(f'<text {base} fill="{PAPER}" stroke="{PAPER}" stroke-width="{0.35 * k:.2f}" stroke-linejoin="round">{esc(text)}</text>')
        add(f'<text {base} fill="{colour}">{esc(text)}</text>')

    # cameras: a pointer showing the direction, a dot, a name (what each one sees is the teal)
    for cam in site["cameras"]:
        cx, cy = cam["position"]
        h = cam["heading_deg"]
        a = math.radians(h)
        tip = (cx + 1.25 * k * math.cos(a), cy + 1.25 * k * math.sin(a))
        left = (cx + 0.55 * k * math.cos(a + 2.2), cy + 0.55 * k * math.sin(a + 2.2))
        right = (cx + 0.55 * k * math.cos(a - 2.2), cy + 0.55 * k * math.sin(a - 2.2))
        add(f'<polygon points="{pts([tip, left, right])}" fill="{INK}" stroke="{PAPER}" stroke-width="{0.08 * k:.2f}"/>')
        if cam["proposed"]:
            add(f'<circle cx="{cx:.2f}" cy="{Y(cy):.2f}" r="{0.55 * k:.2f}" fill="white" stroke="{INK}" stroke-width="{0.2 * k:.2f}"/>')
        else:
            add(f'<circle cx="{cx:.2f}" cy="{Y(cy):.2f}" r="{0.5 * k:.2f}" fill="{INK}" stroke="{PAPER}" stroke-width="{0.12 * k:.2f}"/>')
        label = f'{cam["name"]} new' if cam["proposed"] else cam["name"]
        halo_text(cx, Y(cy) + 1.75 * k, label, 1.05 * k, INK, bold=True)

    # doors / entries, and (indoors) the protected spots
    midx, midy = (minx + maxx) / 2, (miny + maxy) / 2

    def marker(point, text, colour, diamond):
        px, py = point
        r = 0.6 * k
        if diamond:
            d = f"M{px:.2f},{Y(py) - r:.2f} L{px + r:.2f},{Y(py):.2f} L{px:.2f},{Y(py) + r:.2f} L{px - r:.2f},{Y(py):.2f} Z"
            add(f'<path d="{d}" fill="white" stroke="{colour}" stroke-width="{0.2 * k:.2f}"/>')
        else:
            add(f'<circle cx="{px:.2f}" cy="{Y(py):.2f}" r="{r:.2f}" fill="white" stroke="{colour}" stroke-width="{0.2 * k:.2f}"/>')
            add(f'<circle cx="{px:.2f}" cy="{Y(py):.2f}" r="{0.22 * k:.2f}" fill="{colour}"/>')
        dy = 1.7 * k if py < midy else -1.3 * k                                # below if near the south edge, else above
        halo_text(px, Y(py) + dy, text, 1.0 * k, INK2)

    if indoor:
        for e in site["entries"]:
            marker(e["point"], nice_name(e["id"]), INK, True)
        for e in site["entrances"]:
            marker(e["point"], nice_name(e["id"]), RED, False)
    else:
        for e in site["entrances"]:
            marker(e["point"], nice_name(e["id"]), INK, True)

    # scale bar
    bar = _nice_bar(span)
    bx, by = minx, (maxy - miny) + 1.5 * k                                   # below the plan (Y(miny) = maxy - miny)
    add(f'<path d="M{bx:.2f},{by:.2f}H{bx + bar:.2f}M{bx:.2f},{by - 0.25 * k:.2f}V{by + 0.25 * k:.2f}M{bx + bar:.2f},{by - 0.25 * k:.2f}V{by + 0.25 * k:.2f}" '
        f'stroke="{INK}" stroke-width="{0.12 * k:.2f}" fill="none"/>')
    add(f'<text x="{bx + bar + 0.5 * k:.2f}" y="{by + 0.35 * k:.2f}" font-family="sans-serif" font-size="{1.05 * k:.2f}" fill="{INK}">{bar} m</text>')
    # north arrow
    nx, ny = maxx + pad * 0.5, -pad * 0.35
    add(f'<path d="M{nx:.2f},{ny + 1.6 * k:.2f} V{ny:.2f} M{nx - 0.45 * k:.2f},{ny + 0.7 * k:.2f} L{nx:.2f},{ny:.2f} L{nx + 0.45 * k:.2f},{ny + 0.7 * k:.2f}" '
        f'stroke="{INK}" stroke-width="{0.12 * k:.2f}" fill="none"/>')
    add(f'<text x="{nx:.2f}" y="{ny + 2.7 * k:.2f}" font-family="sans-serif" font-size="{1.0 * k:.2f}" text-anchor="middle" fill="{INK}">N</text>')
    add("</svg>")

    svg = "".join(out).encode()
    doc = pymupdf.open(stream=svg, filetype="svg")
    pix = doc[0].get_pixmap(dpi=144)
    return pix.tobytes("png")


# ----------------------------------------------------------------------
# the text of the report
# ----------------------------------------------------------------------

CSS = """
body { font-family: sans-serif; font-size: 9.5pt; color: #14232b; line-height: 1.35; }
h1 { font-size: 22pt; margin: 0 0 2pt 0; }
h2.pb { page-break-before: always; background-color: #ffffff; }
h2 { font-size: 12.5pt; margin: 14pt 0 4pt 0; color: #14232b; }
p { margin: 0 0 5pt 0; }
.sub { font-size: 10.5pt; color: #44585f; margin-bottom: 8pt; }
.muted { color: #6c7f86; font-size: 8.5pt; }
.box { background-color: #eaf1eb; padding: 8pt; margin: 4pt 0 6pt 0; }
table { border-collapse: collapse; width: 100%; }
th { text-align: left; font-size: 8.5pt; color: #44585f; border-bottom: 1px solid #14232b; padding: 3pt 4pt 3pt 0; }
td { padding: 3pt 4pt 3pt 0; border-bottom: 1px solid #c9d3cc; vertical-align: top; }
.num { text-align: right; }
.big { font-size: 22pt; font-weight: bold; }
.good { color: #056b66; font-weight: bold; }
.bad { color: #a92a1d; font-weight: bold; }
.kpi td { border-bottom: none; padding: 4pt 8pt 4pt 0; vertical-align: bottom; }
"""


def _rate_cell(x) -> str:
    n = pct(x)
    return f'<td class="num"><span class="{"good" if n <= 5 else "bad" if n >= 30 else ""}">{n}</span></td>'


def _kpis(plan: dict) -> str:
    """Three headline numbers, in the order a person would act: today, after turning, after adding."""
    best, more = plan["best_existing"], plan["more_cameras"]
    if plan.get("mode") == "indoor":
        goal = pct(plan.get("goal", 0.95))
        cols = [("Floor watched today", f'{pct(plan["now"]["watched_frac"])}%'),
                ("After turning your cameras", f'{pct(best["watched_after"])}%'),
                (f'After adding {len(more["additions"])} camera{"s" if len(more["additions"]) != 1 else ""}'
                 if more["additions"] else "After adding cameras", f'{pct(more["watched_after"])}%')]
        note = (f'Share of the open floor that at least one camera is likely to see (goal: {goal}%). '
                f'Unwatched floor today: {round(plan["now"]["blind_m2"])} m2, after the additions: {round(plan["final"]["blind_m2"])} m2.')
    else:
        worst_now = max(r["rate"] for r in plan["now"])
        worst_turn = max(r["after"] for r in best["doors"])
        worst_add = max(r["after"] for r in more["doors"])
        cols = [("Worst door today", f"{pct(worst_now)} of 100"),
                ("After turning your cameras", f"{pct(worst_turn)} of 100"),
                (f'After adding {len(more["additions"])} camera{"s" if len(more["additions"]) != 1 else ""}'
                 if more["additions"] else "After adding cameras", f"{pct(worst_add)} of 100")]
        note = (f'Out of 100 simulated intruders, how many get in unseen through the most exposed door '
                f'(target: {pct(plan["target"])} or fewer).')
    cells = "".join(f'<td><div class="muted">{esc(label)}</div><div class="big">{esc(value)}</div></td>' for label, value in cols)
    return f'<table class="kpi"><tr>{cells}</tr></table><p class="muted">{note}</p>'


def _door_table(plan: dict) -> str:
    best, more = plan["best_existing"], plan["more_cameras"]
    if plan.get("mode") == "indoor":
        spots = {a["id"] for a in plan["now"]["assets"]}
        now_seen = {a["id"]: a for a in plan["now"]["assets"] + plan["now"]["entries"]}
        end_seen = {a["id"]: a for a in plan["final"]["assets"] + plan["final"]["entries"]}
        watched = lambda a: "Watched" if a["watched"] else "<span class=bad>Not watched</span>"
        rows = "".join(
            f'<tr><td>{esc(nice_name(i))}</td><td>{"Protected spot" if i in spots else "Entrance"}</td>'
            f'<td class="num">{pct(now_seen[i]["chance_seen"])}%</td><td>{watched(now_seen[i])}</td>'
            f'<td class="num">{pct(end_seen[i]["chance_seen"])}%</td><td>{watched(end_seen[i])}</td></tr>'
            for i in [a["id"] for a in plan["now"]["assets"]] + [a["id"] for a in plan["now"]["entries"]])
        head = ('<tr><th width="24%">Place</th><th width="20%">Kind</th><th class="num" width="14%">Seen today</th>'
                '<th width="14%">Today</th><th class="num" width="14%">Seen after</th><th width="14%">After</th></tr>')
        return (f'<table>{head}{rows}</table><p class="muted">"Seen" is the chance a camera notices someone standing there. '
                'Entrances are where an intruder is assumed to come in; they are listed for information. '
                'The goal is the open floor and the protected spots.</p>')
    turn = {r["door"]: r["after"] for r in best["doors"]}
    add = {r["door"]: r["after"] for r in more["doors"]}
    rows = "".join(
        f'<tr><td>{esc(nice_name(r["door"]))}</td>{_rate_cell(r["rate"])}{_rate_cell(turn.get(r["door"], r["rate"]))}'
        f'{_rate_cell(add.get(r["door"], turn.get(r["door"], r["rate"])))}</tr>' for r in plan["now"])
    head = ('<tr><th>Door</th><th class="num">Today</th><th class="num">After turning cameras</th>'
            '<th class="num">After adding cameras</th></tr>')
    return f'<table>{head}{rows}</table><p class="muted">Intruders out of 100 who get in without being seen.</p>'


def _changes(plan: dict) -> str:
    best, more = plan["best_existing"], plan["more_cameras"]
    parts = ["<h2>What to do</h2>"]
    if best["changes"]:
        items = "".join(f'<li>Turn <b>{esc(c["camera"])}</b> by {esc(c["degrees"])} degrees {esc(c["direction"])} '
                        f'(seen from above). It costs nothing.</li>' for c in best["changes"])
        parts.append(f'<p><b>1. Turn the cameras you already have.</b></p><ul>{items}</ul>')
    else:
        parts.append(f'<p><b>1. Turn the cameras you already have.</b> {esc(best["sentence"])}</p>')
    if more["additions"]:
        rows = "".join(
            f'<tr><td><b>{esc(a["name"])}</b></td><td>{esc(a["where"])}</td><td>facing {esc(a["compass"])}</td>'
            f'<td>{a["position"][0]:.1f} m east, {a["position"][1]:.1f} m north</td></tr>' for a in more["additions"])
        n = len(more["additions"])
        parts.append(f'<p><b>2. Add {n} camera{"s" if n != 1 else ""}.</b></p>'
                     f'<table><tr><th>Camera</th><th>Where</th><th>Direction</th><th>Position (from the plan\'s lower-left corner)</th></tr>{rows}</table>')
        if not more.get("reached_target", True):
            parts.append('<p class="muted">Even with these cameras the goal is not fully reached. The "after" map shows what is still uncovered.</p>')
    else:
        parts.append('<p><b>2. Add cameras.</b> Not needed: the cameras you have, turned as above, already meet the goal.</p>')
    return "".join(parts)


def _maps_html(today: dict) -> str:
    """Two maps. Wide plans (a long floor) are stacked so each one is big enough to read; squarer ones sit side by side."""
    xs = [p[0] for p in today["site"]["boundary"]]
    ys = [p[1] for p in today["site"]["boundary"]]
    wide = (max(xs) - min(xs)) / max(0.1, (max(ys) - min(ys))) >= 1.5
    if wide:
        return ('<p><b>Today</b></p><img src="map_today.png" width="500"/>'
                '<p><b>After the suggested changes</b></p><img src="map_after.png" width="500"/>')
    return ('<table><tr><td width="50%"><p><b>Today</b></p><img src="map_today.png" width="245"/></td>'
            '<td width="50%"><p><b>After the suggested changes</b></p><img src="map_after.png" width="245"/></td></tr></table>')


def _outage(outage: dict) -> str:
    rows = "".join(f'<tr><td><b>{esc(r["camera"])}</b></td><td>{esc(_words(r["sentence"]))}</td></tr>' for r in outage["cameras"])
    spof = outage.get("single_points_of_failure") or []
    note = ""
    if spof:
        names = sorted({s["camera"] for s in spof})
        note = f'<p><span class="bad">Single points of failure:</span> losing {esc(", ".join(names))} alone leaves a door or area clearly more exposed.</p>'
    return (f'<h2>If a camera fails</h2><p class="muted">Each camera switched off on its own, the others working. '
            f'These are the cameras you have today.</p>{note}<table>{rows}</table>')


def _summary_box(explained: dict) -> str:
    if explained["source"] == "bedrock":
        label = f'Written by Amazon Bedrock ({esc(explained.get("model") or "")}). Every number was checked against the simulation.'
    else:
        label = "Built-in summary. Every number comes straight from the simulation."
    return f'<h2>In plain words</h2><div class="box"><p>{esc(explained["text"])}</p><p class="muted">{label}</p></div>'


def _method(plan: dict, archetype: str, calibrated: list | None = None) -> str:
    n = plan.get("starts_tested", 200)
    cal = (f" {', '.join(calibrated)} {'was' if len(calibrated) == 1 else 'were'} calibrated from {'its' if len(calibrated) == 1 else 'their'} own "
           "picture, so the ground they see is measured from that picture rather than assumed (how well they see it is still the model)."
           if calibrated else "")
    return (
        "<h2>How these numbers were worked out</h2>"
        f'<p class="muted">{esc(n)} simulated intruders ("{esc(archetype)}" type) start at random points and take the route to the target '
        "that is least likely to be seen. A camera notices someone with a chance that falls off with distance, only inside its view and "
        "only where nothing blocks the line of sight. Several cameras combine. The search for the best setup used one set of intruders; "
        "every figure in this report was measured on a second set it never saw. The same question always gives the same answer.</p>"
        '<p class="muted">Limits: the plan is flat (camera height and tilt are not modelled), the chance of being seen is a simple model, '
        "not measured on real video, and the intruders are simulated. Cameras are simulated from the positions and directions given; "
        f"only cameras linked to a Ring device use real Ring data for online or offline status.{esc(cal)}</p>")


# ----------------------------------------------------------------------
# putting it together
# ----------------------------------------------------------------------

def build_report(*, site_name: str, archetype: str, plan: dict, explained: dict,
                 today: dict, after: dict, outage: dict, when: datetime | None = None) -> bytes:
    when = when or datetime.now(timezone.utc)
    mode = "Indoor floor" if plan.get("mode") == "indoor" else "Property"
    archive = pymupdf.Archive()
    archive.add(map_png(today), "map_today.png")
    archive.add(map_png(after), "map_after.png")

    body = (
        f'<h1>RedHouse security report</h1>'
        f'<p class="sub">{esc(site_name)} - {mode} - {when.strftime("%d %B %Y")}</p>'
        f'{_summary_box(explained)}'
        f'<h2>The headline</h2>{_kpis(plan)}'
        f'<h2>Place by place</h2>{_door_table(plan)}'
        f'{_changes(plan)}'
        '<h2 class="pb">Before and after</h2>'
        f'{_maps_html(today)}'
        '<p class="muted">Teal: where cameras are likely to see someone (darker = more likely). '
        f'{"Red tint: floor nobody watches. " if plan.get("mode") == "indoor" else ""}'
        'The dot with an arrow is a camera and the arrow shows where it points. A camera marked "new" is a suggestion.</p>'
        f'{_outage(outage)}'
        f'{_method(plan, archetype, [c["name"] for c in today["site"]["cameras"] if c.get("calibrated")])}'
    )

    story = pymupdf.Story(html=f"<html><body>{body}</body></html>", user_css=CSS, archive=archive)
    stream = io.BytesIO()
    writer = pymupdf.DocumentWriter(stream)
    media = pymupdf.paper_rect("a4")
    where = media + (42, 40, -42, -52)
    more = 1
    while more:
        dev = writer.begin_page(media)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()

    doc = pymupdf.open(stream=stream.getvalue(), filetype="pdf")
    total = doc.page_count
    for i, page in enumerate(doc, start=1):
        page.insert_text((42, media.height - 30), "RedHouse - simulation only: simulated cameras and intruders",
                         fontsize=7.5, color=(0.42, 0.5, 0.52))
        page.insert_text((media.width - 42 - 40, media.height - 30), f"Page {i} of {total}",
                         fontsize=7.5, color=(0.42, 0.5, 0.52))
    doc.set_metadata({"title": f"RedHouse security report - {site_name}", "author": "RedHouse", "producer": "RedHouse"})
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out
