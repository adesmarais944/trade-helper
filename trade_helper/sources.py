"""League platforms (Sleeper, ESPN) translated into one shape the rest of the tool uses.

Everything downstream speaks Sleeper: player ids are Sleeper ids (FantasyCalc and the
projections are keyed by them), scoring uses Sleeper stat names, and roster slots use
Sleeper slot names (QB, RB, FLEX, BN, ...).
"""
import re
from dataclasses import dataclass
from datetime import date, datetime

from .sleeper import SKILL_POSITIONS

ESPN_API = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}"
ESPN_SLOTS = {0: "QB", 2: "RB", 3: "WRRB_FLEX", 4: "WR", 5: "REC_FLEX", 6: "TE", 7: "SUPER_FLEX",
              16: "DEF", 17: "K", 20: "BN", 23: "FLEX"}
ESPN_IR_SLOT = 21
ESPN_POSITIONS = {1: "QB", 2: "RB", 3: "WR", 4: "TE"}
# ESPN stat id -> Sleeper stat name, for the offensive stats that matter to skill players.
ESPN_STATS = {3: "pass_yd", 4: "pass_td", 19: "pass_2pt", 20: "pass_int", 24: "rush_yd", 25: "rush_td",
              26: "rush_2pt", 42: "rec_yd", 43: "rec_td", 44: "rec_2pt", 53: "rec", 72: "fum_lost"}
ESPN_TE_SLOT, ESPN_REC = "6", 53


@dataclass
class LeagueData:
    name: str
    season: str
    scoring: dict            # Sleeper stat name -> points
    roster_positions: list   # Sleeper slot names, one per active lineup/bench spot (no IR)
    ir_slots: int
    position_limits: dict    # max players per position on the whole roster (incl. IR); missing = no limit
    trade_deadline: int      # NFL week, 0 = none
    deadline_date: date      # exact deadline when the platform gives one (ESPN), else None
    rosters: list            # dicts: roster_id, name, username, wins, losses, players, reserve
    my_roster_id: int


def sleeper_source(api, cfg, week):
    league, users, rosters = api.league(), api.users(), api.rosters()
    user_by_id = {u["user_id"]: u for u in users}
    out = []
    for r in rosters:
        user = user_by_id.get(r.get("owner_id"), {})
        out.append({
            "roster_id": r["roster_id"],
            "name": (user.get("metadata") or {}).get("team_name") or user.get("display_name") or f"Team {r['roster_id']}",
            "username": user.get("display_name", ""),
            "logins": {(user.get("display_name") or "").lower(), (user.get("username") or "").lower()},
            "wins": r["settings"].get("wins", 0), "losses": r["settings"].get("losses", 0),
            "players": r.get("players") or [], "reserve": set(r.get("reserve") or []),
        })
    wanted = cfg["my_username"].lower()
    me = next((r for r in out if wanted in r["logins"]), None)
    if me is None:
        raise SystemExit(f"No team owned by {cfg['my_username']!r}. Owners: {', '.join(r['username'] for r in out)}")
    settings = league["settings"]
    return LeagueData(
        name=league["name"], season=league["season"], scoring=league["scoring_settings"],
        roster_positions=league["roster_positions"], ir_slots=settings.get("reserve_slots") or 0,
        position_limits={pos: settings[f"position_limit_{pos.lower()}"] for pos in SKILL_POSITIONS
                         if settings.get(f"position_limit_{pos.lower()}")},
        trade_deadline=settings.get("trade_deadline") or 0, deadline_date=None,
        rosters=out, my_roster_id=me["roster_id"],
    )


def espn_source(api, cfg, week, db):
    secrets = (cfg["secrets"].get("espn") or {})
    if not secrets.get("espn_s2") or not secrets.get("swid"):
        raise SystemExit("ESPN leagues need espn_s2 and swid in secrets.yaml (see the comments there).")
    season = str(cfg.get("season") or api.state()["season"])
    try:
        data = api.cache.get_json(
            ESPN_API.format(season=season, league_id=cfg["league_id"]), api.ttl["league"],
            params=[("view", v) for v in ("mSettings", "mTeam", "mRoster")],
            name=f"espn_{cfg['league_id']}_{season}",
            cookies={"espn_s2": secrets["espn_s2"], "SWID": secrets["swid"]},
        )
    except Exception as e:
        if "401" in str(e):
            raise SystemExit("ESPN refused access (401). Refresh espn_s2 and swid in secrets.yaml.")
        raise
    settings = data["settings"]

    slots = []
    for slot_id, n in sorted((int(k), v) for k, v in settings["rosterSettings"]["lineupSlotCounts"].items()):
        if slot_id != ESPN_IR_SLOT:
            slots += [ESPN_SLOTS.get(slot_id, f"SLOT{slot_id}")] * n
    ir_slots = settings["rosterSettings"]["lineupSlotCounts"].get(str(ESPN_IR_SLOT), 0)
    limits = settings["rosterSettings"].get("positionLimits") or {}
    position_limits = {pos: limits[str(i)] for i, pos in ESPN_POSITIONS.items() if (limits.get(str(i)) or 0) > 0}

    scoring = {}
    for item in settings["scoringSettings"]["scoringItems"]:
        stat = ESPN_STATS.get(item["statId"])
        if stat:
            scoring[stat] = item.get("points", 0)
            te = (item.get("pointsOverrides") or {}).get(ESPN_TE_SLOT)
            if item["statId"] == ESPN_REC and te is not None:
                scoring["bonus_rec_te"] = te - scoring[stat]

    to_sleeper = _espn_to_sleeper(db)
    members = {m["id"]: m.get("displayName", "") for m in data.get("members", [])}
    rosters = []
    for t in data["teams"]:
        players, reserve = [], set()
        for e in t["roster"]["entries"]:
            pl = e["playerPoolEntry"]["player"]
            pos = ESPN_POSITIONS.get(pl.get("defaultPositionId"))
            pid = to_sleeper(e["playerId"], pl.get("fullName", ""), pos)
            if pid is None:  # K, D/ST or unmatched: still takes a roster spot
                if pos:
                    print(f"ESPN: no Sleeper match for {pl.get('fullName')} ({pos}); he won't be valued")
                pid = f"espn:{e['playerId']}"
            players.append(pid)
            if e["lineupSlotId"] == ESPN_IR_SLOT:
                reserve.add(pid)
        record = t.get("record", {}).get("overall", {})
        rosters.append({
            "roster_id": t["id"],
            "name": (t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}").strip() or f"Team {t['id']}",
            "username": members.get(t.get("primaryOwner"), ""),
            "wins": record.get("wins", 0), "losses": record.get("losses", 0),
            "players": players, "reserve": reserve,
        })
    my_id = int(cfg["team_id"])
    if my_id not in {r["roster_id"] for r in rosters}:
        teams = ", ".join(f"{r['roster_id']} {r['name']}" for r in rosters)
        raise SystemExit(f"No ESPN team {my_id}. Teams: {teams}")

    deadline_ms = (settings.get("tradeSettings") or {}).get("deadlineDate")
    deadline = datetime.fromtimestamp(deadline_ms / 1000).date() if deadline_ms and deadline_ms > 0 else None
    return LeagueData(
        name=settings["name"], season=season, scoring=scoring, roster_positions=slots, ir_slots=ir_slots,
        position_limits=position_limits,
        # NFL weeks are 7 days apart, so count whole weeks from today's week.
        trade_deadline=week + (deadline - date.today()).days // 7 if deadline else 0,
        deadline_date=deadline, rosters=rosters, my_roster_id=my_id,
    )


def _norm(name):
    name = re.sub(r"[^a-z ]", "", name.lower().replace("-", " "))
    return " ".join(w for w in name.split() if w not in {"jr", "sr", "ii", "iii", "iv", "v"})


def _espn_to_sleeper(db):
    """Match ESPN players to Sleeper ids: Sleeper's espn_id when it has one (often missing),
    else normalized name + position, preferring active players on an NFL team."""
    by_espn, by_name = {}, {}
    for pid, p in db.items():
        if p.get("position") not in SKILL_POSITIONS:
            continue
        if p.get("espn_id"):
            by_espn[str(p["espn_id"])] = pid
        name = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}"
        by_name.setdefault((_norm(name), p["position"]), []).append(pid)

    def lookup(espn_id, name, pos):
        if pos is None:
            return None
        if str(espn_id) in by_espn:
            return by_espn[str(espn_id)]
        matches = by_name.get((_norm(name), pos), [])
        return max(matches, key=lambda pid: (bool(db[pid].get("active")), bool(db[pid].get("team"))), default=None)
    return lookup
