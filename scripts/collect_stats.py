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
FORMAT_CSV_PATH = DATA_DIR / "cumulative-downloads-by-format.csv"

OWNER, REPO = os.environ["GITHUB_REPOSITORY"].split("/", 1)
TOKEN = os.environ.get("GITHUB_TOKEN", "")
API_ROOT = f"https://api.github.com/repos/{OWNER}/{REPO}"
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
    if FORMAT_CSV_PATH.exists():
        with FORMAT_CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                rows[(row["date"], row["work"], row["format"])] = int(row["downloads"])
    return rows


def save_csv(rows):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with FORMAT_CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "work", "format", "downloads"])
        writer.writeheader()
        for (date, work, file_format), count in sorted(rows.items()):
            writer.writerow({
                "date": date,
                "work": work,
                "format": file_format,
                "downloads": count,
            })


def make_graph(rows, work_name, graph_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.ticker import MaxNLocator

    plt.rcParams["font.family"] = "Noto Sans CJK JP"
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    observations = {}
    for (date, name, file_format), count in rows.items():
        if name == work_name:
            date_value = datetime.strptime(date, "%Y-%m-%d").date()
            observations.setdefault(file_format, {})[date_value] = count
    if not observations:
        plt.figure(figsize=(10, 4.5))
        plt.text(0.5, 0.5, "形式別の累計ダウンロード数のデータはありません",
                 ha="center", va="center")
        plt.axis("off")
        plt.savefig(graph_path, format="svg", bbox_inches="tight")
        plt.close()
        return

    first = min(date for format_data in observations.values() for date in format_data)
    last = max(
        max(date for format_data in observations.values() for date in format_data),
        datetime.strptime(TODAY, "%Y-%m-%d").date(),
    )
    all_dates = []
    day = first
    while day <= last:
        all_dates.append(day)
        day += timedelta(days=1)

    plt.figure(figsize=(11, 5))
    all_values = []
    total_values = [0] * len(all_dates)
    for file_format, format_data in sorted(observations.items()):
        values = []
        latest_value = 0
        for index, date in enumerate(all_dates):
            latest_value = format_data.get(date, latest_value)
            values.append(latest_value)
            total_values[index] += latest_value
        all_values.extend(values)
        plt.plot(
            all_dates,
            values,
            marker="o",
            markersize=3,
            linewidth=1.8,
            label=file_format,
        )
    all_values.extend(total_values)
    plt.plot(
        all_dates,
        total_values,
        marker="o",
        markersize=3,
        linewidth=2.4,
        label="合計",
        zorder=3,
    )
    plt.title(f"{work_name} の形式別累計ダウンロード数")
    plt.xlabel("日付（日本時間）")
    plt.ylabel("累計ダウンロード数")
    plt.grid(True, alpha=0.3)
    axis = plt.gca()
    axis.set_ylim(0, max(1, max(all_values) * 1.1))
    axis.yaxis.set_major_locator(MaxNLocator(integer=True))
    axis.xaxis.set_major_locator(mdates.DayLocator(interval=1))
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y/%m/%d"))
    axis.legend(title="形式")
    plt.gcf().autofmt_xdate()
    plt.tight_layout()
    plt.savefig(graph_path, format="svg", bbox_inches="tight")
    plt.close()


def main():
    works = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    rows = load_csv()
    cumulative_by_work = {work["name"]: {} for work in works}
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
            file_format = Path(asset_name).suffix
            if not file_format:
                continue

            format_counts = cumulative_by_work[work_name]
            format_counts[file_format] = (
                format_counts.get(file_format, 0) + int(asset.get("download_count", 0))
            )

    formats_by_work = {work["name"]: set() for work in works}
    for _, work_name, file_format in rows:
        if work_name in formats_by_work:
            formats_by_work[work_name].add(file_format)

    for work in works:
        work_name = work["name"]
        format_counts = cumulative_by_work[work_name]
        formats_by_work[work_name].update(format_counts)
        for file_format in formats_by_work[work_name]:
            rows[(TODAY, work_name, file_format)] = format_counts.get(file_format, 0)

    all_formats = set().union(*formats_by_work.values()) if formats_by_work else set()
    for file_format in all_formats:
        rows[(TODAY, "全ワールド", file_format)] = sum(
            format_counts.get(file_format, 0)
            for format_counts in cumulative_by_work.values()
        )

    save_csv(rows)
    for index, work in enumerate(works, start=1):
        graph_path = GRAPH_DIR / f"downloads-{index:02d}.svg"
        make_graph(rows, work["name"], graph_path)
    make_graph(rows, "全ワールド", GRAPH_DIR / "downloads-all.svg")

    print(f"Updated statistics for {TODAY} (JST).")
    for name, format_counts in cumulative_by_work.items():
        for file_format, total in sorted(format_counts.items()):
            print(f"- {name} [{file_format}]: {total} total downloads")


if __name__ == "__main__":
    main()
