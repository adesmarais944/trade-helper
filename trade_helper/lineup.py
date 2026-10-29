"""Roster scoring: best legal starting lineup plus position-aware bench depth."""
from .sleeper import SKILL_POSITIONS

FLEX_ELIGIBLE = {
    "FLEX": {"RB", "WR", "TE"},
    "WRRB_FLEX": {"RB", "WR"},
    "REC_FLEX": {"WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
}


class LineupModel:
    def __init__(self, roster_positions, bench_weights, waiver_pool=(), depth_targets=None,
                 surplus_weight=0.05, te_flex_factor=1.0, position_limits=None):
        self.dedicated = [p for p in roster_positions if p in SKILL_POSITIONS]
        self.flex = [FLEX_ELIGIBLE[p] for p in roster_positions if p in FLEX_ELIGIBLE]
        self.bench_weights = bench_weights
        # Free agents a team could claim into an open roster spot (a hint only, never scored),
        # best projection first within each position.
        self.waiver_pool = sorted(waiver_pool, key=lambda p: p.week_pts, reverse=True)
        # How many players a team typically rosters at each position (starters + backups).
        # Backups up to this count earn bench_weights; anyone beyond it earns surplus_weight.
        self.depth_targets = depth_targets or {}
        self.surplus_weight = surplus_weight
        # A TE only takes a FLEX spot if value * te_flex_factor beats the best RB/WR option.
        self.te_flex_factor = te_flex_factor
        # Max players per position on the whole roster, IR included (e.g. ESPN's RB 5).
        self.position_limits = position_limits or {}

    def _flex_key(self, p, eligible):
        if p.pos == "TE" and eligible - {"TE"}:
            return p.value * self.te_flex_factor
        return p.value

    def starters(self, players):
        """Dedicated slots first (best at each position), then flex slots."""
        ranked = sorted(players, key=lambda p: p.value, reverse=True)
        used, lineup = set(), []
        for pos in self.dedicated:
            pick = next((p for p in ranked if p.pid not in used and p.pos == pos), None)
            if pick:
                used.add(pick.pid)
                lineup.append(pick)
        for eligible in self.flex:
            options = [p for p in ranked if p.pid not in used and p.pos in eligible]
            if options:
                pick = max(options, key=lambda p: self._flex_key(p, eligible))
                used.add(pick.pid)
                lineup.append(pick)
        bench = [p for p in ranked if p.pid not in used]
        return lineup, bench

    def score(self, players):
        """Starters at full value. Bench players fill depth slots at their own position:
        the k-th backup earns bench_weights[k] while the position is under its depth target,
        and surplus_weight after that (a 3rd TE is worth far less than a 3rd RB)."""
        lineup, bench = self.starters(players)
        total = sum(p.value for p in lineup)
        started = {}
        for p in lineup:
            started[p.pos] = started.get(p.pos, 0) + 1
        backups = {}
        for p in bench:  # already sorted best first
            k = backups.get(p.pos, 0)
            backups[p.pos] = k + 1
            open_depth = self.depth_targets.get(p.pos, 0) - started.get(p.pos, 0)
            w = self.bench_weights[k] if k < open_depth and k < len(self.bench_weights) else self.surplus_weight
            total += w * p.value
        return total

    def settle(self, players, capacity, reserve=frozenset()):
        """Fit a roster to its active capacity, then score it.

        Over a position limit: cut whichever player at that position costs the least score.
        Over capacity: cut whichever active player costs the least score (e.g. a surplus 3rd TE
        before a 5th RB). Under capacity: the spots stay open and add nothing. A free agent is
        there for the taking with or without a trade, so a trade doesn't get credit for one.
        Players whose pid is in `reserve` sit on IR and don't use an active spot.
        Returns (score, cuts, open_spots, final_roster); cuts are (player, reason) pairs.
        """
        roster = list(players)
        cuts = []
        for pos, limit in self.position_limits.items():
            while sum(1 for p in roster if p.pos == pos) > limit:
                at_pos = [p for p in roster if p.pos == pos]
                cut = max(at_pos, key=lambda c: self.score([p for p in roster if p.pid != c.pid]))
                roster = [p for p in roster if p.pid != cut.pid]
                cuts.append((cut, f"{pos} limit {limit}"))
        open_spots = capacity - sum(1 for p in roster if p.pid not in reserve)
        for _ in range(max(-open_spots, 0)):
            active = [p for p in roster if p.pid not in reserve]
            cut = max(active, key=lambda c: self.score([p for p in roster if p.pid != c.pid]))
            roster = [p for p in roster if p.pid != cut.pid]
            cuts.append((cut, "roster full"))
        return self.score(roster), cuts, max(open_spots, 0), roster

    def best_claim(self, roster):
        """Free agent who would help this roster most, shown as a hint for an open spot: the
        top-projected free agent at each position, picked by roster need."""
        taken = {p.pid for p in roster}
        candidates = {}
        for fa in self.waiver_pool:
            if fa.pid not in taken and fa.pos not in candidates:
                candidates[fa.pos] = fa
        base = self.score(roster)
        best = max(candidates.values(), key=lambda fa: self.score(roster + [fa]), default=None)
        return best if best and self.score(roster + [best]) > base else None
