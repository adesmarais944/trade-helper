from pathlib import Path

import yaml

DEFAULTS = {
    "league_id": None,
    "my_username": None,
    "final_week": 17,
    "short_term_weeks": 3,
    "short_term_week_weights": [1.0, 0.7, 0.5],
    "status_factors": {"Questionable": 0.85, "Doubtful": 0.0, "Out": 0.0},
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
    "te_flex_factor": 0.7,
    "package_weights": [1.0, 0.80, 0.65, 0.50],
    "overrides_file": "overrides.csv",
    "cache_dir": ".cache",
    "cache_ttl_hours": {"players": 24, "values": 6, "projections": 6, "league": 0.25},
}


def load_config(path):
    path = Path(path).resolve()
    with open(path, encoding="utf-8") as f:
        user = yaml.safe_load(f) or {}
    cfg = {**DEFAULTS, **user}
    if "trade_shapes" not in user and "max_players_per_side" in user:  # older config files
        n = int(user["max_players_per_side"])
        cfg["trade_shapes"] = [f"{a}-{b}" for a in range(1, n + 1) for b in range(1, n + 1)]
    try:
        cfg["trade_shapes"] = sorted({tuple(int(x) for x in str(s).split("-")) for s in cfg["trade_shapes"]})
    except ValueError:
        raise SystemExit(f"{path}: trade_shapes entries must look like \"2-1\" (give-get)")
    cfg["cache_ttl_hours"] = {**DEFAULTS["cache_ttl_hours"], **(user.get("cache_ttl_hours") or {})}
    if not cfg["league_id"] or not cfg["my_username"]:
        raise SystemExit(f"{path}: league_id and my_username are required")
    cfg["league_id"] = str(cfg["league_id"])
    # Relative paths resolve against the config file's folder.
    base = path.parent
    cfg["cache_dir"] = base / cfg["cache_dir"]
    cfg["overrides_file"] = base / cfg["overrides_file"] if cfg["overrides_file"] else None
    return cfg
