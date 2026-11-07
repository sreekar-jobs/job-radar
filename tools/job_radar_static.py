"""Build the cloud (GitHub Pages) version of Job Radar after `job_radar.py fetch`.

  python tools/job_radar_static.py [--out site] [--images 25]

Writes site/index.html (the same page, in STATIC mode) + site/data.json (jobs, roundups, reel scripts, company checks)
+ site/posts/... (post images for the top items and both carousels). The page then works with no server:
Search now opens the GitHub "Run workflow" page, Mark posted / Hide are saved on the phone.
"""
import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import job_radar as R  # noqa: E402
import job_radar_cards as C  # noqa: E402

REPO = "sreekar-jobs/job-radar"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    ap.add_argument("--images", type=int, default=25)
    a = ap.parse_args()
    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "posts").mkdir(parents=True)
    C.OUT = out / "posts"  # images go straight into the site

    cfg = R.load(R.CONFIG, R.DEFAULT_CONFIG)
    jobs = R.load(R.JOBS, {})
    n = R.now()
    open_jobs = sorted([j for j in jobs.values() if j.get("open")], key=lambda j: -j.get("score", 0))

    # pre-made things the laptop server would make on click: images, reel scripts, company checks (top items)
    picks = [j for j in open_jobs if j["tier"] == 1 or j.get("trust") == "good"][: a.images]
    picks += [j for j in open_jobs if j.get("govt") and j.get("student")][:15]
    images, scripts = {}, {}
    for j in {x["id"]: x for x in picks}.values():
        try:
            images[j["id"]] = [p.relative_to(out).as_posix() for p in C.post_images(j, cfg)]
            scripts[j["id"]] = R.reel_script(j, cfg)
        except Exception as e:  # one odd item must not stop the build
            print("skip", j["id"], e)

    def carousel(kind):
        allj = [j for j in open_jobs if not j.get("hidden") and not j.get("flags")
                and (not R.parse_date(j.get("deadline")) or R.parse_date(j["deadline"]) > n)]
        if kind == "student":
            pool, items, per = [j for j in allj if j.get("govt") and j.get("student")], [], {}
            for j in pool:
                org = (j["title"].lower().split() or [""])[0]
                if per.get(org, 0) < 2:
                    per[org] = per.get(org, 0) + 1
                    items.append(j)
                if len(items) == 6:
                    break
        else:
            items = sorted([j for j in allj if not j.get("govt") and j.get("source") not in ("News", "Official page")],
                           key=lambda j: (j["tier"], -j.get("score", 0)))[:6]
        return [p.relative_to(out).as_posix() for p in C.carousel(items, cfg, kind)]

    data = {
        "built": n.isoformat(),
        "jobs": [{k: v for k, v in j.items() if k != "body"} for j in sorted(jobs.values(), key=lambda j: -j.get("score", 0))],
        "state": R.load(R.STATE, {}),
        "config": {"whatsapp_channel": cfg.get("whatsapp_channel", ""), "instagram": cfg.get("instagram", "@sreekar_polepalli")},
        "roundup": R.roundup(cfg), "student_roundup": R.student_roundup(cfg),
        "images": images, "scripts": scripts,
        "carousels": {"jobs": carousel("jobs"), "student": carousel("student")},
        "run_url": f"https://github.com/{REPO}/actions/workflows/radar.yml",
    }
    data["state"].pop("watch_hash", None)
    (out / "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf8")
    page = (Path(__file__).resolve().parent / "job_radar_page.html").read_text(encoding="utf8")
    page = page.replace("<script>", "<script>window.STATIC=true;</script>\n<script>", 1)
    (out / "index.html").write_text(page, encoding="utf8")
    (out / ".nojekyll").write_text("")
    icon = R.ROOT / "assets" / "brand" / "radar_icon.png"
    if icon.exists():
        shutil.copy2(icon, out / "icon.png")  # iPhone home-screen icon
    print(f"site built: {len(data['jobs'])} jobs, {len(images)} image sets, {len(scripts)} scripts -> {out}")


if __name__ == "__main__":
    main()
