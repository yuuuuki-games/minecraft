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
CUMULATIVE_CSV_PATH = DATA_DIR / "cumulative-downloads.csv"

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


def load_csv():
    rows = {}
    if CUMULATIVE_CSV_PATH.exists():
        with CUMULATIVE_CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                rows[(row["date"], row["work"])] = int(row["downloads"])
    return rows


def save_csv(rows):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with CUMULATIVE_CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "work", "downloads"])
        writer.writeheader()
        for (date, work), count in sorted(rows.items()):
            writer.writerow({"date": date, "work": work, "downloads": count})


def make_graph(rows, work_name, graph_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.ticker import MaxNLocator

    plt.rcParams["font.family"] = "Noto Sans CJK JP"
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    observations = {
        datetime.strptime(date, "%Y-%m-%d").date(): count
        for (date, name), count in rows.items()
        if name == work_name
    }
    if not observations:
        plt.figure(figsize=(10, 4.5))
        plt.text(0.5, 0.5, "累計ダウンロード数のデータはありません",
                 ha="center", va="center")
        plt.axis("off")
        plt.savefig(graph_path, format="svg", bbox_inches="tight")
        plt.close()
        return

    first = min(observations)
    last = max(max(observations), datetime.strptime(TODAY, "%Y-%m-%d").date())
    all_dates = []
    values = []
    day = first
    latest_value = observations[first]
    while day <= last:
        all_dates.append(day)
        latest_value = observations.get(day, latest_value)
        values.append(latest_value)
        day += timedelta(days=1)

    plt.figure(figsize=(11, 5))
    plt.plot(all_dates, values, marker="o", markersize=3, linewidth=1.8)
    plt.title(f"{work_name} の累計ダウンロード数")
    plt.xlabel("日付（日本時間）")
    plt.ylabel("累計ダウンロード数")
    plt.grid(True, alpha=0.3)
    axis = plt.gca()
    axis.set_ylim(0, max(1, max(values) * 1.1))
    axis.yaxis.set_major_locator(MaxNLocator(integer=True))
    axis.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y/%m/%d"))
    plt.gcf().autofmt_xdate()
    plt.tight_layout()
    plt.savefig(graph_path, format="svg", bbox_inches="tight")
    plt.close()


def main():
    works = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    rows = load_csv()
    cumulative_by_work = {work["name"]: 0 for work in works}
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

            cumulative_by_work[work_name] += int(asset.get("download_count", 0))

    for work_name, total in cumulative_by_work.items():
        rows[(TODAY, work_name)] = total
    all_worlds_total = sum(cumulative_by_work.values())
    rows[(TODAY, "全ワールド")] = all_worlds_total

    save_csv(rows)
    for index, work in enumerate(works, start=1):
        graph_path = GRAPH_DIR / f"downloads-{index:02d}.svg"
        make_graph(rows, work["name"], graph_path)
    make_graph(rows, "全ワールド", GRAPH_DIR / "downloads-all.svg")

    print(f"Updated statistics for {TODAY} (JST).")
    for name, total in cumulative_by_work.items():
        print(f"- {name}: {total} total downloads")
    print(f"- All worlds: {all_worlds_total} total downloads")


if __name__ == "__main__":
    main()
