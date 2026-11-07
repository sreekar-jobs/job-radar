"""job-radar-publish: send the laptop's latest Job Radar code to the cloud page (github.com/sreekar-jobs/job-radar).

  job-radar-publish            check, copy, upload if anything changed, then start a cloud run (~7 min to the phone)
  job-radar-publish --no-run   upload only (the page updates at the next 09:00 run)

Copies ONLY: tools/job_radar.py, job_radar_page.html, job_radar_cards.py, job_radar_static.py, assets/fonts/*,
assets/brand/profile.jpg. Never videos, .env, keys or anything else. The cloud config.json (no Chrome page watch)
is not overwritten.
"""
import argparse
import ast
import filecmp
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLOUD = Path(r"C:\Users\nikhi\job-radar-cloud")
GH = r"C:\Program Files\GitHub CLI\gh.exe"
REPO = "sreekar-jobs/job-radar"
FILES = [f"tools/{n}" for n in ("job_radar.py", "job_radar_page.html", "job_radar_cards.py", "job_radar_static.py")]
FILES += [p.relative_to(ROOT).as_posix() for p in (ROOT / "assets" / "fonts").glob("*")] + \
    ["assets/brand/profile.jpg", "assets/brand/radar_icon.png", "tools/job_radar_publish.py"]


def git(*a, check=True):
    r = subprocess.run(["git", *a], cwd=CLOUD, capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"git {' '.join(a)} failed:\n{r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-run", action="store_true")
    a = ap.parse_args()
    if not (CLOUD / ".git").exists():
        sys.exit(f"Cloud folder missing: {CLOUD}")
    # 1. never publish broken code
    for f in FILES:
        if f.endswith(".py"):
            try:
                ast.parse((ROOT / f).read_text(encoding="utf8"))
            except SyntaxError as e:
                sys.exit(f"NOT published: {f} has an error (line {e.lineno}): {e.msg}")
    page = (ROOT / "tools/job_radar_page.html").read_text(encoding="utf8")
    if page.count("<script>") != page.count("</script>"):
        sys.exit("NOT published: job_radar_page.html looks broken (script tags)")
    # 2. get the cloud's latest (the daily bot commits job data) then copy what changed
    git("pull", "--rebase", "--quiet")
    changed = []
    for f in FILES:
        src, dst = ROOT / f, CLOUD / f
        if src.exists() and (not dst.exists() or not filecmp.cmp(src, dst, shallow=False)):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            changed.append(f)
    if not changed:
        print("Cloud already has the latest Job Radar code. Nothing to publish.")
        return
    print("Publishing:", ", ".join(changed))
    git("add", *changed)
    git("-c", "user.name=sreekar-jobs", "-c", "user.email=sreekar-jobs@users.noreply.github.com",
        "commit", "-q", "-m", "Update Job Radar from laptop: " + ", ".join(Path(c).name for c in changed))
    git("push", "--quiet")
    print(f"Uploaded to github.com/{REPO}")
    if not a.no_run:
        r = subprocess.run([GH, "workflow", "run", "radar.yml", "-R", REPO], capture_output=True, text=True)
        print("Cloud run started: the phone page updates in ~7 min." if r.returncode == 0 else
              f"Uploaded, but could not start the run ({r.stderr.strip()}); it updates at 09:00 IST.")


if __name__ == "__main__":
    main()
