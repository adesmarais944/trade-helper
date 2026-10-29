"""Trade search: enumerate packages, keep fair ones, score lineup gain for both sides."""
from dataclasses import dataclass, field
from itertools import combinations

from .sleeper import SKILL_POSITIONS

KIND_RANK = {"win-win": 0, "fair": 1, "tough sell": 2, "hurts you": 3}
WEAK, STRONG = 0.9, 1.1    # starter strength vs league average that marks a need / surplus


@dataclass
class Trade:
    partner: object
    give: tuple
    get: tuple
    give_value: float
    get_value: float
    my_gain: float
    their_gain: float
    score: float
    kind: str = ""         # "win-win", "fair", "tough sell" or "hurts you"
    # Roster moves the trade forces (beyond what each team would do anyway).
    my_claims: list = field(default_factory=list)
    my_cuts: list = field(default_factory=list)
    their_claims: list = field(default_factory=list)
    their_cuts: list = field(default_factory=list)
    # Weak positions each side fills with an incoming player who starts for them.
    my_fills: set = field(default_factory=set)
    their_fills: set = field(default_factory=set)

    @property
    def win_win(self):
        return self.kind == "win-win"

    @property
    def fairness(self):
        """Signed gap in package value from my side: + means I receive more."""
        return (self.get_value - self.give_value) / max(self.get_value, self.give_value, 1)

    @property
    def total_gain(self):
        return self.my_gain + self.their_gain

    def pids(self):
        return {p.pid for p in self.give}, {p.pid for p in self.get}


class TradeEngine:
    def __init__(self, league, cfg):
        self.league = league
        self.model = league.model
        self.me = league.me
        self.tol = cfg["fairness_tolerance"]
        self.shapes = cfg["trade_shapes"]
        self.min_value = cfg["min_trade_value"]
        self.min_gain = cfg["min_gain"]
        self.max_partner_loss = cfg["max_partner_loss"]
        self.need_bonus = cfg["need_bonus"]
        self.package_weights = cfg["package_weights"]
        strength = position_strength(league)
        self.weak = {t.roster_id: needs(strength, t)[0] for t in league.teams}
        self.base, self.base_claims = {}, {}
        for t in league.teams:
            score, claims, _, _ = self.model.settle(t.players, t.capacity, t.reserve)
            self.base[t.roster_id] = score
            self.base_claims[t.roster_id] = {p.pid for p in claims}

    def package_value(self, players):
        ranked = sorted((p.value for p in players), reverse=True)
        weights = self.package_weights
        return sum(v * (weights[i] if i < len(weights) else weights[-1]) for i, v in enumerate(ranked))

    def _after(self, team, outgoing, incoming, sender):
        """Settle `team`'s roster after the swap. Incoming IR players stay on IR if a slot is free."""
        out_ids = {p.pid for p in outgoing}
        reserve = team.reserve - out_ids
        for p in incoming:
            if p.pid in sender.reserve and len(reserve) < team.ir_slots:
                reserve = reserve | {p.pid}
        roster = [p for p in team.players if p.pid not in out_ids] + list(incoming)
        score, claims, cuts, final = self.model.settle(roster, team.capacity, reserve)
        new_claims = [p for p in claims if p.pid not in self.base_claims[team.roster_id]]
        return score - self.base[team.roster_id], new_claims, cuts, final

    def _fills(self, team, incoming, roster):
        """Weak positions of `team` that an incoming player fills as a starter."""
        lineup, _ = self.model.starters(roster)
        starting = {p.pid for p in lineup}
        return {p.pos for p in incoming if p.pid in starting and p.pos in self.weak[team.roster_id]}

    def evaluate(self, partner, give, get):
        my_gain, my_claims, my_cuts, my_roster = self._after(self.me, give, get, partner)
        their_gain, their_claims, their_cuts, their_roster = self._after(partner, get, give, self.me)
        t = Trade(partner, tuple(give), tuple(get), self.package_value(give), self.package_value(get),
                  my_gain, their_gain, 0.0,
                  my_claims=my_claims, my_cuts=my_cuts, their_claims=their_claims, their_cuts=their_cuts)
        t.kind = self.classify(t)
        if my_gain >= self.min_gain:  # only worth computing for trades that can be shown
            t.my_fills = self._fills(self.me, get, my_roster)
            t.their_fills = self._fills(partner, give, their_roster)
        # Balanced ranking: the smaller of the two gains, boosted when the trade fills a weak
        # starting spot for one or both sides.
        sides_filled = bool(t.my_fills) + bool(t.their_fills)
        t.score = min(my_gain, their_gain) * (1 + self.need_bonus * sides_filled)
        return t

    def classify(self, t):
        """win-win: both lineups gain min_gain. fair: you gain, the partner wins on the trade
        chart and loses at most max_partner_loss lineup value. Otherwise a tough sell."""
        if t.my_gain >= self.min_gain and t.their_gain >= self.min_gain:
            return "win-win"
        if t.my_gain < 0:
            return "hurts you"
        if t.fairness <= 0 and t.their_gain >= -self.max_partner_loss:
            return "fair"
        return "tough sell"

    def _packages(self, team, sizes, must=()):
        """{size: [(players, package_value)]} for every allowed package size."""
        pool = [p for p in team.players if p.value >= self.min_value or p in must]
        out = {}
        for n in sizes:
            out[n] = [(combo, self.package_value(combo)) for combo in combinations(pool, n)
                      if all(m in combo for m in must)]
        return out

    def _passes(self, t, win_win_only):
        if t.my_gain < self.min_gain:
            return False
        return t.win_win or not win_win_only

    def _fair(self, gv, tv):
        return abs(tv - gv) <= self.tol * max(tv, gv)

    def _has_passenger(self, t, win_win_only, memo):
        """True if some player can be removed and the smaller trade still qualifies and scores
        within min_gain of this one, i.e. that player is a throw-in that doesn't matter."""
        for side in ("give", "get"):
            players = getattr(t, side)
            if len(players) < 2:
                continue
            for x in players:
                give = tuple(p for p in t.give if p != x) if side == "give" else t.give
                get = tuple(p for p in t.get if p != x) if side == "get" else t.get
                key = (frozenset(p.pid for p in give), frozenset(p.pid for p in get))
                if key not in memo:
                    memo[key] = (self.evaluate(t.partner, give, get)
                                 if self._fair(self.package_value(give), self.package_value(get)) else None)
                sub = memo[key]
                if sub and self._passes(sub, win_win_only) and sub.score >= t.score - self.min_gain:
                    return True
        return False

    def search(self, partner, must_give=(), must_get=(), win_win_only=True):
        """Fair-value trades with `partner` that improve your lineup, without pointless throw-ins.

        win_win_only=True keeps only trades where both lineups gain (see classify). Ranked by
        type, then by the smaller of the two gains, so balanced trades rise to the top.
        """
        mine = self._packages(self.me, {a for a, _ in self.shapes}, must_give)
        theirs = self._packages(partner, {b for _, b in self.shapes}, must_get)
        results = []
        for a, b in self.shapes:
            for give, gv in mine[a]:
                for get, tv in theirs[b]:
                    if not self._fair(gv, tv):
                        continue
                    t = self.evaluate(partner, give, get)
                    if self._passes(t, win_win_only):
                        results.append(t)
        memo = {}
        results = [t for t in results if not self._has_passenger(t, win_win_only, memo)]
        results.sort(key=sort_key)
        return results

    def partners(self, per_partner):
        """Best mutually beneficial trades per partner, partners ranked by their best trade."""
        ranked = []
        for team in self.league.teams:
            if team is self.me:
                continue
            trades = distinct(self.search(team), per_partner)
            if trades:
                ranked.append((team, trades))
        ranked.sort(key=lambda x: sort_key(x[1][0]))
        return ranked


def sort_key(t):
    """Type first, then balanced score, then total gain, then the smallest value gap."""
    return KIND_RANK[t.kind], -t.score, -t.total_gain, abs(t.fairness)


def needs(strength, team):
    """(weak positions, strong positions) for a team, from position_strength()."""
    s = strength[team.roster_id]
    weak = [pos for pos in SKILL_POSITIONS if s[pos]["ratio"] < WEAK]
    strong = [pos for pos in SKILL_POSITIONS if s[pos]["ratio"] > STRONG]
    return weak, strong


def distinct(trades, limit):
    """Keep the best of each trade idea: skip trades that differ from an already-picked one
    by adding, removing or swapping a single player (or that only add throw-ins)."""
    picked = []
    for t in trades:
        give, get = t.pids()
        if any((pg <= give and pt <= get) or len(give ^ pg) + len(get ^ pt) <= 2
               for pg, pt in (p.pids() for p in picked if p.partner is t.partner)):
            continue
        picked.append(t)
        if len(picked) == limit:
            break
    return picked


def position_strength(league):
    """Per team, per position: starter value vs league average (1.0 = average), and best bench piece."""
    model = league.model
    slots = {pos: model.dedicated.count(pos) for pos in SKILL_POSITIONS}
    starters = {}
    for t in league.teams:
        ranked = sorted(t.players, key=lambda p: p.value, reverse=True)
        starters[t.roster_id] = {pos: [p for p in ranked if p.pos == pos][: slots[pos]] for pos in SKILL_POSITIONS}
    avg = {pos: sum(sum(p.value for p in s[pos]) for s in starters.values()) / len(league.teams) or 1
           for pos in SKILL_POSITIONS}
    out = {}
    for t in league.teams:
        lineup, bench = model.starters(t.players)
        out[t.roster_id] = {
            pos: {
                "ratio": sum(p.value for p in starters[t.roster_id][pos]) / avg[pos],
                "spare": next((p for p in bench if p.pos == pos), None),
            }
            for pos in SKILL_POSITIONS
        }
    return out
