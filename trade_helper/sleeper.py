"""Sleeper + FantasyCalc HTTP access with a simple on-disk JSON cache."""
import hashlib
import json
import time
from pathlib import Path

import requests

SLEEPER = "https://api.sleeper.app/v1"
SLEEPER_PROJ = "https://api.sleeper.app/projections/nfl"
FANTASYCALC = "https://api.fantasycalc.com/values/current"
SKILL_POSITIONS = ("QB", "RB", "WR", "TE")


class Cache:
    def __init__(self, root, refresh=False):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.refresh = refresh
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "trade-helper/1.0"

    def get_json(self, url, ttl_hours, params=None, name=None):
        key = name or hashlib.sha1((url + json.dumps(params, sort_keys=True)).encode()).hexdigest()[:16]
        path = self.root / f"{key}.json"
        fresh = path.exists() and time.time() - path.stat().st_mtime < ttl_hours * 3600
        if fresh and not self.refresh:
            return json.loads(path.read_text(encoding="utf-8"))
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError):
            if path.exists():  # fall back to stale cache when offline
                return json.loads(path.read_text(encoding="utf-8"))
            raise
        path.write_text(json.dumps(data), encoding="utf-8")
        return data


class SleeperClient:
    def __init__(self, cfg, refresh=False):
        self.cfg = cfg
        self.ttl = cfg["cache_ttl_hours"]
        self.cache = Cache(cfg["cache_dir"], refresh=refresh)
        self.league_id = cfg["league_id"]

    def _league(self, suffix=""):
        return self.cache.get_json(
            f"{SLEEPER}/league/{self.league_id}{suffix}", self.ttl["league"],
            name=f"league_{self.league_id}{suffix.replace('/', '_')}",
        )

    def league(self):
        return self._league()

    def users(self):
        return self._league("/users")

    def rosters(self):
        return self._league("/rosters")

    def state(self):
        return self.cache.get_json(f"{SLEEPER}/state/nfl", self.ttl["league"], name="state_nfl")

    def players(self):
        return self.cache.get_json(f"{SLEEPER}/players/nfl", self.ttl["players"], name="players_nfl")

    def projections(self, season, week):
        params = [("season_type", "regular")] + [("position[]", p) for p in SKILL_POSITIONS]
        return self.cache.get_json(
            f"{SLEEPER_PROJ}/{season}/{week}", self.ttl["projections"], params=params,
            name=f"proj_{season}_{week}",
        )

    def fantasycalc_values(self, num_teams, num_qbs, ppr):
        params = {"isDynasty": "false", "numQbs": num_qbs, "numTeams": num_teams, "ppr": ppr}
        return self.cache.get_json(
            FANTASYCALC, self.ttl["values"], params=params,
            name=f"fantasycalc_{num_teams}_{num_qbs}_{ppr}",
        )
