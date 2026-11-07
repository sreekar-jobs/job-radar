"""job-radar: daily fresher job finder for the @sreekar_polepalli job page (built 2026-11-07).

  job-radar open            start the page (http://localhost:8765) and open it in the browser
  job-radar fetch           search all sources now (the daily scheduled task "Job Radar" runs this)
  job-radar serve           page only (no browser)

Priority (what goes on top):
  TIER 1  GUARANTEED TEST   everyone who registers gets the test/exam (hiring challenges, assessment as the first
                            round, or the posting says "test link to all / no shortlisting")
  TIER 2  TEST MENTIONED    a test/exam is part of the process but after a screening, or an official
                            hiring page changed (new drive?)
  TIER 3  FRESHER JOB       fresher / entry-level / internship, no test info
Inside a tier: test date soon, deadline soon (not past), popular (registrations), new today.

Sources (public pages only, polite, once a day): Unstop hiring challenges + jobs + internships (with round
details: dates, round type), HackerEarth hiring challenges, and official career pages you list in
reels/job_radar/config.json ("watch"), which raise an alert when the page changes.
Every fact shown comes from the source page. Nothing is guessed: no pay / date / batch shown if not given.
Data: reels/job_radar/ (jobs.json, state.json, config.json, radar.log).
"""
import argparse
import datetime as dt
import hashlib
import html
import json
import re
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "reels" / "job_radar"
JOBS, STATE, CONFIG, LOG = DATA / "jobs.json", DATA / "state.json", DATA / "config.json", DATA / "radar.log"
PAGE = Path(__file__).resolve().parent / "job_radar_page.html"
PORT = 8765
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36", "Accept-Language": "en-IN,en;q=0.9"}
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
LOCK = threading.Lock()
MARK_LOCK = threading.Lock()

DEFAULT_CONFIG = {
    "unstop_pages": {"competitions": 8, "jobs": 10, "internships": 8},
    "max_detail_calls": 220,
    "watch": [
        {"name": "Wipro careers", "url": "https://careers.wipro.com/"},
        {"name": "Capgemini India careers", "url": "https://www.capgemini.com/in-en/careers/"},
        {"name": "Tech Mahindra careers", "url": "https://careers.techmahindra.com/"},
    ],
    "note": "Add official career pages to 'watch' (name + url). Remove any that keep failing.",
}

TEST_WORDS = r"(online test|online assessment|assessment|aptitude|coding (round|test|challenge)|mcq|quiz|exam\b|" \
             r"examination|hackathon|hiring challenge|nqt|written test|test link|hackerrank|hackerearth|amcat|" \
             r"mettl|cocubes|elitmus)"
GUARANTEED = r"(test link (will be )?(sent|shared) to all|all (registered|applied) candidates|everyone who (registers|applies)|" \
             r"no (resume )?shortlist|without (any )?shortlist|direct(ly)? (test|exam|assessment)|" \
             r"all applicants will (receive|get|take)|open to all registrants|registered participants will receive)"
FRESHER = r"(fresher|freshers|entry[- ]level|graduate|trainee|0[-– ]?(1|2) ?(years?|yrs)|0 years|no experience|" \
          r"intern\b|internship|batch|pass[- ]?out|campus|genc|nqt|20(2[4-8]))"
HIRING = r"(hiring|recruit|job offer|job opportunit|full[- ]time (role|offer|opportunit)|\bppo\b|\bppi\b|" \
         r"pre[- ]placement|placement (offer|opportunit)|internship (offer|opportunit)|interview (call|opportunit)|" \
         r"get hired|talent hunt|hiring challenge|campus drive|off[- ]campus)"
TEST_SUBTYPES = {"quiz", "coding", "assessment", "online_assessment", "mcq", "test", "hackathon", "online_test",
                 "coding_challenge", "aptitude"}


# ---------------------------------------------------------------- helpers
def now():
    return dt.datetime.now(IST)


def log(msg):
    DATA.mkdir(parents=True, exist_ok=True)
    line = f"{now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf8") as f:
        f.write(line + "\n")
    st = load(STATE, {})
    st["progress"] = msg
    save(STATE, st)


def load(p, default):
    try:
        return json.loads(p.read_text(encoding="utf8"))
    except Exception:
        return default


def save(p, data):
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf8")
    tmp.replace(p)


def text_of(h):
    h = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h or "", flags=re.S | re.I)
    h = re.sub(r"<br\s*/?>|</p>|</li>|</h\d>", "\n", h, flags=re.I)
    h = html.unescape(re.sub(r"<[^>]+>", " ", h))
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n", h)).strip()


def parse_date(s):
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=IST)
    except Exception:
        return None


def get(url, **kw):
    for attempt in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=30, **kw)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            return r
        except requests.RequestException:
            time.sleep(2)
    return None


def batches(t):
    found = set()
    for m in re.finditer(r"(batch|pass(ed)?[- ]?out|graduat\w*|year of passing|yop|class of)[^.\n]{0,60}", t, re.I):
        found |= set(re.findall(r"\b20(2[3-9])\b", m.group(0)))
    for m in re.finditer(r"\b20(2[3-9])\b[^.\n]{0,25}(batch|pass[- ]?out|graduates)", t, re.I):
        found.add(m.group(1))
    return sorted("20" + y for y in found)


def duration(t):
    m = re.search(r"(\d+(\.\d+)?)\s*(hours?|hrs?)(\s*(and\s*)?(\d+)\s*(minutes?|mins?))?", t, re.I)
    if m:
        return m.group(0).strip()
    m = re.search(r"\b(\d{2,3})\s*(minutes?|mins?)\b", t, re.I)
    return m.group(0) if m else ""


def sections(t):
    words = ["aptitude", "quantitative", "logical reasoning", "reasoning", "verbal", "english", "coding",
             "programming", "technical mcq", "mcq", "communication", "versant", "essay", "pseudo code",
             "data structures", "sql"]  # no "AI": site menus ("AI Interviews") gave false hits
    low = t.lower()
    return [w.title() if w not in ("mcq", "sql") else w.upper() for w in words if re.search(rf"\b{w}\b", low)][:7]


# ---------------------------------------------------------------- Unstop
def unstop_list(kind, pages):
    out = []
    for p in range(1, pages + 1):
        r = get(f"https://unstop.com/api/public/opportunity/search-result?opportunity={kind}&page={p}"
                f"&per_page=30&oppstatus=open")
        if not r or not r.ok:
            break
        rows = r.json().get("data", {}).get("data", [])
        if not rows:
            break
        out += rows
        time.sleep(0.4)
    return out


def unstop_interesting(j, kind):
    title = j.get("title") or ""
    org = (j.get("organisation") or {}).get("name") or ""
    body = text_of(j.get("details"))[:4000]
    filt = " ".join(f.get("name", "") for f in j.get("filters") or [])
    blob = f"{title} {filt} {body}"
    if kind == "competitions":
        # only real hiring challenges: a job / internship offer is the prize, run by a company (not a college club)
        if re.search(r"college|university|institute|\biit|\bnit\b|\biim|school|student|chapter|club|council|"
                     r"society|association|cell\b|fest\b", org, re.I):
            return False
        if re.search(r"case (competition|study)|quiz competition|debate|essay|poster|photography|ideathon", title, re.I):
            return False
        return bool(re.search(HIRING, title + " " + body[:2500], re.I))
    return bool(re.search(FRESHER, blob, re.I)) or "fresher" in filt.lower()


def unstop_job(j, kind, detail):
    c = detail or {}
    body = text_of(c.get("details") or j.get("details"))
    title = (j.get("title") or "").strip()
    org = ((j.get("organisation") or {}).get("name") or "").strip()
    rounds = []
    for rd in sorted(c.get("rounds") or [], key=lambda x: x.get("round_order") or 0):
        d = (rd.get("details") or [{}])[0] or {}
        rounds.append({"type": (rd.get("subtype") or "").lower(), "title": (d.get("title") or "").strip(),
                       "start": d.get("start_date"), "end": d.get("end_date"),
                       "desc": text_of(d.get("description") or "")[:300]})
    def is_test(r):
        return r["type"] in TEST_SUBTYPES or bool(re.search(r"test|assessment|quiz|aptitude|coding|exam|mcq",
                                                            r["title"], re.I))
    test_rounds = [r for r in rounds if is_test(r)]
    gate_before = []
    for r in rounds:
        if is_test(r):
            break
        if r["type"] in ("screening", "interview") or re.search(r"screen|shortlist|interview", r["title"], re.I):
            gate_before.append(r)
    tier, why = 3, "Fresher / entry-level opening"
    if test_rounds and not gate_before:
        tier, why = 1, f"Everyone who registers gets round 1: {test_rounds[0]['title'] or test_rounds[0]['type']}"
    elif re.search(GUARANTEED, body, re.I):
        tier, why = 1, "Posting says the test goes to all registered candidates"
    elif kind == "competitions" and test_rounds:
        tier, why = 1, "Hiring challenge: the test is the first step"
    elif test_rounds:
        tier, why = 2, f"Test round after {gate_before[0]['title'] or 'screening'}"
    elif re.search(TEST_WORDS, body, re.I):
        tier, why = 2, "Posting mentions a test / assessment"
    jd = j.get("jobDetail") or c.get("job_detail") or {}
    pay = ""
    if jd and jd.get("show_salary") and not jd.get("not_disclosed") and (jd.get("min_salary") or jd.get("max_salary")):
        lo, hi = jd.get("min_salary"), jd.get("max_salary")
        rng = f"₹{lo:,}" + (f" – ₹{hi:,}" if hi and hi != lo else "") if lo else f"up to ₹{hi:,}"
        pay = f"{rng} / {jd.get('pay_in') or 'period'}"
    locs = [l.get("city") for l in (j.get("locations") or []) if l.get("city")]
    reg = j.get("regnRequirements") or c.get("regnRequirements") or {}
    deadline = reg.get("end_regn_dt") or j.get("end_date")
    tests = [{"label": r["title"] or r["type"].title(), "start": r["start"], "end": r["end"]} for r in test_rounds]
    url = "https://unstop.com/" + (j.get("public_url") or "").lstrip("/")
    return {
        "id": f"unstop-{j.get('id')}", "source": "Unstop", "kind": {"competitions": "Hiring challenge",
                                                                     "jobs": "Job", "internships": "Internship"}[kind],
        "company": org, "title": title, "tier": tier, "why": why, "tests": tests,
        "duration": next((duration(r["desc"]) for r in test_rounds if duration(r["desc"])), "") or duration(body),
        "pattern": sections(" ".join(r["title"] + " " + r["desc"] for r in test_rounds) + " " + body[:3000]),
        "rounds": [r["title"] or r["type"] for r in rounds if r["type"] != "finalize"],
        "batch": batches(body), "pay": pay, "location": ", ".join(locs[:3]) or (j.get("region") or "").title(),
        "deadline": deadline, "registered": j.get("registerCount") or 0, "apply_url": url, "source_url": url,
        "summary": body[:600], "body": body[:5000],
    }


def fetch_unstop(cfg):
    jobs, calls = [], 0
    for kind, pages in cfg["unstop_pages"].items():
        rows = unstop_list(kind, pages)
        keep = [j for j in rows if j.get("regn_open") and unstop_interesting(j, kind)]
        log(f"Unstop {kind}: {len(rows)} open, {len(keep)} fresher/hiring candidates")
        for j in keep:
            detail = None
            if calls < cfg["max_detail_calls"]:
                r = get(f"https://unstop.com/api/public/competition/{j['id']}")
                calls += 1
                if r is not None and r.ok:
                    detail = (r.json().get("data") or {}).get("competition")
                time.sleep(0.35)
            try:
                jobs.append(unstop_job(j, kind, detail))
            except Exception as e:  # one odd posting must not stop the run
                log(f"  skip unstop {j.get('id')}: {e}")
    return jobs


# ---------------------------------------------------------------- HackerEarth
def fetch_hackerearth():
    urls = set()
    r = get("https://www.hackerearth.com/chrome-extension/events/")
    if r is not None and r.ok:
        for e in r.json().get("response", []):
            if "hiring" in (e.get("challenge_type", "") + e.get("title", "") + e.get("url", "")).lower():
                urls.add(e["url"])
    r = get("https://www.hackerearth.com/challenges/")
    if r is not None and r.ok:
        t = r.text.replace("\\/", "/")
        for m in re.findall(r"/challenges/(?:hiring|competitive|new/competitive)/[a-z0-9\-]+/", t):
            if "hiring" in m:
                urls.add("https://www.hackerearth.com" + m)
    jobs = []
    for u in sorted(urls):
        p = get(u)
        if p is None or not p.ok:
            continue
        t = text_of(p.text)
        title = re.search(r"Sign up\s+(.{5,120}?)\s+Hosted By", t)
        mon = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
        host = re.search(rf"Hosted By\s*-?\s*(.{{2,60}}?)\s+{mon}\b", t) or re.search(r"Hosted By\s*-?\s*(\S+(?: \S+){0,2})", t)
        stamps = []
        for m in re.finditer(rf"{mon} \d{{1,2}},? \d{{4}}(?:,? \d{{1,2}}:\d{{2}} ?[AP]M)?", t):
            try:
                s = m.group(0).replace(",", "")
                for f in ("%b %d %Y %I:%M %p", "%b %d %Y %I:%M%p", "%b %d %Y", "%B %d %Y"):
                    try:
                        stamps.append(dt.datetime.strptime(s, f).replace(tzinfo=IST))
                        break
                    except ValueError:
                        pass
            except Exception:
                pass
        stamps = sorted(set(stamps))
        about = t.split("About Challenge", 1)[1] if "About Challenge" in t else ""  # skip the site menu text
        reg = re.search(r"([\d.]+K?) registrations", t)
        regs = int(float(reg.group(1).rstrip("K")) * (1000 if reg.group(1).endswith("K") else 1)) if reg else 0
        jobs.append({
            "id": "he-" + u.rstrip("/").rsplit("/", 1)[-1], "source": "HackerEarth", "kind": "Hiring challenge",
            "company": host.group(1).strip() if host else "",
            "title": title.group(1).strip() if title else u.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title(),
            "tier": 1, "why": "HackerEarth hiring challenge: every registered candidate takes the test",
            "tests": [{"label": "Online test window", "start": stamps[0].isoformat(),
                       "end": stamps[-1].isoformat() if len(stamps) > 1 else None}] if stamps else [],
            "duration": duration(t), "pattern": sections(about), "rounds": [], "batch": batches(about), "pay": "",
            "location": "Online", "deadline": stamps[-1].isoformat() if stamps else None, "registered": regs,
            "apply_url": u, "source_url": u, "summary": about[:600] or t[:600], "body": about[:5000],
        })
        time.sleep(0.5)
    log(f"HackerEarth: {len(jobs)} hiring challenges")
    return jobs


# ---------------------------------------------------------------- Amazon (official jobs feed)
def fetch_amazon():
    jobs, seen = [], set()
    for q in ["associate", "representative", "trainee", "ml data", "customer service", "quality", "analyst", "intern"]:
        r = get(f"https://www.amazon.jobs/en/search.json?base_query={requests.utils.quote(q)}"
                f"&normalized_country_code[]=IND&result_limit=50&sort=recent")
        if r is None or not r.ok:
            continue
        for a in r.json().get("jobs", []):
            title = a.get("title") or ""
            quals = text_of(a.get("basic_qualifications") or "")
            if a.get("id_icims") in seen or re.search(r"senior|\bsr\.?\b|manager|lead\b|principal|\bii\b|\biii\b|head|director|"
                                                       r"counsel|architect|specialist ii", title, re.I):
                continue
            if re.search(r"\b([2-9]|1[0-9])\+?\s*(years|yrs)", quals, re.I):
                continue
            seen.add(a.get("id_icims"))
            body = text_of((a.get("description") or "") + "\n" + quals + "\n" + (a.get("preferred_qualifications") or ""))
            url = "https://www.amazon.jobs" + (a.get("job_path") or "")
            test = re.search(r"assessment|online test|aptitude", body, re.I)
            posted = a.get("posted_date")
            jobs.append({
                "id": f"amazon-{a.get('id_icims')}", "source": "Amazon jobs", "kind": "Job", "company": "Amazon",
                "title": title, "tier": 2 if test else 3,
                "why": "Official Amazon posting mentions an assessment" if test else "Official Amazon entry-level opening",
                "tests": [], "duration": "", "pattern": sections(body) if test else [], "rounds": [],
                "batch": batches(body), "pay": "", "location": a.get("normalized_location") or a.get("location") or "",
                "deadline": None, "registered": 0, "apply_url": url, "source_url": url,
                "summary": body[:600], "body": body[:5000], "posted": None, "posted_on": posted,
            })
        time.sleep(0.4)
    merged = {}
    for j in jobs:  # Amazon lists the same role many times (one per team / requisition): one card, with a count
        j.pop("posted", None)
        k = (j["title"].lower(), j["location"].lower())
        if k in merged:
            merged[k]["openings"] = merged[k].get("openings", 1) + 1
        else:
            merged[k] = j
    for j in merged.values():
        if j.get("openings", 1) > 1:
            j["why"] += f" ({j['openings']} openings)"
    log(f"Amazon jobs: {len(merged)} entry-level India roles ({len(jobs)} postings)")
    return list(merged.values())


# ---------------------------------------------------------------- news leads (mass drives)
DRIVE_WORDS = r"(off[- ]?campus|nqt|hiring drive|mass hiring|fresher(s)? (hiring|recruitment|drive)|registration (open|starts|begins|link)|" \
              r"exam date|test date|apply (now|online)|recruitment 202\d|hiring 202\d|graduate (program|hiring)|genc|elite nlth|" \
              r"walk[- ]?in)"


def fetch_news(cfg):
    queries = cfg.get("news_queries") or [
        '"off campus" drive 2026', 'TCS NQT registration', 'Infosys fresher hiring 2026', 'Wipro Elite NLTH',
        'Accenture freshers hiring 2026', 'Cognizant GenC hiring', 'Capgemini fresher hiring 2026',
        'HCLTech freshers hiring', '"hiring challenge" 2026 freshers', 'freshers walk-in drive Hyderabad']
    jobs, seen = [], set()
    for q in queries:
        r = get("https://news.google.com/rss/search", params={"q": q + " when:10d", "hl": "en-IN", "gl": "IN", "ceid": "IN:en"})
        if r is None or not r.ok:
            continue
        for it in re.findall(r"<item>(.*?)</item>", r.text, re.S):
            title = html.unescape((re.search(r"<title>(.*?)</title>", it, re.S) or [None, ""])[1]).strip()
            link = html.unescape((re.search(r"<link>(.*?)</link>", it, re.S) or [None, ""])[1]).strip()
            pub = (re.search(r"<pubDate>(.*?)</pubDate>", it) or [None, ""])[1]
            head = title.rsplit(" - ", 1)[0]
            if head.lower() in seen or not re.search(DRIVE_WORDS, head, re.I):
                continue
            if re.search(r"placement(s)? (report|record|202)|highest package|median|iim|layoff|shares?|stock|quarter|q[1-4]\b|"
                         r"results|profit|revenue|marathon", head, re.I):
                continue
            co = next((m.group(0) for m in [re.search(rf"\b({KNOWN})\b", head.lower())] if m), "")
            if not co:
                continue
            seen.add(head.lower())
            try:
                when = dt.datetime.strptime(pub[:25].strip(), "%a, %d %b %Y %H:%M:%S").replace(tzinfo=dt.timezone.utc)
            except ValueError:
                when = None
            jobs.append({
                "id": "news-" + hashlib.sha1(head.lower().encode()).hexdigest()[:12], "source": "News", "kind": "News lead",
                "company": co.upper() if len(co) <= 4 else co.title(), "title": head,
                "tier": 2, "why": "News says a drive / hiring is open: find the OFFICIAL link before posting",
                "tests": [], "duration": "", "pattern": [], "rounds": [], "batch": batches(head), "pay": "", "location": "",
                "deadline": None, "registered": 0, "apply_url": link, "source_url": link,
                "summary": f"{title} (published {when:%d %b %Y} )" if when else title, "body": head,
            })
        time.sleep(0.5)
    log(f"News leads: {len(jobs)} drive announcements")
    return jobs


# ---------------------------------------------------------------- govt jobs, exam dates, schemes (AP / TS / Central)
GOVT_QUERIES = {
    "Govt job": ["TSPSC notification", "APPSC notification", "TGPSC notification", "TSLPRB recruitment", "AP police recruitment",
                 "Telangana DSC notification", "AP DSC notification", "Singareni recruitment", "TGSRTC recruitment",
                 "APSRTC recruitment", "SSC notification 2026", "IBPS notification", "SBI recruitment freshers",
                 "RRB notification 2026", "PSU recruitment through GATE", "UPSC notification 2026", "Telangana govt jobs notification",
                 "Andhra Pradesh govt jobs notification", "AP grama sachivalayam notification", "Telangana TET notification",
                 "AP TET notification", "BEL BHEL NTPC ONGC recruitment freshers"],
    "Exam date": ["GATE 2027 exam date", "GATE 2027 registration", "TS EAPCET 2027", "AP EAPCET 2027", "TS ICET", "AP ICET",
                  "TS PGECET", "TGPSC Group exam date", "APPSC Group exam date", "admit card released Telangana",
                  "admit card released Andhra Pradesh", "UGC NET exam date", "CAT 2026 exam", "SSC exam date 2026",
                  "IBPS exam date 2026", "TS LAWCET AP LAWCET", "polycet 2027"],
    "Scheme": ["Telangana ePASS scholarship", "Telangana post matric scholarship", "AP post matric scholarship",
               "Vidya Deevena fee reimbursement", "Andhra Pradesh fee reimbursement students", "Telangana fee reimbursement",
               "National Scholarship Portal 2026", "AICTE Pragati Saksham scholarship", "Telangana overseas scholarship",
               "AP overseas education scheme", "BC study circle free coaching Telangana", "AP free coaching students",
               "Talliki Vandanam", "PM Vidyalaxmi education loan", "scholarship last date 2026 India students",
               "Telangana government scheme students", "Telangana scheme launched", "Andhra Pradesh government scheme students",
               "Andhra Pradesh new scheme launched", "Telangana scholarship 2026", "AP scholarship 2026",
               "central government scheme students youth", "PM internship scheme", "scheme site:pib.gov.in youth",
               "Telangana unemployed youth scheme", "AP unemployment allowance", "skill development scheme Telangana Andhra"],
}
GOVT_KEEP = {
    "Govt job": r"(recruit|notification|vacanc|posts?\b|apply|application|jobs?\b|hiring|deadline|last date|dsc|tet\b)",
    "Exam date": r"(exam date|schedule|admit card|hall ticket|registration|apply|application|result|answer key|timetable|"
                 r"time table|exam on|exam pattern|counselling|notification)",
    "Scheme": r"(scheme|yojana|scholarship|allowance|subsidy|insurance|loan|stipend|internship|fee reimbursement|free (coaching|laptop|bus|training))",
}
GOVT_DROP = r"(opinion|editorial|explained:? why|stock|shares|ipo|cricket|movie|box office|murder|arrest|accident|dies|" \
            r"election result|rally|protest against|controversy|congress|bjp|ysrcp|tdp|brs|struggles|slams|criticis|alleges|funds|" \
            r"deputy cm|minister (says|reviews)|assembly|politic|urges|rahul gandhi|appeals to)"


OFFICIAL = [  # stable official portals only; anything else gets a Google "official site" search link
    (r"\bnsp\b|national scholarship|nmmss?|pre[- ]matric|post[- ]matric.*central|pragati|saksham", "National Scholarship Portal",
     "https://scholarships.gov.in"),
    (r"epass|e-pass|telangana.{0,40}(fee reimbursement|post[- ]matric|pre[- ]matric)", "Telangana ePASS", "https://telanganaepass.cgg.gov.in"),
    (r"vidya deevena|vasathi deevena|jnanabhumi|andhra.{0,40}(fee reimbursement|post[- ]matric)", "AP Jnanabhumi",
     "https://jnanabhumi.ap.gov.in"),
    (r"aicte", "AICTE", "https://www.aicte-india.org"),
    (r"pm internship", "PM Internship Scheme", "https://pminternship.mca.gov.in"),
    (r"vidyalaxmi", "PM Vidyalaxmi", "https://pmvidyalaxmi.co.in"),
    (r"appsc|andhra pradesh public service", "APPSC", "https://psc.ap.gov.in"),
    (r"tspsc|tgpsc|telangana public service", "TGPSC", "https://www.tspsc.gov.in"),
    (r"\bupsc\b", "UPSC", "https://upsc.gov.in"),
    (r"\bssc\b", "SSC", "https://ssc.gov.in"),
    (r"\bibps\b", "IBPS", "https://www.ibps.in"),
    (r"ugc net|\bnta\b|\bcuet\b|\bneet\b|jee main", "NTA", "https://nta.ac.in"),
    (r"\bsbi\b", "SBI Careers", "https://sbi.co.in/web/careers"),
    (r"slprb|ap police", "AP SLPRB", "https://slprb.ap.gov.in"),
]


def official_link(t):
    for rx, name, url in OFFICIAL:
        if re.search(rx, t, re.I):
            return {"name": name, "url": url}
    q = re.sub(r"[:|,].*$", "", t)[:80]
    return {"name": "Find official site", "url": "https://www.google.com/search?q=" + requests.utils.quote(q + " official website")}


def headline_deadline(head):
    """'last date Nov 15', 'deadline extended to November 15', 'till 20 Nov', 'closes on ...' -> date."""
    best = None
    for pos, d in dates_in(head):
        ctx = head[max(0, pos - 45): pos].lower()
        if re.search(r"last date|deadline|till|until|extended|closes|closing|by\b|before|ends|apply (by|till)|upto|up to", ctx) \
                and d > now() - dt.timedelta(days=1):
            best = d if best is None or d < best else best
    return best.replace(hour=23, minute=59).isoformat() if best else None


def govt_state(t):
    if re.search(r"telangana|\bts\b|\btg\b|\b(ts|tg)(psc|lprb|rtc|srtc|cscl|genco|transco|spdcl|npdcl|epdcl|wreis|swreis|chs|eapcet|eamcet|icet|pgecet|lawcet|edcet|polycet|tet|dsc|hc|ssdc|msrb|sche|crda|cob|bie|bse)\b|hyderabad|singareni|warangal|ts ?eapcet|ts ?icet",
                 t, re.I):
        return "Telangana"
    if re.search(r"andhra|\bap\b|\bap(psc|lprb|rtc|srtc|cscl|genco|transco|spdcl|npdcl|epdcl|wreis|swreis|chs|eapcet|eamcet|icet|pgecet|lawcet|edcet|polycet|tet|dsc|hc|ssdc|msrb|sche|crda|cob|bie|bse)\b|amaravati|vijayawada|visakhapatnam|vizag|tirupati|apsrtc|ap ?eapcet|ap ?icet|"
                 r"sachivalayam", t, re.I):
        return "Andhra Pradesh"
    return "Central / All India"


def fetch_govt(cfg):
    jobs, seen, stories = [], set(), {}
    for kind, queries in GOVT_QUERIES.items():
        for q in queries:
            r = get("https://news.google.com/rss/search", params={"q": q + " when:10d", "hl": "en-IN", "gl": "IN", "ceid": "IN:en"})
            if r is None or not r.ok:
                continue
            for it in re.findall(r"<item>(.*?)</item>", r.text, re.S)[:8]:
                title = html.unescape((re.search(r"<title>(.*?)</title>", it, re.S) or [None, ""])[1]).strip()
                link = html.unescape((re.search(r"<link>(.*?)</link>", it, re.S) or [None, ""])[1]).strip()
                pub = (re.search(r"<pubDate>(.*?)</pubDate>", it) or [None, ""])[1]
                head, _, outlet = title.rpartition(" - ")
                head = head or title
                key = re.sub(r"[^a-z0-9]", "", head.lower())[:70]
                ex = re.search(r"\b(gate|eapcet|eamcet|icet|pgecet|lawcet|edcet|polycet|ugc net|cat|neet \w+|jee \w+|"
                               r"ssc \w+|ibps \w+|rrb \w+|upsc \w+|sbi \w+|tspsc \w+|tgpsc \w+|appsc \w+|dsc|tet|tslprb|nmmss?|nsp|epass|"
                               r"vidya deevena|parivartan|post[- ]matric|pre[- ]matric|study circle|pragati|saksham)\b",
                               head.lower())
                story = ex.group(0).replace("nmmss", "nmms") if ex else " ".join(re.findall(r"[a-z0-9]+", head.lower())[:3])  # same story, many sites
                if key in seen or stories.get(story, 0) >= 2 or re.search(GOVT_DROP, head, re.I) or                         not re.search(GOVT_KEEP[kind], head, re.I):
                    continue
                seen.add(key)
                stories[story] = stories.get(story, 0) + 1
                try:
                    when = dt.datetime.strptime(pub[:25].strip(), "%a, %d %b %Y %H:%M:%S").replace(tzinfo=dt.timezone.utc)
                except ValueError:
                    when = None
                exam = []
                if kind != "Scheme":
                    ds = [d for _, d in dates_in(head) if now() - dt.timedelta(days=1) < d < now() + dt.timedelta(days=300)]
                    dl_ = headline_deadline(head)
                    ds = [d for d in ds if not dl_ or d.date() != parse_date(dl_).date()]  # the last date is shown separately
                    if ds:
                        exam = [{"label": "Date in headline (verify)", "start": min(ds).isoformat(),
                                 "end": max(ds).isoformat() if len(ds) > 1 else None}]
                official = "pib.gov.in" in link or re.search(r"\bpib\b", outlet or "", re.I)
                jobs.append({
                    "id": "gov-" + hashlib.sha1(key.encode()).hexdigest()[:12], "source": "PIB (official)" if official else "News",
                    "kind": kind, "company": govt_state(head), "title": head, "tier": 2 if kind != "Scheme" else 3,
                    "why": {"Govt job": "Govt / PSU notification in the news: open the official site before posting",
                            "Exam date": "Exam date / admit card news: confirm on the official exam site",
                            "Scheme": "Govt scheme news: confirm eligibility on the official portal"}[kind],
                    "tests": exam, "duration": "", "pattern": [], "rounds": [], "batch": batches(head), "pay": "",
                    "location": govt_state(head), "deadline": headline_deadline(head), "registered": 0, "apply_url": link,
                    "official": official_link(head),
                    "source_url": link, "summary": f"{head} ({outlet}, {when:%d %b %Y})" if when else head, "body": head,
                    "govt": True, "outlet": outlet, "published": when.isoformat() if when else None,
                })
            time.sleep(0.4)
    log(f"Govt & schemes: {len(jobs)} items (AP / TS / Central)")
    return jobs


# ---------------------------------------------------------------- official pages (change alerts)
CHROME = ROOT / "remotion" / "node_modules" / ".remotion" / "chrome-headless-shell" / "win64" / \
    "chrome-headless-shell-win64" / "chrome-headless-shell.exe"


def chrome_text(url):
    """Rendered page text via Remotion's headless Chrome (for sites that need JavaScript)."""
    import subprocess
    if not CHROME.exists():
        return ""
    try:
        r = subprocess.run([str(CHROME), "--headless", "--disable-gpu", "--no-first-run", f"--user-agent={UA['User-Agent']}",
                            "--virtual-time-budget=12000", "--dump-dom", url], capture_output=True, timeout=75,
                           creationflags=0x08000000)
        t = r.stdout.decode("utf8", "ignore")
        return "" if re.search(r"<title>\s*(Access Denied|404)", t, re.I) else text_of(t)
    except Exception:
        return ""


def fresher_part(t):
    """Only the sentences about freshers / graduates / drives, so menus and banners don't trigger alerts."""
    parts = re.findall(r"[^.\n]{0,160}(?:fresher|graduate|campus|batch|202[5-8] pass|internship|trainee|hiring drive|"
                       r"register|registration|nqt|genc|elite|assessment)[^.\n]{0,160}", t, re.I)
    return " ".join(sorted(set(p.strip() for p in parts)))


# ---------------------------------------------------------------- official pages (change alerts)
def fetch_watch(cfg, hashes):
    jobs, ok = [], 0
    for w in cfg.get("watch", []):
        t = ""
        if not w.get("chrome"):
            r = get(w["url"])
            t = text_of(r.text) if r is not None and r.ok else ""
        if not t:
            t = chrome_text(w["url"])
        if len(t) < 300:
            log(f"  watch failed (blocked or empty): {w['name']}")
            continue
        ok += 1
        t = fresher_part(t) or t
        t = re.sub(r"\d{1,2}:\d{2}|\b\d{10,}\b", "", t)  # ignore clocks / ids that change on every load
        h = hashlib.sha1(t.encode()).hexdigest()
        old = hashes.get(w["url"])
        hashes[w["url"]] = h
        if old and old != h:
            jobs.append({
                "id": "watch-" + hashlib.sha1((w["url"] + h).encode()).hexdigest()[:10], "source": "Official page",
                "kind": "Page updated", "company": w["name"], "title": "Official hiring page changed: check for a new drive",
                "tier": 2, "why": "The company's own careers page changed since the last run", "tests": [],
                "duration": "", "pattern": sections(t[:5000]), "rounds": [], "batch": batches(t), "pay": "",
                "location": "", "deadline": None, "registered": 0, "apply_url": w["url"], "source_url": w["url"],
                "summary": t[:600], "body": t[:5000], "test_words": bool(re.search(TEST_WORDS, t, re.I)),
            })
    log(f"Official pages: {ok}/{len(cfg.get('watch', []))} reachable, {len(jobs)} changed")
    return jobs


# ---------------------------------------------------------------- trust check, categories
RED_FLAGS = [
    ("Asks for money (fee / deposit)", r"(registration|training|joining|processing|security|refundable|kit)\s*(fee|charges?|deposit|amount)"
                                       r"|pay\s*(rs\.?|₹|inr)\s*\d|deposit of|fee of (rs|₹)"),
    ("Recruiter uses Gmail/Yahoo email", r"[\w.+-]+@(gmail|yahoo|outlook|hotmail|rediffmail|ymail)\.com"),
    ("Contact only on WhatsApp/Telegram", r"(whatsapp|telegram)\s*(only|us at|me at|on\s*\+?\d)|contact (on|via) (whatsapp|telegram)"),
    ("Too-good-to-be-true pay / guarantee", r"100\s*% (job|placement)|guaranteed (job|placement|income)|earn (up to )?(₹|rs\.?|inr)\s?\d[\d,]{4,}"
                                            r"[^.\n]{0,40}(home|daily|per day|week)|no interview"),
    ("Data-entry / typing / MLM style", r"network marketing|\bmlm\b|refer (and|&) earn|data entry from home|typing job|copy paste job"),
]
FREE_MAIL = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "rediffmail.com", "ymail.com"}
SKIP_DOMAINS = r"(unstop|hackerearth|linkedin|google|forms\.gle|bit\.ly|tinyurl|instagram|facebook|youtube|wa\.me|whatsapp|t\.me|" \
               r"naukri|indeed|glassdoor|ambitionbox|microsoft|apple|cloudfront|amazonaws)"
IT_WORDS = r"(developer|software|engineer|sde\b|programmer|full ?stack|front ?end|back ?end|web dev|android|ios\b|java|python|" \
           r"\.net|react|angular|node|devops|cloud|aws|azure|data (scien|analy|engineer)|machine learning|\bai\b|\bml\b|cyber|" \
           r"security analyst|qa\b|tester|testing|automation|ui/ux|sql|database|it support|network engineer|sap\b|salesforce)"
CITIES = [("Hyderabad", r"hyderabad|secunderabad|hitec"), ("Bengaluru", r"bangalore|bengaluru"), ("Chennai", r"chennai"),
          ("Pune", r"pune"), ("Mumbai", r"mumbai|navi mumbai|thane"), ("Delhi NCR", r"delhi|noida|gurgaon|gurugram|ncr"),
          ("Kolkata", r"kolkata"), ("Ahmedabad", r"ahmedabad"), ("Remote / Online", r"remote|online|work from home|wfh")]


KNOWN = """tcs|tata consultancy|infosys|wipro|hcl|hcltech|tech mahindra|cognizant|accenture|capgemini|ibm|deloitte|pwc|
ey|ernst & young|kpmg|genpact|ltimindtree|lti mindtree|l&t|larsen|mphasis|hexaware|persistent|zensar|cyient|
coforge|birlasoft|sonata|mindtree|virtusa|ust global|nttdata|ntt data|dxc|atos|cgi|oracle|sap labs|microsoft|google|
amazon|apple|adobe|salesforce|intel|nvidia|qualcomm|cisco|vmware|dell|hewlett|lenovo|samsung|
siemens|bosch|honeywell|general electric|philips|schneider|abb|texas instruments|micron|amd|synopsys|
cadence|servicenow|workday|atlassian|intuit|paypal|visa inc|mastercard|american express|amex|goldman sachs|
jp morgan|jpmorgan|morgan stanley|barclays|hsbc|citibank|deutsche bank|wells fargo|ubs|bank of america|
standard chartered|hdfc|icici|axis bank|kotak|sbi|state bank|yes bank|indusind|bajaj|tata|reliance|jio|
airtel|bharti|mahindra|adani|aditya birla|godrej|hul|hindustan unilever|unilever|p&g|procter|nestle|
pepsico|coca-cola|asian paints|maruti|hyundai|toyota|honda|uber|swiggy|zomato|flipkart|myntra|meesho|
paytm|phonepe|razorpay|cred|zerodha|groww|upstox|byju|unacademy|physicswallah|upgrad|freshworks|zoho|
postman|browserstack|chargebee|druva|innovaccer|inmobi|nykaa|lenskart|dream11|sharechat|dunzo|zepto|
blinkit|bigbasket|delhivery|urban company|makemytrip|oyo|policybazaar|pine labs|juspay|mu sigma|fractal|
tiger analytics|latentview|tredence|ganit|concentrix|teleperformance|wns|exl|firstsource|hinduja global|
startek|sutherland|ibm india|cepheid|danaher|thermo fisher|pfizer|novartis|abbott|sun pharma|dr reddy|cipla|
lupin|biocon|apollo|fortis|medtronic|walmart|tesco|ikea|decathlon|exxon|chevron|
schlumberger|caterpillar|cummins|john deere|eaton|emerson|rockwell|ericsson|nokia|vodafone|verizon|at&t|
gartner|mckinsey|bcg|bain|zs associates|ab inbev|diageo|nike|adidas|sony|panasonic|xiaomi|vivo|oppo""".replace("\n", "")
AGENCY = r"(hr solutions?|consult(ancy|ants|ing services)|placement|staffing|manpower|recruit(ment|ers|ing)|" \
         r"talent (solutions|acquisition|hub)|hiring solutions|job(s)? (portal|hub|services)|career (solutions|services)|" \
         r"\bhr\b|human resource)"
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def known_company(name, cfg=None):
    extra = "|".join(re.escape(x.lower()) for x in (cfg or {}).get("known_companies_extra", []))
    rx = rf"\b({KNOWN}{'|' + extra if extra else ''})\b"
    return bool(re.search(rx, (name or "").lower()))


def dates_in(text):
    """Dates written in posting text: '15 Nov', '15th November 2026', 'Nov 15', '15/11/2026'."""
    out, n = [], now()
    mon = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
    for m in re.finditer(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{mon},?\s*(\d{{4}})?|\b{mon}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s*(\d{{4}})?"
                         r"|\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b", text, re.I):
        g = m.groups()
        try:
            if g[0]:
                d, mo, y = int(g[0]), MONTHS[g[1][:3].lower()], int(g[2]) if g[2] else None
            elif g[3]:
                d, mo, y = int(g[4]), MONTHS[g[3][:3].lower()], int(g[5]) if g[5] else None
            else:
                d, mo, y = int(g[6]), int(g[7]), int(g[8])
            y = y or (n.year if (mo, d) >= (n.month, n.day) or n.month - mo < 2 else n.year + 1)
            out.append((m.start(), dt.datetime(y, mo, d, tzinfo=IST)))
        except (ValueError, KeyError, TypeError):
            pass
    return out


def text_test_dates(body):
    """Test / exam dates written in the description (not as a round): only dates within 90 chars of a test word."""
    found = []
    for pos, d in dates_in(body):
        ctx = body[max(0, pos - 90): pos + 40]
        if re.search(r"(test|exam|assessment|quiz|aptitude|coding round|online round|nqt|hackathon)", ctx, re.I) and \
                not re.search(r"(last date|deadline|apply by|registration (closes|ends)|posted)", ctx[-60:], re.I):
            if now() - dt.timedelta(days=2) < d < now() + dt.timedelta(days=150):
                found.append(d)
    found = sorted(set(found))
    if not found:
        return []
    return [{"label": "Test date (from posting text)", "start": found[0].isoformat(),
             "end": found[-1].isoformat() if len(found) > 1 else None}]


def red_flags(text):
    return [label for label, rx in RED_FLAGS if re.search(rx, text or "", re.I)]


def site_domain(text):
    for m in re.finditer(r"[\w.+-]+@([\w-]+(\.[\w-]+)+)", text or ""):
        d = m.group(1).lower()
        if d not in FREE_MAIL and not re.search(SKIP_DOMAINS, d):
            return d
    for m in re.finditer(r"https?://(www\.)?([\w-]+(\.[\w-]+)+)", text or ""):
        d = m.group(2).lower()
        if not re.search(SKIP_DOMAINS, d):
            return d
    return ""


def categorize(j):
    blob = f"{j.get('title', '')} {j.get('body', '')[:1500]}"
    j["category"] = "IT" if re.search(IT_WORDS, j.get("title", ""), re.I) or \
        (j.get("kind") == "Hiring challenge" and re.search(r"coding|programming|developer|software", blob, re.I)) else "Non-IT"
    loc = (j.get("location") or "") + " " + j.get("body", "")[:600]
    j["cities"] = [c for c, rx in CITIES if re.search(rx, loc, re.I)][:3] or ["Other"]
    if j.get("govt"):
        j["category"], j["cities"] = "Govt", [j.get("location") or "Central / All India"]
    j["flags"] = red_flags(j.get("body", "") + " " + j.get("title", ""))
    if not [t for t in j.get("tests", []) if t.get("start")]:
        td = text_test_dates(j.get("body", ""))
        if td:
            j["tests"] = j.get("tests", []) + td
            if j["tier"] == 3:
                j["tier"], j["why"] = 2, "Posting gives a test / exam date"
    trust_score(j)


def trust_score(j):
    """0-100. Signals we can verify without logging in anywhere; Company check adds website age."""
    s, why = 50, []
    co = j.get("company") or ""
    if known_company(co, load(CONFIG, {})):
        s += 30; why.append("+30 well-known employer")
    elif re.search(AGENCY, co, re.I):
        s -= 15; why.append("-15 HR agency / consultancy (middleman)")
    if j.get("source") == "HackerEarth":
        s += 25; why.append("+25 hosted on HackerEarth")
    reg = j.get("registered") or 0
    if reg >= 1000:
        s += 12; why.append(f"+12 {reg:,} registered")
    elif reg >= 300:
        s += 8; why.append(f"+8 {reg:,} registered")
    elif reg >= 100:
        s += 4; why.append(f"+4 {reg:,} registered")
    dom = site_domain(j.get("body", ""))
    if dom:
        s += 8; why.append(f"+8 company website / work email ({dom})")
    if j.get("pay"):
        s += 6; why.append("+6 pay disclosed")
    if j.get("tests"):
        s += 6; why.append("+6 dated test rounds")
    c = j.get("check") or {}
    age = c.get("domain_age")
    if age is not None:
        d = 20 if age >= 5 else 12 if age >= 3 else -10 if age >= 1 else -30
        s += d; why.append(f"{d:+d} website {age} yrs old")
    for f in j.get("flags") or []:
        s -= 45 if "money" in f.lower() or "too-good" in f.lower() else 25
        why.append(f"-{45 if 'money' in f.lower() or 'too-good' in f.lower() else 25} {f}")
    if j.get("source") == "Amazon jobs":
        s += 20; why.append("+20 official company job feed")
    if j.get("source") == "PIB (official)":
        s = max(s, 90); why.append("official Government of India press release (PIB)")
    elif j.get("source") == "News":
        s = min(s, 60); why.append("capped at 60: news lead, not the official page")
    s = max(0, min(100, s))
    j["trust_score"], j["trust_why"] = s, why
    j["trust"] = "good" if s >= 70 else "red" if s < 40 else "check"


AGE_CACHE = {}


def domain_age(domain):
    if domain in AGE_CACHE:
        return AGE_CACHE[domain]
    AGE_CACHE[domain] = _domain_age(domain)
    return AGE_CACHE[domain]


def _domain_age(domain):
    parts = domain.split(".")
    for i in range(len(parts) - 1):  # careers.wipro.com -> wipro.com; tcs.co.in tries co.in last
        d = ".".join(parts[i:])
        r = get(f"https://rdap.org/domain/{d}")
        if r is not None and r.ok:
            for ev in r.json().get("events", []):
                if ev.get("eventAction") == "registration":
                    when = parse_date(ev.get("eventDate"))
                    if when:
                        return d, round((now() - when).days / 365.25, 1)
            return d, None
    return domain, None


def company_check(j):
    q = requests.utils.quote(j.get("company") or j.get("title", ""))
    out = {"checked": now().isoformat(), "flags": red_flags(j.get("body", "") + " " + j.get("title", "")), "good": [],
           "linkedin": f"https://www.linkedin.com/search/results/companies/?keywords={q}",
           "reviews": f"https://www.google.com/search?q={q}+reviews+ambitionbox+glassdoor",
           "google": f"https://www.google.com/search?q={q}+company+official+website"}
    dom = site_domain(j.get("body", ""))
    if dom:
        d, age = domain_age(dom)
        out["domain"], out["domain_age"] = d, age
        if age is not None:
            (out["good"] if age >= 3 else out["flags"]).append(
                f"Website {d} registered {age} years ago" if age >= 1 else f"Website {d} is very new (under 1 year)")
    else:
        out["notes"] = ["No company website or work email in the posting: check LinkedIn / Google below"]
    if j.get("source") == "HackerEarth":
        out["good"].append("Hosted on HackerEarth (company pays HackerEarth to run it)")
    if (j.get("registered") or 0) >= 300:
        out["good"].append(f"{j['registered']:,} people already registered")
    past = sum(1 for x in load(JOBS, {}).values() if x.get("company") == j.get("company") and x.get("id") != j.get("id"))
    if past:
        out["good"].append(f"{past} other opening(s) from this company in the radar")
    out["level"] = "red" if any("money" in f.lower() or "too-good" in f.lower() for f in out["flags"]) else \
        ("good" if len(out["good"]) >= 2 and not out["flags"] else "check")
    return out


# ---------------------------------------------------------------- scoring + run
STUDENT_PRIORITY = [  # (points, label, regex) for the Govt & schemes tab: student benefits first
    (900, "🎓 Scholarship", r"scholarship|fee reimbursement|vidya deevena|vasathi deevena|epass|e-pass|post[- ]matric|"
                           r"pre[- ]matric|merit[- ]cum|nsp\b|national scholarship|pragati|saksham|overseas (study|education)"),
    (750, "🎓 Student benefit", r"stipend|internship|free (coaching|laptop|tablet|bus pass|training|hostel)|education loan|"
                                r"vidyalaxmi|study circle|skill (training|development)|student|students|youth|unemployed|"
                                r"talliki vandanam|amma vodi|fellowship"),
    (650, "📝 Registration / last date", r"registration (open|begins|starts|link|window)|apply (online|now)|last date|deadline|"
                                        r"application (form|window|correction)|extended"),
    (550, "📅 Exam / admit card / result", r"exam date|schedule|admit card|hall ticket|result|answer key|counselling|timetable|time table"),
    (450, "🏛 Govt job notification", r"recruit|notification|vacanc|posts?\b"),
]
NOT_STUDENT = r"farmer|pension|old age|senior citizen|kisan|crop|saree|cattle|fisherm|weaver|panchayat|ration"


def student_priority(j):
    t = j.get("title", "")
    for pts, label, rx in STUDENT_PRIORITY:
        if re.search(rx, t, re.I):
            return pts, label
    return 200, ""


def score(j):
    n = now()
    if j.get("govt"):
        pts, label = student_priority(j)
        j["student"] = label
        s = pts - (400 if re.search(NOT_STUDENT, j.get("title", ""), re.I) else 0)
        s += 120 if j.get("location") in ("Telangana", "Andhra Pradesh") else 0
        pub = parse_date(j.get("published"))
        if pub:
            s += max(0, 100 - (n - pub).days * 10)  # fresher news first
        d = parse_date(j.get("deadline"))
        if d:
            left = (d - n).total_seconds() / 86400
            s += max(0, 250 - left * 12) if left >= 0 else -1500  # deadline soon = post it now; passed = bottom
        return round(s)
    s = {1: 3000, 2: 2000, 3: 1000}[j["tier"]]
    starts = [parse_date(t.get("start")) for t in j.get("tests", [])]
    starts = [d for d in starts if d and d > n - dt.timedelta(days=1)]
    if starts:
        days = (min(starts) - n).days
        s += max(0, 300 - days * 15)
    d = parse_date(j.get("deadline"))
    if d:
        left = (d - n).total_seconds() / 86400
        s += max(0, 200 - left * 10) if left >= 0 else -900
    s += (j.get("trust_score", 50) - 50) * 6  # trusted jobs rise, risky ones sink (still shown, with a warning)
    if j.get("batch"):
        s += 60
    if j.get("pay"):
        s += 30
    s += min(150, (j.get("registered") or 0) / 40)
    if j.get("first_seen", "")[:10] == f"{n:%Y-%m-%d}":
        s += 80
    return round(s)


def run(manual=False):
    if not LOCK.acquire(blocking=False):
        return False
    try:
        cfg = load(CONFIG, None)
        if not cfg:
            cfg = DEFAULT_CONFIG
            save(CONFIG, cfg)
        state = load(STATE, {})
        hashes = dict(state.get("watch_hash", {}))
        state.update(running=True, started=now().isoformat(), progress="Starting…")
        save(STATE, state)
        log(f"Search started ({'button' if manual else 'scheduled/CLI'})")
        found = []
        for name, fn in [("Unstop", lambda: fetch_unstop(cfg)), ("HackerEarth", fetch_hackerearth),
                         ("Amazon", fetch_amazon), ("News", lambda: fetch_news(cfg)), ("Govt", lambda: fetch_govt(cfg)),
                         ("Official pages", lambda: fetch_watch(cfg, hashes))]:
            try:
                found += fn()
            except Exception as e:
                log(f"{name} FAILED: {e}")
        old = load(JOBS, {})
        today = f"{now():%Y-%m-%d}"
        seen_now = set()
        for j in found:
            prev = old.get(j["id"], {})
            j["first_seen"] = prev.get("first_seen", now().isoformat())
            j["last_seen"] = now().isoformat()
            for k in ("posted", "hidden", "note"):
                if k in prev:
                    j[k] = prev[k]
            j["open"] = True
            if "check" in prev:
                j["check"] = prev["check"]
            categorize(j)
            j["score"] = score(j)
            old[j["id"]] = j
            seen_now.add(j["id"])
        # auto company check (website age) for the jobs you would actually post: tier 1-2, open, not checked this week
        todo = [j for j in old.values() if j.get("open") and j["tier"] <= 2 and j["id"] in seen_now and
                (not j.get("check") or parse_date(j["check"].get("checked")) < now() - dt.timedelta(days=7))][:70]
        if todo:
            log(f"Company check: {len(todo)} test jobs")
        for j in todo:
            try:
                j["check"] = company_check(j)
                trust_score(j)
                j["check"].update(trust_score=j["trust_score"], trust=j["trust"])
                j["score"] = score(j)
            except Exception as e:
                log(f"  check failed {j['id']}: {e}")
        for k, j in old.items():  # not listed any more = closed (kept for history, hidden from the main tabs)
            if k not in seen_now and j.get("source") != "Official page":
                j["open"] = False
            d = parse_date(j.get("deadline"))
            if d and d < now():
                j["open"] = False
        cutoff = now() - dt.timedelta(days=45)
        old = {k: v for k, v in old.items() if parse_date(v.get("last_seen")) and parse_date(v["last_seen"]) > cutoff}
        save(JOBS, old)
        t1 = sum(1 for j in old.values() if j["open"] and j["tier"] == 1)
        new = sum(1 for j in old.values() if j["open"] and j["first_seen"][:10] == today)
        state = load(STATE, {})
        state.update(running=False, last_run=now().isoformat(), counts={
            "open": sum(1 for j in old.values() if j["open"]), "tier1": t1, "new_today": new},
            watch_hash=hashes)
        save(STATE, state)
        log(f"Done: {state['counts']['open']} open, {t1} guaranteed-test, {new} new today")
        return True
    finally:
        st = load(STATE, {})
        if st.get("running"):
            st["running"] = False
            save(STATE, st)
        LOCK.release()


# ---------------------------------------------------------------- reel script + roundup
def fmt_d(s):
    d = parse_date(s)
    return f"{d.day} {d:%b}" if d else ""


def pick_broll(queries, used):
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import broll_own
        clips = broll_own.load()
    except Exception:
        return ["(own B-roll library not found)"] * len(queries)
    out = []
    for q in queries:
        words = q.lower().split()
        best = sorted((c for c in clips if c["file"] not in used and c.get("orientation") != "landscape"),
                      key=lambda c: -broll_own.score(c, words))
        c = best[0] if best and broll_own.score(best[0], words) > 0 else None
        if c:
            used.add(c["file"])
            out.append(c["file"][:-4])
        else:
            out.append("face (no B-roll)")
    return out


def reel_script(j, cfg):
    company, role = j.get("company") or "Company", j.get("title", "")
    tests = [t for t in j.get("tests", []) if t.get("start")]
    dl = parse_date(j.get("deadline"))
    days = (dl - now()).days if dl else None
    ch = cfg.get("whatsapp_channel", "")
    lines = []  # (you say, on screen, broll query)
    if j.get("govt"):
        st = j.get("location", "")
        lines += [(f"{st} students, ఇది మీకోసం important update.", {"Govt job": "GOVT JOB ALERT 🚨", "Exam date": "EXAM DATE 📅",
                                                                     "Scheme": "NEW SCHEME 🏛"}[j["kind"]], "thinking"),
                  (j["title"] + ".", j["title"][:30].upper(), "office desk-work")]
        if j.get("tests"):
            t = j["tests"][0]
            lines.append((f"Date {fmt_d(t['start'])}" + (f" నుంచి {fmt_d(t['end'])}" if t.get("end") else "") + ".",
                          f"📅 {fmt_d(t['start'])}", "interview-prep"))
        lines += [("Full details official website లో check చేయండి, link మన WhatsApp channel లో పెట్టా.", "LINK IN BIO 👆", "")]
    elif j["tier"] == 1:
        lines.append(("Guys, apply చేసిన వెంటనే test link వస్తుంది. Resume shortlist కూడా లేదు.", "NO SHORTLIST 🔥",
                      "it-job test-link laptop"))
    elif days is not None and 0 <= days <= 3:
        lines.append((f"Guys, ఈ job కి ఇంకా {days or 'ఈ రోజే'} {'days' if days else ''} మాత్రమే ఉంది!".replace("  ", " "),
                      "LAST DATE TODAY 🚨" if days == 0 else f"ONLY {days} DAYS ⏰", "thinking deadline"))
    else:
        lines.append((f"{company} నుంచి freshers కి hiring వచ్చింది.", f"{company.upper()[:22]} HIRING", "mnc office hiring"))
    lines.append((f"Company {company}, role {role}.", role[:28].upper(), "office mnc campus"))
    if j.get("batch"):
        lines.append((f"{', '.join(j['batch'])} batch వాళ్ళు eligible.", f"{' / '.join(j['batch'])} BATCH ✅", "new-job joining"))
    if tests:
        t = tests[0]
        rng = fmt_d(t["start"]) + (f" నుంచి {fmt_d(t['end'])}" if t.get("end") and fmt_d(t["end"]) != fmt_d(t["start"]) else "")
        lines.append((f"Test {rng} మధ్యలో ఉంటుంది" + (f", {j['duration']}." if j.get("duration") else "."),
                      f"TEST: {fmt_d(t['start'])}" + (f" – {fmt_d(t['end'])}" if t.get("end") else ""), "test-link laptop desk-work"))
    if j.get("pattern"):
        lines.append((f"Pattern లో {', '.join(j['pattern'][:4])} ఉంటాయి.", " · ".join(j["pattern"][:3]).upper(), "interview-prep thinking"))
    if j.get("pay"):
        lines.append((f"Pay {j['pay']} అని posting లో ఇచ్చారు.", j["pay"].split(" /")[0], "salary money"))
    if j.get("location"):
        remote = re.search(r"online|remote|home", j["location"], re.I)
        lines.append((f"Location {j['location']}.", f"📍 {j['location'][:24]}", "wfh hybrid laptop" if remote else "commute office"))
    if dl and days is not None and days >= 0:
        lines.append((f"Last date {fmt_d(j['deadline'])}. Late చేయకండి.", f"LAST DATE: {fmt_d(j['deadline'])}", "thinking"))
    lines.append(("Apply link మన WhatsApp channel లో పెట్టా, channel link bio లో ఉంది. Follow చేయండి, daily updates వస్తాయి.",
                  "LINK IN BIO 👆", ""))
    clips = pick_broll([q for *_, q in lines[:-1] if q], set())
    rows, k = [], 0
    for i, (say, scr, q) in enumerate(lines, 1):
        clip = clips[k] if q else "face, CTA"
        k += 1 if q else 0
        rows.append(f"| {i} | {say} | `{scr}` | `{clip}` |")
    facts = [f"- Company: {company}", f"- Role: {role}", f"- Type: {j.get('kind')} ({j.get('category', '')})"]
    facts += [f"- {t['label']}: {fmt_d(t['start'])}" + (f" – {fmt_d(t['end'])}" if t.get("end") else "") for t in tests]
    for k2, lab in (("batch", "Batch"), ("duration", "Duration"), ("pay", "Pay (as posted)"), ("location", "Location")):
        v = j.get(k2)
        if v:
            facts.append(f"- {lab}: {', '.join(v) if isinstance(v, list) else v}")
    if j.get("deadline"):
        facts.append(f"- Apply by: {fmt_d(j['deadline'])}")
    flags = j.get("check", {}).get("flags") or j.get("flags") or []
    caption = [f"{company} {'Hiring Challenge' if j.get('kind') == 'Hiring challenge' else 'Hiring'} 🔥", ""]
    if j["tier"] == 1:
        caption.append("✅ No shortlist: test link for everyone")
    caption += [f"✅ {f[2:]}" for f in facts[3:7]]
    caption += ["", "🔗 Apply link: WhatsApp channel (link in bio)", "",
                "#jobs #freshers #hiring #telugu #itjobs #offcampus #placements #careertips"]
    md = [f"# Reel script: {company}: {role}", "",
          f"Generated by Job Radar on {now():%d %b %Y %H:%M}. Length ~25–35 s. Say it in your own words; "
          "I edit from what you actually say.", "",
          f"**Official page (check it's live before recording):** {j.get('apply_url')}", ""]
    if flags:
        md += ["**⚠️ Company check warnings:** " + "; ".join(flags), ""]
    md += ["## Script", "", "| # | You say | On screen | B-roll |", "|---|---|---|---|", *rows, "",
           "**Sounds:** notification on line 1, clock_tick on the last-date line, ding on the CTA. Everything else silent.",
           "", "## On-screen facts (all from the posting)", "", *facts, "", "## Instagram caption", "", "```", *caption, "```",
           "", "## WhatsApp channel post", "", "Use the 🟢 WhatsApp post button on the card (adds your channel link).",
           "", "## Recording checklist", "", "- [ ] Official page still open, dates unchanged",
           "- [ ] iPhone 1080p, face at eye level, quiet room", "- [ ] Say the hook in the first 2 seconds",
           f"- [ ] Drop the recording in reels\\inbox and tell me: \"edit the {company} reel\""]
    return "\n".join(md)


def roundup(cfg):
    n = now()
    jobs = [j for j in load(JOBS, {}).values() if j.get("open") and not j.get("hidden") and not j.get("flags")
            and (not parse_date(j.get("deadline")) or parse_date(j["deadline"]) > n)]
    jobs.sort(key=lambda j: (j["tier"], -j.get("score", 0)))
    top = jobs[:6]
    L = [f"🔥 *THIS WEEK'S TOP {len(top)} FRESHER JOBS* 🔥", f"_{n:%d %b %Y}_", ""]
    for i, j in enumerate(top, 1):
        line = f"*{i}. {j.get('company') or j['title']}*: {j['title']}"
        bits = []
        if j["tier"] == 1:
            bits.append("✅ test for everyone")
        if j.get("batch"):
            bits.append("🎓 " + ", ".join(j["batch"]))
        t = next((t for t in j.get("tests", []) if t.get("start")), None)
        if t:
            bits.append("📝 test " + fmt_d(t["start"]))
        if j.get("deadline"):
            bits.append("⏰ till " + fmt_d(j["deadline"]))
        L += [line, " · ".join(bits) if bits else "", "👉 " + j["apply_url"], ""]
    L += ["━━━━━━━━━━━━━━", "📢 *Daily job updates for freshers*"]
    if cfg.get("whatsapp_channel"):
        L += ["Join our WhatsApp channel 👇", cfg["whatsapp_channel"]]
    if cfg.get("instagram"):
        L.append(f"📸 Instagram: {cfg['instagram']}")
    L += ["", "🙌 Forward this to your friends!"]
    return "\n".join(x for x in L if x is not None)


def student_roundup(cfg):
    n = now()
    items = [j for j in load(JOBS, {}).values() if j.get("govt") and j.get("open") and not j.get("hidden")
             and j.get("student") and (not parse_date(j.get("deadline")) or parse_date(j["deadline"]) > n)
             and not re.search(NOT_STUDENT, j.get("title", ""), re.I)]
    items.sort(key=lambda j: -j.get("score", 0))
    groups = [("🎓 *SCHOLARSHIPS & STUDENT BENEFITS*", ("🎓 Scholarship", "🎓 Student benefit")),
              ("📝 *REGISTRATIONS & LAST DATES*", ("📝 Registration / last date",)),
              ("📅 *EXAMS, ADMIT CARDS & RESULTS*", ("📅 Exam / admit card / result",)),
              ("🏛 *GOVT JOB NOTIFICATIONS*", ("🏛 Govt job notification",))]
    L = ["📢 *THIS WEEK FOR AP & TS STUDENTS* 📢", f"_{n:%d %b %Y}_", ""]
    for head, labels in groups:
        g = [j for j in items if j["student"] in labels][:4]
        if not g:
            continue
        L += [head]
        for j in g:
            d = parse_date(j.get("deadline"))
            tag = f" ⏰ *last date {d.day} {d:%b}*" if d else ""
            st = {"Telangana": "TS", "Andhra Pradesh": "AP"}.get(j.get("location"), "All India")
            L += [f"• [{st}] {j['title']}{tag}", f"  👉 {j['official']['url'] if (j.get('official') or {}).get('name', 'Find official site') != 'Find official site' else j['apply_url']}"]
        L.append("")
    L += ["✅ Always confirm on the official website", "━━━━━━━━━━━━━━", "📢 *Daily updates for AP & TS students*"]
    if cfg.get("whatsapp_channel"):
        L += ["Join our WhatsApp channel 👇", cfg["whatsapp_channel"]]
    if cfg.get("instagram"):
        L.append(f"📸 Instagram: {cfg['instagram']}")
    L += ["", "🙌 Forward this to your friends & juniors!"]
    return "\n".join(L)


# ---------------------------------------------------------------- web page
def local_net(ip):
    import ipaddress
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
        return a.is_loopback or a.is_private
    except ValueError:
        return False


def lan_ip():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no packet is sent; just picks the Wi-Fi interface
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def handle_one_request(self):
        if not local_net(self.client_address[0]):
            self.close_connection = True
            return
        super().handle_one_request()

    def send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode("utf8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.split("?")[0] in ("/", "/index.html"):
            return self.send(200, PAGE.read_bytes(), "text/html")
        if self.path.startswith("/api/jobs"):
            jobs = sorted(load(JOBS, {}).values(), key=lambda j: -j.get("score", 0))
            jobs = [{k: v for k, v in j.items() if k != "body"} for j in jobs]  # full text stays on the laptop
            return self.send(200, json.dumps({"jobs": jobs, "state": load(STATE, {})}, ensure_ascii=False))
        if self.path.startswith("/api/roundup"):
            cfg = load(CONFIG, DEFAULT_CONFIG)
            text = student_roundup(cfg) if "student" in self.path else roundup(cfg)
            return self.send(200, json.dumps({"text": text}, ensure_ascii=False))
        if self.path.split("?")[0] == "/icon.png":
            icon = ROOT / "assets" / "brand" / "radar_icon.png"
            return self.send(200, icon.read_bytes(), "image/png") if icon.exists() else self.send(404, "{}")
        if self.path.startswith("/posts/"):
            import job_radar_cards as C
            p = (C.OUT / requests.utils.unquote(self.path.split("?")[0][7:])).resolve()
            if C.OUT.resolve() in p.parents and p.suffix == ".png" and p.exists():
                return self.send(200, p.read_bytes(), "image/png")
            return self.send(404, "{}")
        if self.path.startswith("/api/carousel"):
            import job_radar_cards as C
            kind = "student" if "student" in self.path else "jobs"
            n = now()
            allj = [j for j in load(JOBS, {}).values() if j.get("open") and not j.get("hidden") and not j.get("flags")
                    and (not parse_date(j.get("deadline")) or parse_date(j["deadline"]) > n)]
            if kind == "student":
                pool = sorted([j for j in allj if j.get("govt") and j.get("student") and
                               not re.search(NOT_STUDENT, j["title"], re.I)], key=lambda j: -j.get("score", 0))
                items, per_org, per_label = [], {}, {}
                for j in pool:  # variety: max 2 per organisation (APPSC, NSP…) and max 3 per type
                    org = re.findall(r"[a-z0-9]+", j["title"].lower())[:1]
                    org = org[0] if org else ""
                    if per_org.get(org, 0) >= 2 or per_label.get(j["student"], 0) >= 3:
                        continue
                    per_org[org] = per_org.get(org, 0) + 1
                    per_label[j["student"]] = per_label.get(j["student"], 0) + 1
                    items.append(j)
                    if len(items) == 6:
                        break
            else:
                items = sorted([j for j in allj if not j.get("govt") and j.get("source") not in ("News", "Official page")],
                               key=lambda j: (j["tier"], -j.get("score", 0)))[:6]
            paths = C.carousel(items, load(CONFIG, DEFAULT_CONFIG), kind)
            return self.send(200, json.dumps({"images": ["/posts/" + p.relative_to(C.OUT).as_posix() for p in paths],
                                              "folder": str(paths[0].parent)}))
        if self.path.startswith("/api/state"):
            return self.send(200, json.dumps(load(STATE, {}), ensure_ascii=False))
        if self.path.startswith("/api/config"):
            c = load(CONFIG, DEFAULT_CONFIG)
            return self.send(200, json.dumps({"whatsapp_channel": c.get("whatsapp_channel", ""),
                                              "instagram": c.get("instagram", "@sreekar_polepalli")}))
        self.send(404, "{}")

    def do_POST(self):
        if not local_net(self.client_address[0]):  # this laptop or a device on the home Wi-Fi only
            return self.send(403, "{}")
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/run":
            if load(STATE, {}).get("running") and LOCK.locked():
                return self.send(200, '{"started": false, "reason": "already running"}')
            threading.Thread(target=run, kwargs={"manual": True}, daemon=True).start()
            return self.send(200, '{"started": true}')
        if self.path == "/api/image":
            import job_radar_cards as C
            j = load(JOBS, {}).get(body.get("id"))
            if not j:
                return self.send(404, '{"error": "job not found"}')
            paths = C.post_images(j, load(CONFIG, DEFAULT_CONFIG))
            return self.send(200, json.dumps({"images": ["/posts/" + p.relative_to(C.OUT).as_posix() for p in paths],
                                              "folder": str(paths[0].parent)}))
        if self.path in ("/api/check", "/api/script"):
            jobs = load(JOBS, {})
            j = jobs.get(body.get("id"))
            if not j:
                return self.send(404, '{"error": "job not found"}')
            if self.path == "/api/check":
                j["check"] = company_check(j)
                trust_score(j)
                j["check"]["trust_score"], j["check"]["trust"], j["check"]["trust_why"] = j["trust_score"], j["trust"], j["trust_why"]
                with MARK_LOCK:
                    allj = load(JOBS, {})
                    if j["id"] in allj:
                        allj[j["id"]].update(check=j["check"], trust_score=j["trust_score"], trust=j["trust"],
                                             trust_why=j["trust_why"])
                        save(JOBS, allj)
                return self.send(200, json.dumps(j["check"], ensure_ascii=False))
            if "check" not in j:
                j["check"] = company_check(j)
            text = reel_script(j, load(CONFIG, DEFAULT_CONFIG))
            slug = re.sub(r"[^a-z0-9]+", "-", f"{j.get('company', '')} {j.get('title', '')}".lower()).strip("-")[:50]
            out = ROOT / "reels" / "scripts" / "jobs" / f"{now():%Y-%m-%d}_{slug}.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf8")
            return self.send(200, json.dumps({"text": text, "path": str(out)}, ensure_ascii=False))
        if self.path == "/api/config":
            c = load(CONFIG, None) or dict(DEFAULT_CONFIG)
            for k in ("whatsapp_channel", "instagram"):
                if k in body:
                    v = str(body[k]).strip()[:200]
                    if k == "whatsapp_channel" and v and not re.match(r"^https://(www\.)?whatsapp\.com/channel/\S+$", v):
                        return self.send(400, '{"error": "Paste the channel link (https://whatsapp.com/channel/...)"}')
                    c[k] = v
            save(CONFIG, c)
            return self.send(200, '{"ok": true}')
        if self.path == "/api/mark":
            with MARK_LOCK:
                jobs = load(JOBS, {})
                j = jobs.get(body.get("id"))
                if j:
                    for k in ("posted", "hidden", "note"):
                        if k in body:
                            j[k] = body[k]
                    save(JOBS, jobs)
            return self.send(200, '{"ok": true}')
        self.send(404, "{}")


def serve(open_browser):
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)  # laptop + phone on the same home Wi-Fi
    except OSError:  # already running: just open it
        if open_browser:
            webbrowser.open(f"http://localhost:{PORT}")
        print(f"Job Radar already running at http://localhost:{PORT}")
        return
    st = load(STATE, {})
    if st.get("running"):
        st["running"] = False
        save(STATE, st)
    ip = lan_ip()
    print(f"Job Radar: http://localhost:{PORT}  (close this window to stop the page)")
    if ip:
        print(f"On your phone (same Wi-Fi): http://{ip}:{PORT}")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    srv.serve_forever()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="open", choices=["open", "fetch", "serve"])
    a = ap.parse_args()
    if a.cmd == "fetch":
        sys.exit(0 if run() else 1)
    serve(a.cmd == "open")


if __name__ == "__main__":
    main()
