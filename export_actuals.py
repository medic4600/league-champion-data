#!/usr/bin/env python3
"""
export_actuals.py — League Champion / Delivery 4.17

Pulls ONE NFL season of weekly PLAYER stats from nflverse (via nflreadpy)
and writes a small JSON file that the League Champion iOS app fetches and
scores itself (half-PPR) using its own ScoringRules.

WHY THIS RUNS ON YOUR MAC (not inside Claude's sandbox):
    nflverse data lives on GitHub's release servers, which Claude's sandbox
    is blocked from reaching. Your Mac has open internet, so the pull works
    here. This is a manual / periodic export — run it whenever you want
    fresh data, then update the hosted file.

WHAT IT EMITS:
    RAW stat lines only (passing / rushing / receiving + fumbles + 2pt). It
    deliberately does NOT compute fantasy points — the app does that with
    YOUR league's half-PPR rules, so the numbers are always correct for your
    league no matter what.

SCOPE (Delivery 4.17 v1):
    Offensive skill positions only: QB, RB, WR, TE. (Kickers and team
    defenses aren't in this table; the app treats their recent-form as
    neutral for now.)

USAGE:
    1) One-time install:
           pip3 install nflreadpy
    2) Confirm the column names (the "decode-confirm" step — paste the
       output back to Claude before the Swift gets built):
           python3 export_actuals.py --print-columns
    3) Generate the file (defaults to the 2025 season):
           python3 export_actuals.py
       or pick a season / output path explicitly:
           python3 export_actuals.py --season 2025 --out actuals_2025.json
"""

import argparse
import json
from datetime import datetime, timezone

import nflreadpy as nfl


# Offensive skill positions we keep (Delivery 4.17 v1 scope).
KEEP_POSITIONS = {"QB", "RB", "WR", "TE"}

# The app's JSON contract version. Bump only if the shape below changes.
SCHEMA_VERSION = 1


def first_present(row, candidates, default=None):
    """Return row[col] for the first column name that exists and isn't None.

    nflverse occasionally renames columns across versions (e.g.
    `recent_team` -> `team`, `interceptions` -> `passing_interceptions`).
    This helper makes the export resilient to whichever name is present,
    which is exactly what closes the two open-question column names from
    the Phase 2 plan.
    """
    for col in candidates:
        if col in row and row[col] is not None:
            return row[col]
    return default


def num(value):
    """Coerce a possibly-missing / None stat to a float."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def load_season(season):
    """Load one season of weekly player stats as a list of plain dicts."""
    df = nfl.load_player_stats(seasons=[season])
    # nflreadpy returns a Polars DataFrame; to_dicts() gives plain rows.
    return df.to_dicts(), list(df.columns)


def print_columns(season):
    """Decode-confirm: show the real column names + how the ambiguous ones
    will resolve. This is the gate before any Swift mapping is trusted."""
    _, columns = load_season(season)
    print(f"\n{len(columns)} columns in load_player_stats(seasons=[{season}]):\n")
    for c in sorted(columns):
        print(f"  {c}")
    print("\nAmbiguous-name check (what the export WILL use):")
    checks = {
        "player_id":     ["player_id", "gsis_id"],
        "name":          ["player_display_name", "player_name"],
        "team":          ["team", "recent_team"],
        "position":      ["position", "position_group"],
        "interceptions": ["interceptions", "passing_interceptions"],
    }
    for label, cands in checks.items():
        present = [c for c in cands if c in columns]
        chosen = present[0] if present else "*** NONE FOUND ***"
        print(f"  {label:14s} -> {chosen}   (candidates present: {present})")
    print()


def build_rows(raw_rows):
    """Map nflverse rows into the app's JSON contract (offense only, REG)."""
    out = []
    for r in raw_rows:
        # Regular season only.
        if first_present(r, ["season_type"], "REG") != "REG":
            continue
        # Offensive skill positions only.
        pos = first_present(r, ["position", "position_group"])
        if pos not in KEEP_POSITIONS:
            continue

        fumbles_lost = (
            num(first_present(r, ["sack_fumbles_lost"]))
            + num(first_present(r, ["rushing_fumbles_lost"]))
            + num(first_present(r, ["receiving_fumbles_lost"]))
        )
        two_pt = (
            num(first_present(r, ["passing_2pt_conversions"]))
            + num(first_present(r, ["rushing_2pt_conversions"]))
            + num(first_present(r, ["receiving_2pt_conversions"]))
        )

        out.append({
            "gsisId":              first_present(r, ["player_id", "gsis_id"]),
            "name":                first_present(r, ["player_display_name", "player_name"], ""),
            "team":                first_present(r, ["team", "recent_team"], ""),
            "position":            pos,
            "week":                int(num(first_present(r, ["week"]))),
            "passingYards":        num(first_present(r, ["passing_yards"])),
            "passingTDs":          num(first_present(r, ["passing_tds"])),
            "interceptions":       num(first_present(r, ["interceptions", "passing_interceptions"])),
            "rushingYards":        num(first_present(r, ["rushing_yards"])),
            "rushingTDs":          num(first_present(r, ["rushing_tds"])),
            "receptions":          num(first_present(r, ["receptions"])),
            "receivingYards":      num(first_present(r, ["receiving_yards"])),
            "receivingTDs":        num(first_present(r, ["receiving_tds"])),
            "fumblesLost":         fumbles_lost,
            "twoPointConversions": two_pt,
        })
    return out


def main():
    parser = argparse.ArgumentParser(description="Export one NFL season of weekly actuals to JSON.")
    parser.add_argument("--season", type=int, default=2025, help="Season year (default: 2025).")
    parser.add_argument("--out", type=str, default=None, help="Output path (default: actuals_<season>.json).")
    parser.add_argument("--print-columns", action="store_true", help="Decode-confirm: print column names and exit.")
    args = parser.parse_args()

    if args.print_columns:
        print_columns(args.season)
        return

    print(f"Loading {args.season} weekly player stats from nflverse ...")
    raw_rows, _ = load_season(args.season)
    rows = build_rows(raw_rows)

    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "season": args.season,
        "seasonType": "REG",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": "nflverse load_player_stats (CC-BY 4.0)",
        "rows": rows,
    }

    out_path = args.out or f"actuals_{args.season}.json"
    with open(out_path, "w") as f:
        json.dump(payload, f, separators=(",", ":"))

    players = len({r["gsisId"] for r in rows})
    weeks = sorted({r["week"] for r in rows})
    span = f"{weeks[0]}..{weeks[-1]}" if weeks else "none"
    print(f"\nWrote {out_path}")
    print(f"  rows:    {len(rows)}")
    print(f"  players: {players}")
    print(f"  weeks:   {span}  ({len(weeks)} weeks)")
    print("\nNext: put this file on GitHub and copy its RAW link (Claude will walk you through it).")


if __name__ == "__main__":
    main()
