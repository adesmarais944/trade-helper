"""Load the league from Sleeper and value every rostered skill player.

Value model (FantasyCalc scale, top player ~10000):
  market_value    CSV override, else FantasyCalc redraft value (rest-of-season market, fetched
                  for this league's team count, QB count and PPR). Players FantasyCalc doesn't
                  list fall back to rest-of-season projections, capped below its lowest value.
  scoring_factor  rest-of-season projected points under this league's scoring divided by the
                  points under the scoring FantasyCalc assumes (4-pt passing TD, no TE premium).
                  1.0 when the two match.
  value           market_value * scoring_factor.
Byes, injuries and short-term projections are shown as tags only; they don't change value.
"""
import csv
from dataclasses import dataclass, field
from datetime import date

from .lineup import FLEX_ELIGIBLE, LineupModel
from .sleeper import SKILL_POSITIONS, SleeperClient
from .sources import espn_source, sleeper_source

FLAG_RATIO = 1.35        # projection/market gap that earns an upside (^) or win-now (!) flag
FLAG_MIN_VALUE = 1500    # ignore flags on fringe players
# Scoring FantasyCalc values assume, beyond the PPR setting they're fetched with.
FC_SCORING = {"pass_td": 4, "bonus_rec_te": 0}
FACTOR_LIMITS = (0.75, 1.35)    # cap on the scoring adjustment
FACTOR_MIN_PTS = 20             # below this many projected ROS points, don't adjust
FC_PPR = (0, 0.5, 1)            # PPR settings FantasyCalc offers
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
    short_pts: float = 0.0      # next short_term_weeks of projections (display and flags)
    proj_value: float = 0.0     # short_pts as value over replacement on the market curve (flags only)
    market_value: float = 0.0
    scoring_factor: float = 1.0
    value: float = 0.0
    byes: list = field(default_factory=list)
    star: bool = False
    owner: "Team" = None

    @property
    def upside(self):
        return self.value >= FLAG_MIN_VALUE and self.value >= FLAG_RATIO * self.proj_value

    @property
    def win_now(self):
        return self.proj_value >= FLAG_MIN_VALUE and self.proj_value >= FLAG_RATIO * self.value

    def __hash__(self):
        return hash(self.pid)

    def __eq__(self, other):
        return isinstance(other, Player) and other.pid == self.pid


@dataclass
class Team:
    roster_id: int
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
    trade_deadline: int        # NFL week, 0 = none
    deadline_date: date        # exact deadline when the platform gives one (ESPN), else None
    short_weeks: list
    model: LineupModel
    teams: list
    me: Team
    players: dict          # pid -> Player, every valued skill player (rostered or not)
    waiver_pool: list      # free agents a team would claim into an open roster spot
    depth_targets: dict    # players per position a team typically rosters
    num_teams: int
    fc_ppr: float
    scoring_changes: dict  # setting -> (this league, FantasyCalc's assumption) where they differ

    @property
    def deadline_passed(self):
        if self.deadline_date:
            return date.today() > self.deadline_date
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
    """Score projected stats with the given settings; fall back to Sleeper's own total."""
    hits = [stats[k] * v for k, v in scoring.items() if k in stats and v]
    if hits:
        return sum(hits)
    rec = scoring.get("rec", 0)
    return stats.get("pts_ppr" if rec >= 1 else "pts_half_ppr" if rec > 0 else "pts_std", 0.0)


def _scoring_factor(league_pts, fc_pts):
    """How much more (or less) a player scores under this league's rules than under
    FantasyCalc's, capped so a thin projection can't swing his value wildly."""
    if fc_pts < FACTOR_MIN_PTS or league_pts <= 0:
        return 1.0
    lo, hi = FACTOR_LIMITS
    return min(max(league_pts / fc_pts, lo), hi)


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
    # Sleeper supplies the NFL calendar, player database and projections for every platform.
    api = SleeperClient(cfg, refresh=refresh)
    state, db = api.state(), api.players()
    week = state["week"] if state.get("season_type") == "regular" else 0
    if cfg["platform"] == "espn":
        src = espn_source(api, cfg, week, db)
    else:
        src = sleeper_source(api, cfg, week)

    scoring = {**src.scoring, **cfg["scoring"]}
    roster_positions = src.roster_positions
    num_teams = len(src.rosters)
    season = src.season
    final_week = cfg["final_week"]
    fc_ppr = min(FC_PPR, key=lambda x: abs(x - scoring.get("rec", 0)))
    fc_scoring = {**scoring, **FC_SCORING, "rec": fc_ppr}
    scoring_changes = {k: (scoring.get(k, 0), v) for k, v in fc_scoring.items() if scoring.get(k, 0) != v}

    # --- projections ---
    first_week = max(week, 1)
    ros_weeks = list(range(first_week, final_week + 1))
    short_weeks = ros_weeks[: cfg["short_term_weeks"]]
    week_pts, short_pts, ros_pts, ros_fc_pts, byes = {}, {}, {}, {}, {}
    all_teams = {p.get("team") for p in db.values() if p.get("team") and p.get("position") in SKILL_POSITIONS}
    for wk in ros_weeks:
        rows = api.projections(season, wk)
        # Sleeper lists bye-week teams too, just without an opponent.
        playing = {r.get("team") for r in rows if r.get("team") and r.get("opponent")}
        for t in all_teams - playing:
            byes.setdefault(t, []).append(wk)
        for r in rows:
            pid, stats = r["player_id"], r.get("stats") or {}
            pts = _points(stats, scoring)
            if wk == first_week:
                week_pts[pid] = pts
            ros_pts[pid] = ros_pts.get(pid, 0.0) + pts
            ros_fc_pts[pid] = ros_fc_pts.get(pid, 0.0) + _points(stats, fc_scoring)
            if wk in short_weeks:
                short_pts[pid] = short_pts.get(pid, 0.0) + pts

    pos_of = {pid: p.get("position") for pid, p in db.items() if p.get("position") in SKILL_POSITIONS}

    # --- market values ---
    num_qbs = 2 if "SUPER_FLEX" in roster_positions else 1
    fc = api.fantasycalc_values(num_teams, num_qbs, fc_ppr)
    fc_values = {str(row["player"]["sleeperId"]): float(row["value"])
                 for row in fc if row["player"].get("sleeperId")}
    curve = sorted(fc_values.values(), reverse=True)

    def vor(points):
        pts = {pid: v for pid, v in points.items() if pid in pos_of}
        repl = _replacement_levels(pts, pos_of, roster_positions, num_teams)
        return {pid: v - repl[pos_of[pid]] for pid, v in pts.items() if v > 0}

    proj_values = _rank_to_curve(vor(short_pts), curve)
    ros_values = _rank_to_curve(vor(ros_pts), curve)
    floor = curve[-1] if curve else 0.0

    name_index = {}
    for pid, p in db.items():
        if pid in pos_of:
            name_index.setdefault(_name(p).lower(), pid)
    overrides = _load_overrides(cfg["overrides_file"], name_index)

    players = {}
    for pid in set(pos_of) & (set(fc_values) | set(ros_values) | set(overrides)
                              | {x for r in src.rosters for x in r["players"]}):
        p = db[pid]
        if pid in overrides or pid in fc_values:
            market = overrides.get(pid, fc_values.get(pid))
            factor = _scoring_factor(ros_pts.get(pid, 0.0), ros_fc_pts.get(pid, 0.0))
        else:  # not on FantasyCalc's list: projections (already league scoring), capped below its floor
            market, factor = min(ros_values.get(pid, 0.0), floor), 1.0
        team = p.get("team") or "FA"
        players[pid] = Player(
            pid=pid, name=_name(p), pos=p["position"], nfl_team=team,
            injury=p.get("injury_status") or "", rookie=p.get("years_exp") == 0,
            week_pts=week_pts.get(pid, 0.0), short_pts=short_pts.get(pid, 0.0),
            proj_value=proj_values.get(pid, 0.0), market_value=market, scoring_factor=factor,
            value=market * factor,
            byes=[w for w in byes.get(team, []) if w in short_weeks],
        )
    for p in sorted(players.values(), key=lambda p: p.value, reverse=True)[: cfg["star_count"]]:
        p.star = True

    # --- teams ---
    teams = []
    for r in src.rosters:
        reserve = r["reserve"]
        skill = [players[pid] for pid in r["players"] if pid in players]
        # K/DEF and other non-skill players still take active roster spots.
        other_active = [pid for pid in r["players"] if pid not in players and pid not in reserve]
        team = Team(
            roster_id=r["roster_id"], name=r["name"], username=r["username"],
            wins=r["wins"], losses=r["losses"],
            players=skill, capacity=len(roster_positions) - len(other_active),
            reserve=reserve & {p.pid for p in skill}, ir_slots=src.ir_slots,
        )
        for p in skill:
            p.owner = team
        teams.append(team)
    me = next(t for t in teams if t.roster_id == src.my_roster_id)

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
    position_limits = {**src.position_limits, **(cfg["position_limits"] or {})}
    for pos, limit in position_limits.items():
        depth_targets[pos] = min(depth_targets.get(pos, limit), limit)
    model = LineupModel(roster_positions, cfg["bench_weights"], waiver_pool, depth_targets,
                        cfg["surplus_weight"], cfg["te_flex_factor"], position_limits)

    return League(
        name=src.name, season=season, week=week, final_week=final_week,
        trade_deadline=src.trade_deadline, deadline_date=src.deadline_date, short_weeks=short_weeks, model=model,
        teams=teams, me=me, players=players, waiver_pool=waiver_pool, depth_targets=depth_targets,
        num_teams=num_teams, fc_ppr=fc_ppr, scoring_changes=scoring_changes,
    )


def _name(p):
    return p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
