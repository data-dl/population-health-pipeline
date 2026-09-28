"""docs/index.html - a one-page report of the demo scenario, built from the workspace it ran in.

Static HTML with inline SVG, light surface, no external requests. Every chart has a table view and a
keyboard-reachable tooltip; values are also printed at the bar tips.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from .settings import Settings
from .warehouse import Warehouse

# The page is published from docs/ on GitHub Pages, which serves Markdown as raw text: link to the
# rendered files on GitHub instead.
BLOB = "https://github.com/data-dl/population-health-pipeline/blob/main/"

CSS = """
:root { color-scheme: light;
  --surface: #fcfcfb; --page: #f9f9f7; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --series-1: #2a78d6; --border: rgba(11,11,11,0.10);
  --good: #0ca30c; --warning: #fab219; --serious: #ec835a; --critical: #d03b3b; --good-text: #006300; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1040px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 28px; margin: 0 0 6px; } h2 { font-size: 20px; margin: 40px 0 8px; }
h3 { font-size: 15px; margin: 0 0 4px; }
p { margin: 0 0 12px; color: var(--ink-2); max-width: 78ch; } a { color: #1c5cab; }
.lede { font-size: 16px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin: 20px 0 8px; }
.tile { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
.tile .label { color: var(--ink-2); font-size: 13px; } .tile .value { font-size: 26px; font-weight: 600; }
.tile .note { color: var(--muted); font-size: 12px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px;
  margin: 12px 0; overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13.5px; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--grid); vertical-align: top; }
th { color: var(--ink-2); font-weight: 600; } td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.status { white-space: nowrap; } .status i { font-style: normal; font-weight: 700; margin-right: 4px; }
.s-good i { color: var(--good); } .s-warn i { color: #b07a00; } .s-bad i { color: var(--critical); }
.s-muted { color: var(--muted); }
details { margin-top: 8px; } summary { cursor: pointer; color: var(--ink-2); font-size: 13px; }
svg text { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
.mark:focus { outline: none; } .mark:focus .bar, .mark:hover .bar { opacity: 0.8; }
#tip { position: fixed; pointer-events: none; background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 6px 10px; font-size: 13px; box-shadow: 0 4px 16px rgba(0,0,0,.08); display: none; }
#tip strong { display: block; font-size: 15px; }
.cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; }
pre { background: var(--page); border: 1px solid var(--grid); border-radius: 8px; padding: 10px 12px; font-size: 12px;
  overflow-x: auto; margin: 6px 0 0; }
.foot { margin-top: 48px; color: var(--muted); font-size: 13px; }
.runs td:nth-child(1) { white-space: nowrap; } .runs td:nth-child(2) { min-width: 300px; }
"""

SCRIPT = """
const tip = document.getElementById('tip');
function show(el, x, y) {
  tip.replaceChildren();
  const v = document.createElement('strong'); v.textContent = el.dataset.value;
  const l = document.createElement('span'); l.textContent = el.dataset.label;
  tip.append(v, l); tip.style.display = 'block';
  tip.style.left = Math.min(x + 14, window.innerWidth - tip.offsetWidth - 8) + 'px';
  tip.style.top = (y + 14) + 'px';
}
document.querySelectorAll('.mark').forEach(el => {
  el.addEventListener('pointermove', e => show(el, e.clientX, e.clientY));
  el.addEventListener('pointerleave', () => { tip.style.display = 'none'; });
  el.addEventListener('focus', () => { const r = el.getBoundingClientRect(); show(el, r.left, r.bottom); });
  el.addEventListener('blur', () => { tip.style.display = 'none'; });
});
"""


def e(value) -> str:
    return html.escape("" if value is None else str(value))


def _bar_path(x: float, y: float, w: float, h: float) -> str:
    """Square at the baseline, 4px rounded at the data end."""
    r = min(4.0, w / 2, h / 2)
    if w <= 0:
        return ""
    return (
        f"M{x:.1f},{y:.1f} h{w - r:.1f} a{r},{r} 0 0 1 {r},{r} v{h - 2 * r:.1f} "
        f"a{r},{r} 0 0 1 {-r},{r} h{-(w - r):.1f} z"
    )


def hbar(rows: list[dict], *, max_value: float, fmt, ticks: list[float], label_width: int = 250, title: str) -> str:
    """Horizontal bars, one series. rows: {label, value (None = suppressed), note}."""
    width, bar, pitch, top = 720, 14, 26, 8
    plot = width - label_width - 70
    height = top + pitch * len(rows) + 26
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{e(title)}">']
    for t in ticks:
        x = label_width + plot * t / max_value
        parts.append(
            f'<line x1="{x:.1f}" y1="{top - 4}" x2="{x:.1f}" y2="{height - 22}" stroke="var(--grid)" '
            f'stroke-width="1"/><text x="{x:.1f}" y="{height - 6}" font-size="11" fill="var(--muted)" '
            f'text-anchor="middle">{e(fmt(t))}</text>'
        )
    parts.append(
        f'<line x1="{label_width}" y1="{top - 4}" x2="{label_width}" y2="{height - 22}" '
        f'stroke="var(--axis)" stroke-width="1"/>'
    )
    for i, r in enumerate(rows):
        y = top + i * pitch
        cy = y + bar / 2 + 4
        parts.append(
            f'<g class="mark" tabindex="0" data-label="{e(r["label"])}" '
            f'data-value="{e(r.get("tip") or (fmt(r["value"]) if r["value"] is not None else "suppressed"))}">'
        )
        parts.append(f'<rect x="0" y="{y}" width="{width}" height="{pitch}" fill="transparent"/>')
        parts.append(
            f'<text x="{label_width - 10}" y="{cy}" font-size="12.5" fill="var(--ink-2)" '
            f'text-anchor="end">{e(r["label"])}</text>'
        )
        if r["value"] is None:
            parts.append(
                f'<text x="{label_width + 8}" y="{cy}" font-size="12" fill="var(--muted)">suppressed '
                f"(small cell)</text>"
            )
        else:
            w = plot * r["value"] / max_value
            parts.append(f'<path class="bar" d="{_bar_path(label_width, y + 4, w, bar)}" fill="var(--series-1)"/>')
            parts.append(
                f'<text x="{label_width + w + 6:.1f}" y="{cy}" font-size="12" fill="var(--ink)">'
                f"{e(fmt(r['value']))}{e(r.get('note', ''))}</text>"
            )
        parts.append("</g>")
    parts.append("</svg>")
    return "".join(parts)


def table(headers: list[str], rows: list[list], numeric: set[int] | None = None) -> str:
    numeric = numeric or set()
    head = "".join(
        f'<th class="num">{e(h)}</th>' if i in numeric else f"<th>{e(h)}</th>" for i, h in enumerate(headers)
    )
    body = "".join(
        "<tr>"
        + "".join(f'<td class="num">{c}</td>' if i in numeric else f"<td>{c}</td>" for i, c in enumerate(r))
        + "</tr>"
        for r in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def status(text: str, kind: str) -> str:
    icon = {"good": "&#10003;", "warn": "&#9888;", "bad": "&#10005;", "muted": "&#8211;"}[kind]
    return f'<span class="status s-{kind}"><i aria-hidden="true">{icon}</i>{e(text)}</span>'


def _pct(x) -> str:
    return "" if x is None else f"{float(x) * 100:.1f}%"


def gather(settings: Settings, results: list, key: dict) -> dict:
    feeds = settings.feeds_order
    last_measured = [r for r, _ in results if r.dag("measures_and_documents") is not None][-1]
    with Warehouse(settings.warehouse_path) as wh:
        runs = []
        for result, story in results:
            rid = result.run_id
            loads = wh.rows("select * from audit.loads where run_id = ? order by feed, name", [rid])
            gates = {
                r["feed"]: r["decision"] for r in wh.rows("select * from audit.gate_decisions where run_id = ?", [rid])
            }
            held = wh.scalar(
                "select count(*) from audit.row_findings where run_id = ? and candidate = 'new' "
                "and action = 'quarantine'",
                [rid],
            )
            released = sum(
                wh.scalar(f"select count(*) from quarantine.{f} where _q_released_run = ?", [rid]) or 0
                for f in feeds
                if wh.table_exists("quarantine", f)
            )
            fresh = wh.rows("select * from audit.freshness where run_id = ? order by feed, source", [rid])
            runs.append(
                {
                    "run_id": rid,
                    "as_of": result.as_of,
                    "story": story,
                    "loads": loads,
                    "gates": gates,
                    "held": held,
                    "released": released,
                    "fresh": fresh,
                    "measured": result.dag("measures_and_documents") is not None,
                }
            )
        held_rows = {}
        for f in (f for f in feeds if wh.table_exists("quarantine", f)):
            for r in wh.rows(f"select _file, _row_num, _q_rule_ids from quarantine.{f}"):
                held_rows[(f, r["_file"], r["_row_num"])] = set(r["_q_rule_ids"].split(","))
        deduped = {r["name"]: r["rows_deduped"] for r in wh.rows("select name, rows_deduped from audit.loads")}
        touched = {
            (r["feed"], r["name"], r["row_num"], r["rule_id"])
            for r in wh.rows(
                "select f.feed, l.name, f.row_num, f.rule_id from audit.row_findings f "
                "join audit.loads l using (load_id) where f.candidate = 'new' and f.action in ('repair', 'warn')"
            )
        }
        measures = wh.rows(
            "select s.*, d.family from marts.measure_summary s join marts.measure_definitions d "
            "using (measure_id) where site_id = 'ALL' order by d.family, s.measure_id"
        )
        summary = {
            (r["measure_id"], r["site_id"]): [r["numerator"], r["denominator"]]
            for r in wh.rows("select * from marts.measure_summary")
        }
        history = wh.rows(
            "select run_id, measure_id, numerator, denominator, rate from audit.measure_history "
            "where site_id = 'ALL' order by run_id"
        )
        equity = wh.rows(
            "select * from marts.measure_equity where measure_id = 'DEP_SCREEN' "
            "and stratifier in ('language_group', 'payer_type') order by stratifier, group_value"
        )
        counts = {
            f: wh.scalar(f"select count(*) from published.{t}")
            for f, t in (
                ("patients", "patients"),
                ("screenings", "screening_events"),
                ("labs", "lab_results"),
                ("appointments", "appointments"),
                ("fills", "pharmacy_fills"),
            )
        }
        dbt = wh.rows("select status from audit.dbt_results where run_id = ?", [last_measured.run_id])
    planted_duplicates = {f["name"]: f.get("counts", {}).get("deduped", 0) for f in key["files"]}
    caught: dict[str, list[int]] = {}
    for p in key["planted"]:
        slot = caught.setdefault(p["rule"], [0, 0])
        slot[0] += 1
        if p["expect"] == "quarantined":
            hit = p["rule"] in held_rows.get((p["feed"], p["file"], p["row"]), set())
        elif p["expect"] in ("repaired", "warned"):
            hit = (p["feed"], p["file"], p["row"], p["rule"]) in touched
        else:  # an exact duplicate: its file's de-duplicated count must be exactly the planted ones
            hit = deduped.get(p["file"]) == planted_duplicates[p["file"]]
        slot[1] += int(hit)
    matching = sum(
        1 for m, by_site in key["measures"].items() for site, v in by_site.items() if summary.get((m, site)) == v
    )
    return {
        "runs": runs,
        "caught": caught,
        "measures": measures,
        "history": history,
        "matching": matching,
        "equity": equity,
        "counts": counts,
        "dbt_passed": sum(r["status"] in ("success", "pass") for r in dbt),
        "dbt_nodes": len(dbt),
        "last_measured": last_measured,
    }


def build_page(docs: Path, settings: Settings, results: list, key: dict) -> Path:
    data = gather(settings, results, key)
    deid = json.loads((data["last_measured"].evidence_dir / "deid_verification.json").read_text(encoding="utf-8"))
    sample = json.loads((docs / "sample_run" / "document_before_after.json").read_text(encoding="utf-8"))
    planted = len(key["planted"])
    caught_total = sum(v[1] for v in data["caught"].values())
    measure_cells = sum(len(v) for v in key["measures"].values())
    rows_in = sum(f["rows"] for f in key["files"])
    sources = sum(len(c.sources) for c in settings.contracts.values())

    out = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<title>Population-health pipeline - sample run</title>",
        f"<style>{CSS}</style></head><body><main>",
        "<h1>Population-health pipeline</h1>",
        "<p class='lede'>Five supplier feeds, a validated warehouse, nine quality measures, longitudinal "
        "patient documents and a de-identified copy - run on a synthetic network of 5,000 patients over five "
        "scheduled days, and graded against an answer key. This page is built from that run. "
        f"<a href='{BLOB.split('/blob/')[0]}'>Repository</a> &middot; "
        f"<a href='{BLOB}docs/sample_run/README.md'>run evidence</a> &middot; "
        f"<a href='{BLOB}docs/design_decisions.md'>design decisions</a></p>",
    ]

    tiles = [
        ("Rows delivered", f"{rows_in:,}", f"{len(key['files'])} files from {sources} supplier sources"),
        ("Planted defects caught", f"{caught_total} / {planted}", "by the rule each was planted for"),
        ("Measure cells matching", f"{data['matching']} / {measure_cells}", "dbt vs an independent implementation"),
        ("dbt nodes passing", f"{data['dbt_passed']} / {data['dbt_nodes']}", "models, seed and tests, last build"),
        (
            "Leak-gate checks",
            f"{sum(c['passed'] for c in deid['checks'])} / {len(deid['checks'])}",
            "before the demo copy is released",
        ),
    ]
    out.append(
        "<div class='tiles'>"
        + "".join(
            f"<div class='tile'><div class='label'>{e(a)}</div><div class='value'>{e(b)}</div>"
            f"<div class='note'>{e(c)}</div></div>"
            for a, b, c in tiles
        )
        + "</div>"
    )

    out.append("<h2>Five scheduled days</h2><p>Each run sees only the files that had arrived by its date.</p>")
    rows = []
    for r in data["runs"]:
        loaded = sum(
            1 for x in r["loads"] if x["status"] in ("published", "merged", "superseded") and not x["blocked_rule"]
        )
        stopped = [x for x in r["loads"] if x["blocked_rule"]]
        gates = ", ".join(f for f, d in sorted(r["gates"].items()) if d == "HOLD")
        fresh = [f for f in r["fresh"] if f["overall"] != "OK"]
        notes: list[str] = []
        for f in fresh:
            for flag in (
                f["delivery_status"] if f["delivery_status"] != "ON_TIME" else None,
                "stuck" if f["pipeline_status"] == "STUCK" else None,
            ):
                if flag and f"{f['feed']} {flag.lower()}" not in notes:
                    notes.append(f"{f['feed']} {flag.lower()}")
        fresh_text = "; ".join(notes)
        rows.append(
            [
                f"<a href='{BLOB}docs/sample_run/{e(r['run_id'])}/summary.md'>{e(r['as_of'])}</a>",
                e(r["story"]),
                str(loaded) + (f" + {len(stopped)} stopped" if stopped else ""),
                f"{r['held']:,}" + (f" (released {r['released']})" if r["released"] else ""),
                status("held: " + gates, "bad")
                if gates
                else (status("all published", "good") if r["loads"] else status("nothing new", "muted")),
                status(fresh_text, "warn") if fresh else status("all on time", "good"),
                "yes" if r["measured"] else "no (nothing changed)",
            ]
        )
    out.append(
        "<div class='card runs'>"
        + table(
            ["day", "what happens", "files loaded", "rows held", "gate", "freshness", "measures rebuilt"], rows, {2, 3}
        )
        + "</div>"
    )

    rules = sorted(data["caught"].items(), key=lambda kv: (-kv[1][0], kv[0]))
    titles = {r.id: r.title for c in settings.contracts.values() for r in c.rules}
    titles["merge"] = "Exact duplicates inside a file are merged, counted once"
    names = {rid: ("duplicates" if rid == "merge" else rid) for rid, _ in rules}
    chart_rows = [
        {"label": names[rid], "value": v[1], "tip": f"{titles.get(rid, rid)}: {v[1]} of {v[0]} caught"}
        for rid, v in rules
    ]
    out.append(
        "<h2>What the rules caught</h2><p>Every planted defect, by the rule it was planted for, "
        "held, repaired or flagged by that rule - and, graded separately, nothing else touched.</p>"
    )
    top = max(v[0] for v in data["caught"].values())
    out.append(
        "<div class='card'><h3>Planted defects caught, by rule</h3>"
        + hbar(
            chart_rows,
            max_value=top,
            fmt=lambda v: f"{int(v)}",
            ticks=[0, top // 2, top],
            label_width=120,
            title="Planted defects caught, by rule",
        )
        + "<details><summary>Table</summary>"
        + table(
            ["rule", "what it checks", "planted", "caught"],
            [[e(names[rid]), e(titles.get(rid, "")), str(v[0]), str(v[1])] for rid, v in rules],
            {2, 3},
        )
        + "</details></div>"
    )

    out.append(
        "<h2>Measures, 2025</h2><p>Network rates from the dbt marts. Each is also computed by an "
        "independent Python implementation from the generator's own data; the two agree at every clinic, "
        "and adherence agrees patient by patient.</p>"
    )
    labels = {m["measure_id"]: f"{m['measure_name']}" for m in data["measures"]}
    chart_rows = [
        {
            "label": labels[m["measure_id"]],
            "value": float(m["rate"]),
            "note": " (lower is better)" if m["direction"] == "lower_better" else "",
            "tip": f"{_pct(m['rate'])} - {m['numerator']:,} of {m['denominator']:,}",
        }
        for m in data["measures"]
    ]
    out.append(
        "<div class='card'><h3>Network rate by measure</h3>"
        + hbar(
            chart_rows,
            max_value=1.0,
            fmt=_pct,
            ticks=[0, 0.25, 0.5, 0.75, 1.0],
            label_width=300,
            title="Network rate by measure",
        )
        + "<details><summary>Table</summary>"
        + table(
            ["measure", "numerator", "denominator", "rate"],
            [
                [e(labels[m["measure_id"]]), f"{m['numerator']:,}", f"{m['denominator']:,}", _pct(m["rate"])]
                for m in data["measures"]
            ],
            {1, 2, 3},
        )
        + "</details></div>"
    )

    first, second = data["runs"][0]["run_id"], data["runs"][1]["run_id"]
    hist = {(h["run_id"], h["measure_id"]): h for h in data["history"]}
    restated = [
        (m, hist[(first, m)], hist[(second, m)])
        for (run, m) in hist
        if run == second
        and (hist[(first, m)]["numerator"], hist[(first, m)]["denominator"])
        != (hist[(second, m)]["numerator"], hist[(second, m)]["denominator"])
    ]
    out.append(
        "<div class='cols'><div class='card'><h3>Restated by late data (day 2)</h3>"
        "<p>Late lab results and late-adjudicated claims for 2025 arrived after the year was reported.</p>"
        + table(
            ["measure", "day 1", "day 2"],
            [
                [
                    e(labels.get(m, m)),
                    f"{a['numerator']} / {a['denominator']} ({_pct(a['rate'])})",
                    f"{b['numerator']} / {b['denominator']} ({_pct(b['rate'])})",
                ]
                for m, a, b in restated
            ],
            {1, 2},
        )
        + "</div>"
    )
    eq = data["equity"]
    out.append(
        "<div class='card'><h3>Depression screening, by language and payer</h3>"
        "<p>Relative rate compares a group with everyone else in its stratifier. Small cells are hidden, "
        "with a complementary cell where needed.</p>"
        + table(
            ["group", "eligible", "rate", "relative rate"],
            [
                [
                    e(f"{r['stratifier'].replace('_', ' ')}: {r['group_value']}"),
                    f"{r['denominator']:,}" if not r["suppressed"] else "&ndash;",
                    _pct(r["rate"])
                    if not r["suppressed"]
                    else status(f"suppressed ({r['suppression_reason']})", "muted"),
                    e(r["relative_rate"]) if r["relative_rate"] is not None else "",
                ]
                for r in eq
            ],
            {1, 2, 3},
        )
        + "</div></div>"
    )

    out.append(
        "<h2>Freshness</h2><p>Per supplier source: did the delivery arrive on time, and did what arrived "
        "reach published? Day 3 and day 4.</p>"
    )
    for r in data["runs"][2:4]:
        rows = []
        for f in r["fresh"]:
            kind_d = {"ON_TIME": "good", "DUE": "warn", "LATE": "bad"}[f["delivery_status"]]
            kind_p = "good" if f["pipeline_status"] == "OK" else "bad"
            rows.append(
                [
                    e(f["feed"]),
                    e(f["source"]),
                    status(f["delivery_status"].replace("_", " ").lower(), kind_d),
                    status(f["pipeline_status"].lower(), kind_p),
                    e(f["latest_published_event"]),
                    e(f["detail"] or ""),
                ]
            )
        out.append(
            f"<div class='card'><h3>{e(r['as_of'])}</h3>"
            + table(["feed", "source", "delivery", "pipeline", "newest published", "detail"], rows)
            + "</div>"
        )

    out.append(
        "<h2>De-identification</h2><p>The same patient in the restricted documents and in the released "
        "demo copy (both synthetic). Every date moved by one shift; clinical values unchanged.</p>"
    )

    def excerpt(doc: dict) -> str:
        pick = {
            "_id": doc["_id"],
            "demographics": {
                k: doc["demographics"][k] for k in ("firstName", "lastName", "birthDate", "phone", "email")
            },
            "address": doc["demographics"]["address"],
            "careTeam": doc["careTeam"],
            "latestPHQ9": (doc["screenings"].get("PHQ9") or {}).get("latest"),
        }
        return e(json.dumps(pick, indent=1))

    out.append(
        "<div class='cols'><div class='card'><h3>Restricted</h3><pre>"
        + excerpt(sample["restricted"])
        + "</pre></div><div class='card'><h3>Released demo copy</h3><pre>"
        + excerpt(sample["demo"])
        + "</pre></div></div>"
    )
    out.append(
        "<div class='card'><h3>Leak gate</h3>"
        + table(
            ["check", "result", "detail"],
            [
                [e(c["check"]), status("pass", "good") if c["passed"] else status("fail", "bad"), e(c["detail"])]
                for c in deid["checks"]
            ],
        )
        + "</div>"
    )

    out.append(
        "<p class='foot'>Synthetic data only. Built by <code>python -m pophealth demo --docs docs</code>. "
        "LOINC content (codes only) is copyright Regenstrief Institute, Inc., used under the LOINC licence."
        "</p></main><div id='tip' role='tooltip'></div>"
    )
    out.append(f"<script>{SCRIPT}</script></body></html>")
    page = docs / "index.html"
    page.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    return page
