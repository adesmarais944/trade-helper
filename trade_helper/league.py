"""Load the league from Sleeper and value every rostered skill player.

Value model (all on the FantasyCalc scale, top player ~10000):
  short_value  next N weeks of projected points under this league's scoring (nearer weeks
               weigh more), as value over replacement, mapped onto the FantasyCalc value curve
               by rank. Byes count as 0; this week's points are scaled by injury status.
  long_value   FantasyCalc redraft value (rest-of-season market), or a CSV override; players
               FantasyCalc doesn't list fall back to rest-of-season projections.
  value        w * short + (1 - w) * long, with w = current_week / final_week.
"""
import csv
from dataclasses import dataclass, field
from datetime import date, timedelta

from .lineup import FLEX_ELIGIBLE, LineupModel
from .sleeper import SKILL_POSITIONS, SleeperClient

FLAG_RATIO = 1.35        # short/long gap that earns an upside (^) or win-now (!) flag
FLAG_MIN_VALUE = 1500    # ignore flags on fringe players
UNAVAILABLE = {"IR", "Out", "PUP", "Sus", "NA", "Doubtful"}   # never claimed off waivers


@dataclass
class Player:
    pid: str
    name: str
    pos: str
    nfl_team: str
    injury: str = ""
    rookie: bool = False
    week_pts: float = 0.0       # this week's projection (used to pick waiver claims)
    short_pts: float = 0.0
    ros_pts: float = 0.0
    short_value: float = 0.0
    long_value: float = 0.0
    value: float = 0.0
    byes: list = field(default_factory=list)
    star: bool = False
    owner: "Team" = None

    @property
    def upside(self):
        return self.long_value >= FLAG_MIN_VALUE and self.long_value >= FLAG_RATIO * self.short_value

    @property
    def win_now(self):
        return self.short_value >= FLAG_MIN_VALUE and self.short_value >= FLAG_RATIO * self.long_value

    def __hash__(self):
        return hash(self.pid)

    def __eq__(self, other):
        return isinstance(other, Player) and other.pid == self.pid


@dataclass
class Team:
    roster_id: int
    owner_id: str
    name: str
    username: str
    wins: int
    losses: int
    players: list          # skill-position players (incl. IR)
    capacity: int          # active (non-IR) roster spots available to skill players
    reserve: set           # pids currently on IR
    ir_slots: int

    def record(self):
        return f"{self.wins}-{self.losses}"


@dataclass
class League:
    name: str
    season: str
    week: int
    final_week: int
    trade_deadline: int
    short_weight: float
    short_weeks: list
    model: LineupModel
    teams: list
    me: Team
    players: dict          # pid -> Player, every valued skill player (rostered or not)
    waiver_pool: list      # free agents a team would claim into an open roster spot
    depth_targets: dict    # players per position a team typically rosters
    num_teams: int
    scoring_rec: float

    @property
    def deadline_passed(self):
        return bool(self.trade_deadline) and self.week > self.trade_deadline

    @property
    def weeks_to_deadline(self):
        return self.trade_deadline - self.week if self.trade_deadline else None

    def team_of(self, pid):
        p = self.players.get(pid)
        return p.owner if p else None

    def rostered(self):
        return [p for t in self.teams for p in t.players]


def _points(stats, scoring):
    """Score projected stats with the league's settings; fall back to Sleeper's half-PPR total."""
    hits = [stats[k] * v for k, v in scoring.items() if k in stats and v]
    if hits:
        return sum(hits)
    return stats.get("pts_half_ppr", 0.0)


def _status_factor(status, game_date, factors, today=None):
    """Share of this week's projection a player keeps given his injury status. Questionable
    only counts once the final injury report is out, so a midweek rest day doesn't."""
    if status not in factors:
        return 1.0
    if status == "Questionable":
        if not game_date:
            return 1.0
        game = date.fromisoformat(game_date)
        lead = 1 if game.weekday() == 3 else 2   # Thursday games' final report is Wednesday
        if (today or date.today()) < game - timedelta(days=lead):
            return 1.0
    return factors[status]


def _replacement_levels(points, pos_of, roster_positions, num_teams):
    """Points of the best player left at each position after every team fills its starters."""
    by_pos = {pos: sorted((pts for pid, pts in points.items() if pos_of.get(pid) == pos), reverse=True)
              for pos in SKILL_POSITIONS}
    taken = {pos: roster_positions.count(pos) * num_teams for pos in SKILL_POSITIONS}
    for slot, eligible in FLEX_ELIGIBLE.items():
        for _ in range(roster_positions.count(slot) * num_teams):
            best = max(eligible, key=lambda pos: by_pos[pos][taken[pos]] if taken[pos] < len(by_pos[pos]) else -1)
            taken[best] += 1
    return {pos: by_pos[pos][taken[pos]] if taken[pos] < len(by_pos[pos]) else 0.0 for pos in SKILL_POSITIONS}


def _rank_to_curve(scores, curve):
    """Map each player's score to the value at the same rank on the market curve. Players
    below replacement keep sliding down the curve instead of dropping straight to 0."""
    ranked = sorted(scores, key=lambda pid: scores[pid], reverse=True)
    return {pid: (curve[i] if i < len(curve) else 0.0) for i, pid in enumerate(ranked)}


def _load_overrides(path, name_index):
    if not path or not path.exists():
        return {}
    out = {}
    with open(path, encoding="utf-8") as f:
        rows = csv.DictReader(line for line in f if line.strip() and not line.lstrip().startswith("#"))
        for row in rows:
            key, value = (row.get("player") or "").strip(), (row.get("value") or "").strip()
            if not key or not value:
                continue
            pid = key if key.isdigit() else name_index.get(key.lower())
            if pid is None:
                print(f"overrides: no player named {key!r}, skipped")
                continue
            out[pid] = float(value)
    return out


def load_league(cfg, refresh=False):
    api = SleeperClient(cfg, refresh=refresh)
    league, users, rosters, state = api.league(), api.users(), api.rosters(), api.state()
    db = api.players()

    settings = league["settings"]
    scoring = league["scoring_settings"]
    roster_positions = league["roster_positions"]
    num_teams = settings.get("num_teams") or len(rosters)
    season = league["season"]
    week = state["week"] if state.get("season_type") == "regular" else 0
    final_week = cfg["final_week"]
    short_weight = min(max(week / final_week, 0.0), 1.0)

    # --- projections ---
    first_week = max(week, 1)
    ros_weeks = list(range(first_week, final_week + 1))
    short_weeks = ros_weeks[: cfg["short_term_weeks"]]
    week_weights = cfg["short_term_week_weights"] or [1.0]
    status_factors = cfg["status_factors"] or {}
    # short_pts is plain projected points (shown in tables); short_score weighs nearer weeks more.
    week_pts, short_pts, short_score, ros_pts, byes = {}, {}, {}, {}, {}
    all_teams = {p.get("team") for p in db.values() if p.get("team") and p.get("position") in SKILL_POSITIONS}
    for wk in ros_weeks:
        rows = api.projections(season, wk)
        # Sleeper lists bye-week teams too, just without an opponent.
        playing = {r.get("team") for r in rows if r.get("team") and r.get("opponent")}
        for t in all_teams - playing:
            byes.setdefault(t, []).append(wk)
        for r in rows:
            pid = r["player_id"]
            pts = _points(r.get("stats") or {}, scoring)
            if wk == first_week:
                pts *= _status_factor((db.get(pid) or {}).get("injury_status"), r.get("date"), status_factors)
                week_pts[pid] = pts
            ros_pts[pid] = ros_pts.get(pid, 0.0) + pts
            if wk in short_weeks:
                i = short_weeks.index(wk)
                short_pts[pid] = short_pts.get(pid, 0.0) + pts
                short_score[pid] = short_score.get(pid, 0.0) + pts * week_weights[min(i, len(week_weights) - 1)]

    pos_of = {pid: p.get("position") for pid, p in db.items() if p.get("position") in SKILL_POSITIONS}

    # --- market values ---
    num_qbs = 2 if "SUPER_FLEX" in roster_positions else 1
    fc = api.fantasycalc_values(num_teams, num_qbs, scoring.get("rec", 0))
    fc_values = {str(row["player"]["sleeperId"]): float(row["value"])
                 for row in fc if row["player"].get("sleeperId")}
    curve = sorted(fc_values.values(), reverse=True)

    def vor(points):
        pts = {pid: v for pid, v in points.items() if pid in pos_of}
        repl = _replacement_levels(pts, pos_of, roster_positions, num_teams)
        return {pid: v - repl[pos_of[pid]] for pid, v in pts.items() if v > 0}

    short_values = _rank_to_curve(vor(short_score), curve)
    ros_values = _rank_to_curve(vor(ros_pts), curve)
    floor = curve[-1] if curve else 0.0

    name_index = {}
    for pid, p in db.items():
        if pid in pos_of:
            name_index.setdefault(_name(p).lower(), pid)
    overrides = _load_overrides(cfg["overrides_file"], name_index)

    players = {}
    for pid in set(pos_of) & (set(fc_values) | set(short_values) | set(ros_values) | set(overrides)
                              | {x for r in rosters for x in (r.get("players") or [])}):
        p = db[pid]
        if pid in overrides:
            long_v = overrides[pid]
        elif pid in fc_values:
            long_v = fc_values[pid]
        else:  # not on FantasyCalc's list: projections only, capped below the market's floor
            long_v = min(ros_values.get(pid, 0.0), floor)
        short_v = short_values.get(pid, 0.0)
        team = p.get("team") or "FA"
        players[pid] = Player(
            pid=pid, name=_name(p), pos=p["position"], nfl_team=team,
            injury=p.get("injury_status") or "", rookie=p.get("years_exp") == 0,
            week_pts=week_pts.get(pid, 0.0), short_pts=short_pts.get(pid, 0.0), ros_pts=ros_pts.get(pid, 0.0),
            short_value=short_v, long_value=long_v,
            value=short_weight * short_v + (1 - short_weight) * long_v,
            byes=[w for w in byes.get(team, []) if w in short_weeks],
        )
    for p in sorted(players.values(), key=lambda p: p.value, reverse=True)[: cfg["star_count"]]:
        p.star = True

    # --- teams ---
    user_by_id = {u["user_id"]: u for u in users}
    ir_slots = settings.get("reserve_slots") or 0
    teams = []
    for r in rosters:
        user = user_by_id.get(r.get("owner_id"), {})
        roster_ids = r.get("players") or []
        reserve = set(r.get("reserve") or [])
        skill = [players[pid] for pid in roster_ids if pid in players]
        # K/DEF and other non-skill players still take active roster spots.
        other_active = [pid for pid in roster_ids if pid not in players and pid not in reserve]
        team = Team(
            roster_id=r["roster_id"], owner_id=r.get("owner_id"),
            name=(user.get("metadata") or {}).get("team_name") or user.get("display_name") or f"Team {r['roster_id']}",
            username=user.get("display_name", ""),
            wins=r["settings"].get("wins", 0), losses=r["settings"].get("losses", 0),
            players=skill, capacity=len(roster_positions) - len(other_active),
            reserve=reserve & {p.pid for p in skill}, ir_slots=ir_slots,
        )
        for p in skill:
            p.owner = team
        teams.append(team)

    wanted = cfg["my_username"].lower()
    me = next((t for t in teams if t.username.lower() == wanted
               or (user_by_id.get(t.owner_id, {}).get("username") or "").lower() == wanted), None)
    if me is None:
        raise SystemExit(f"No team owned by {cfg['my_username']!r}. Owners: {', '.join(t.username for t in teams)}")

    # Waiver pool: top free agents per position by this week's projection. A team claiming
    # into an open spot picks whichever of these helps its lineup most.
    waiver_pool = []
    for pos in SKILL_POSITIONS:
        fa = [p for p in players.values() if p.owner is None and p.pos == pos
              and p.nfl_team != "FA" and p.injury not in UNAVAILABLE]
        waiver_pool += sorted(fa, key=lambda p: p.week_pts, reverse=True)[: cfg["waiver_candidates"]]

    # Depth targets: how many players at each position teams in this league actually roster
    # (e.g. QB 2 even in a 1-QB league), never fewer than the starting slots.
    depth_targets = {}
    for pos in SKILL_POSITIONS:
        avg = sum(1 for t in teams for p in t.players if p.pos == pos) / max(len(teams), 1)
        depth_targets[pos] = max(round(avg), roster_positions.count(pos))
    depth_targets.update(cfg["depth_targets"] or {})
    model = LineupModel(roster_positions, cfg["bench_weights"], waiver_pool, depth_targets,
                        cfg["surplus_weight"], cfg["te_flex_factor"])

    return League(
        name=league["name"], season=season, week=week, final_week=final_week,
        trade_deadline=settings.get("trade_deadline") or 0, short_weight=short_weight,
        short_weeks=short_weeks, model=model,
        teams=teams, me=me, players=players, waiver_pool=waiver_pool, depth_targets=depth_targets, num_teams=num_teams, scoring_rec=scoring.get("rec", 0),
    )


def _name(p):
    return p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
