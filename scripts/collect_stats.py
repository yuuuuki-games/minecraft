import csv
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "works.json"
DATA_DIR = ROOT / "data"
GRAPH_DIR = ROOT / "graphs"
CSV_PATH = DATA_DIR / "downloads.csv"
SNAPSHOT_PATH = DATA_DIR / "asset-snapshots.json"
GRAPH_PATH = GRAPH_DIR / "downloads.svg"

OWNER, REPO = os.environ["GITHUB_REPOSITORY"].split("/", 1)
TOKEN = os.environ.get("GITHUB_TOKEN", "")
API_ROOT = f"https://api.github.com/repos/{OWNER}/{REPO}"
ASSET_EXTENSIONS = (".mcworld", ".mcpack", ".zip")
TODAY = datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()


def api_get(url):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "release-download-stats",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"GitHub API request failed: {url}: {exc}") from exc


def get_releases():
    releases = []
    page = 1
    while True:
        batch = api_get(f"{API_ROOT}/releases?per_page=100&page={page}")
        if not batch:
            break
        releases.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return releases


def load_json(path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_csv():
    rows = {}
    if CSV_PATH.exists():
        with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                rows[(row["date"], row["work"])] = int(row["downloads"])
    return rows


def save_csv(rows):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "work", "downloads"])
        writer.writeheader()
        for (date, work), count in sorted(rows.items()):
            writer.writerow({"date": date, "work": work, "downloads": count})


def make_graph(rows, work_names):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    plt.rcParams["font.family"] = "Noto Sans CJK JP"
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    dates_in_data = [datetime.strptime(d, "%Y-%m-%d").date() for d, _ in rows]
    if not dates_in_data:
        plt.figure(figsize=(10, 4.5))
        plt.text(0.5, 0.5, "Download history will appear after the first run.",
                 ha="center", va="center")
        plt.axis("off")
        plt.savefig(GRAPH_PATH, format="svg", bbox_inches="tight")
        plt.close()
        return

    first = min(dates_in_data)
    last = max(max(dates_in_data), datetime.strptime(TODAY, "%Y-%m-%d").date())
    all_dates = []
    day = first
    while day <= last:
        all_dates.append(day)
        day += timedelta(days=1)

    plt.figure(figsize=(11, 5))
    for work in work_names:
        values = [rows.get((d.isoformat(), work), 0) for d in all_dates]
        plt.plot(all_dates, values, marker="o", markersize=3, linewidth=1.8, label=work)
    plt.title("Daily GitHub Release Downloads")
    plt.xlabel("Date (Japan Standard Time)")
    plt.ylabel("Downloads per day")
    plt.grid(True, alpha=0.3)
    plt.gca().xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    plt.gcf().autofmt_xdate()
    plt.legend(loc="best")
    plt.tight_layout()
    plt.savefig(GRAPH_PATH, format="svg", bbox_inches="tight")
    plt.close()


def main():
    works = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    snapshots = load_json(SNAPSHOT_PATH, {})
    rows = load_csv()

    # Prepare a zero row for each configured work for today.
    for work in works:
        rows.setdefault((TODAY, work["name"]), 0)

    delta_by_work = {work["name"]: 0 for work in works}
    current_snapshots = dict(snapshots)
    releases = get_releases()

    for release in releases:
        if release.get("draft"):
            continue
        tag = release.get("tag_name", "")
        matching = [w for w in works if tag.startswith(w["tag_prefix"])]
        if not matching:
            continue

        # A tag prefix should identify only one configured work.
        if len(matching) > 1:
            raise RuntimeError(f"Tag prefix collision for tag: {tag}")
        work_name = matching[0]["name"]

        for asset in release.get("assets", []):
            asset_name = asset.get("name", "").lower()
            if not asset_name.endswith(ASSET_EXTENSIONS):
                continue

            asset_id = str(asset["id"])
            current_count = int(asset.get("download_count", 0))

            # First time seeing an asset: use its current count as the baseline,
            # not as today's downloads. This avoids counting historical downloads
            # as if they happened today.
            if asset_id in snapshots:
                previous_count = int(snapshots[asset_id])
                delta = max(0, current_count - previous_count)
                delta_by_work[work_name] += delta

            current_snapshots[asset_id] = current_count

    # Add today's increments. If manually rerun on the same JST date, add only
    # the newly observed increments, so repeated runs do not overwrite the day.
    for work_name, delta in delta_by_work.items():
        key = (TODAY, work_name)
        rows[key] = rows.get(key, 0) + delta

    save_csv(rows)
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(
        json.dumps(current_snapshots, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    make_graph(rows, [w["name"] for w in works])

    print(f"Updated statistics for {TODAY} (JST).")
    for name, delta in delta_by_work.items():
        print(f"- {name}: +{delta}")


if __name__ == "__main__":
    main()
