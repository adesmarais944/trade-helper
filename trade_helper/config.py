from pathlib import Path

import yaml

PLATFORMS = ("sleeper", "espn")

DEFAULTS = {
    "platform": "sleeper",
    "league_id": None,
    "my_username": None,    # Sleeper: your username or display name
    "team_id": None,        # ESPN: the teamId in your team page's URL
    "season": None,         # ESPN: season year (default: the current NFL season)
    "secrets_file": "secrets.yaml",   # git-ignored; ESPN cookies
    # Scoring overrides in Sleeper stat names (pass_td, rec, bonus_rec_te, ...). Sleeper
    # leagues read their scoring from the API; these replace individual settings.
    "scoring": {},
    "final_week": 17,
    "short_term_weeks": 3,
    "fairness_tolerance": 0.10,
    # give-get shapes from your side, e.g. "2-1" = you give 2, get 1.
    "trade_shapes": [f"{a}-{b}" for a in (1, 2, 3) for b in (1, 2, 3)],
    "waiver_candidates": 5,
    "min_trade_value": 500,
    "min_gain": 50,
    "max_partner_loss": 150,
    "need_bonus": 0.25,
    "trades_per_partner": 3,
    "star_count": 12,
    "bench_weights": [0.30, 0.20, 0.15, 0.10],
    "surplus_weight": 0.05,
    "depth_targets": None,
    # Max players per position, e.g. {RB: 5}. Read from ESPN/Sleeper; entries here override.
    "position_limits": None,
    "te_flex_factor": 0.7,
    "package_weights": [1.0, 0.80, 0.65, 0.50],
    "overrides_file": "overrides.csv",
    "cache_dir": ".cache",
    "cache_ttl_hours": {"players": 24, "values": 6, "projections": 6, "league": 0.25},
}


def _pick_league(leagues, wanted, path):
    """Exact name, else a unique case-insensitive prefix, else the only league on that
    platform (so `--league espn` works)."""
    if wanted in leagues:
        return wanted
    key = str(wanted).lower()
    for match in ([n for n in leagues if n.lower().startswith(key)],
                  [n for n, e in leagues.items() if str((e or {}).get("platform", "sleeper")).lower() == key]):
        if len(match) == 1:
            return match[0]
    raise SystemExit(f"{path}: no single league matches {wanted!r}. Leagues: {', '.join(leagues) or '-'}")


def load_config(path, league=None):
    """Load settings for one league: global settings, then that league's entry on top."""
    path = Path(path).resolve()
    with open(path, encoding="utf-8") as f:
        user = yaml.safe_load(f) or {}
    leagues = user.pop("leagues", None)
    default = user.pop("league", None)
    if leagues is None:  # older config files: one league at the top level
        leagues = {"default": {k: user.pop(k) for k in ("platform", "league_id", "my_username", "scoring") if k in user}}
    name = _pick_league(leagues, league or default or next(iter(leagues), None), path)
    entry = leagues[name] or {}

    cfg = {**DEFAULTS, **user, **entry, "league_name": name}
    if "trade_shapes" not in user and "trade_shapes" not in entry and "max_players_per_side" in cfg:
        n = int(cfg["max_players_per_side"])
        cfg["trade_shapes"] = [f"{a}-{b}" for a in range(1, n + 1) for b in range(1, n + 1)]
    try:
        cfg["trade_shapes"] = sorted({tuple(int(x) for x in str(s).split("-")) for s in cfg["trade_shapes"]})
    except ValueError:
        raise SystemExit(f"{path}: trade_shapes entries must look like \"2-1\" (give-get)")
    cfg["cache_ttl_hours"] = {**DEFAULTS["cache_ttl_hours"], **(user.get("cache_ttl_hours") or {}),
                              **(entry.get("cache_ttl_hours") or {})}
    cfg["scoring"] = {**(user.get("scoring") or {}), **(entry.get("scoring") or {})}
    if cfg["platform"] not in PLATFORMS:
        raise SystemExit(f"{path}: league {name!r}: platform {cfg['platform']!r} isn't supported yet "
                         f"(supported: {', '.join(PLATFORMS)})")
    me_key = "team_id" if cfg["platform"] == "espn" else "my_username"
    if not cfg["league_id"] or not cfg[me_key]:
        raise SystemExit(f"{path}: league {name!r} needs league_id and {me_key}")
    cfg["league_id"] = str(cfg["league_id"])
    # Relative paths resolve against the config file's folder.
    base = path.parent
    cfg["cache_dir"] = base / cfg["cache_dir"]
    cfg["overrides_file"] = base / cfg["overrides_file"] if cfg["overrides_file"] else None
    secrets = base / cfg["secrets_file"] if cfg["secrets_file"] else None
    cfg["secrets"] = {}
    if secrets and secrets.exists():
        with open(secrets, encoding="utf-8") as f:
            cfg["secrets"] = yaml.safe_load(f) or {}
    return cfg
