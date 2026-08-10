#!/usr/bin/env python3
"""
Availability Watch — weekly dispersion flags for League Champion.

Runs in a scheduled GitHub Action in medic4600/league-champion-data.
Downloads DynastyProcess's weekly FantasyPros ECR scrape, computes the
Rock-1-validated normalized dispersion for the newest in-season weekly
rank pages, and writes availability_<season>.json.

Math (frozen per Rock 1 verdict, 2026-08-10 — do not tweak):
    within each position page, sorted by ECR rank:
        disp = log( sd / rolling-median-7 of sd by rank )
    flag = disp in the top quartile within that position.

Preseason / offseason: the weekly pages don't exist, so the script writes
a valid file with an empty players list. That is correct behavior.
"""

import io
import json
import sys
import urllib.request
from datetime import datetime, timezone

import numpy as np
import pandas as pd

FPECR_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_fpecr.parquet"
PLAYERIDS_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv"

# The four weekly in-season rank pages validated in Rock 1.
# RB/WR/TE pages are PPR (disclosed limitation); QB page is scoring-neutral.
WEEKLY_PAGES = {
    "/nfl/rankings/qb.php": "QB",
    "/nfl/rankings/ppr-rb.php": "RB",
    "/nfl/rankings/ppr-wr.php": "WR",
    "/nfl/rankings/ppr-te.php": "TE",
}

ROLLING_WINDOW = 7          # rolling median window over sd, by rank
FLAG_QUANTILE = 0.75        # top quartile of disp within position = flagged
MAX_SCRAPE_AGE_DAYS = 10    # stale scrape -> treat as no data


def season_for(today: datetime) -> int:
    """NFL season year: Jan/Feb scrapes belong to the prior calendar year's season."""
    return today.year if today.month >= 3 else today.year - 1


def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "league-champion-availability/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def compute_flags(weekly: pd.DataFrame) -> pd.DataFrame:
    """Input: one scrape date's rows for the four weekly pages.
    Output: same rows plus disp + flagged columns."""
    out = []
    for page, pos in WEEKLY_PAGES.items():
        grp = weekly[weekly["fp_page"] == page].copy()
        if grp.empty:
            continue
        grp = grp.sort_values("ecr").reset_index(drop=True)
        grp["sd"] = pd.to_numeric(grp["sd"], errors="coerce")
        rolling_med = grp["sd"].rolling(ROLLING_WINDOW, center=True, min_periods=1).median()
        with np.errstate(divide="ignore", invalid="ignore"):
            grp["disp"] = np.log(grp["sd"] / rolling_med)
        # sd <= 0 (perfect consensus) or undefined median -> not flaggable
        grp.loc[~np.isfinite(grp["disp"]), "disp"] = np.nan
        valid = grp["disp"].dropna()
        if valid.empty:
            grp["flagged"] = False
        else:
            threshold = valid.quantile(FLAG_QUANTILE)
            grp["flagged"] = grp["disp"].notna() & (grp["disp"] >= threshold)
        grp["position"] = pos
        out.append(grp)
    if not out:
        return pd.DataFrame()
    return pd.concat(out, ignore_index=True)


def main() -> None:
    now = datetime.now(timezone.utc)
    season = season_for(now)

    fpecr = pd.read_parquet(io.BytesIO(download(FPECR_URL)))
    ids = pd.read_csv(io.BytesIO(download(PLAYERIDS_URL)))

    fpecr["scrape_date"] = pd.to_datetime(fpecr["scrape_date"])
    latest = fpecr["scrape_date"].max()
    age_days = (now.replace(tzinfo=None) - latest).days

    players = []
    scrape_note = latest.date().isoformat()

    if age_days <= MAX_SCRAPE_AGE_DAYS:
        weekly = fpecr[
            (fpecr["scrape_date"] == latest)
            & (fpecr["fp_page"].isin(WEEKLY_PAGES.keys()))
        ]
        flagged = compute_flags(weekly)
        if not flagged.empty:
            ids = ids[ids["fantasypros_id"].notna()].copy()
            ids["fp_id_str"] = ids["fantasypros_id"].astype("Int64").astype(str)
            idmap = ids.drop_duplicates("fp_id_str").set_index("fp_id_str")
            for _, row in flagged.iterrows():
                gsis = idmap["gsis_id"].get(row["id"])
                sleeper = idmap["sleeper_id"].get(row["id"])
                players.append({
                    "gsisId": None if pd.isna(gsis) else str(gsis),
                    "sleeperId": None if pd.isna(sleeper) else str(int(sleeper)) if isinstance(sleeper, float) else str(sleeper),
                    "name": row["player"],
                    "team": None if pd.isna(row["tm"]) else str(row["tm"]),
                    "position": row["position"],
                    "flagged": bool(row["flagged"]),
                })

    doc = {
        "schemaVersion": 1,
        "season": season,
        "generatedAt": now.isoformat(),
        "scrapeDate": scrape_note,
        "source": "FantasyPros ECR via DynastyProcess (github.com/dynastyprocess/data), weekly scrape",
        "players": players,
    }

    out_path = f"availability_{season}.json"
    with open(out_path, "w") as f:
        json.dump(doc, f, indent=1)
    n_flagged = sum(1 for p in players if p["flagged"])
    print(f"Wrote {out_path}: {len(players)} players evaluated, {n_flagged} flagged, scrape {scrape_note}")


if __name__ == "__main__":
    sys.exit(main())

