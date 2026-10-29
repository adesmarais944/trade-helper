# Handoff: Sleeper Trade Helper

Context for picking up this project in a new chat or with a different model. Read this first,
then `README.md` (user-facing usage) and the code in `trade_helper/`.

## Goal

A tool that connects to the user's Sleeper fantasy football league through the public,
read-only Sleeper API. It suggests trade partners and specific trades that benefit **both**
teams, and can target or shop individual players and grade a proposed trade. It only
suggests trades. Offers are made by hand in the Sleeper app.

## User decisions (settled; don't revisit without asking)

| Topic | Decision |
|---|---|
| League type | **Redraft**, half PPR. No dynasty, keeper or contend/rebuild logic. |
| Language / output | Python, terminal output (`rich` tables). |
| Interface | Python CLI is the core. A thin Claude skill wraps it. |
| Scope | Several leagues (2026-10-28): config has a `leagues:` map, `league:` picks the default, `--league NAME` overrides. Only `platform: sleeper` works; the user plans an ESPN league (full PPR, 4-pt passing TD) later. |
| Value model (2026-10-28, replaces the now/ROS blend) | **Value = FantasyCalc ROS value (or override) × per-player scoring factor.** The old blend (`w = week/17` of a 3-week projection value) over-penalized byes (Nico Collins −31% for one bye) and made values hard to explain. FC is real trade-market data, which also fits the assumption that opponents value players the way the tool does. |
| Scoring formats | PPR: fetch FC at the league's PPR (0/0.5/1). Everything else (6-pt pass TD, TE premium): per-player factor = ROS projected points under league scoring / under FC's assumed scoring, capped 0.75–1.35. Per player, not per position, so pocket passers gain more than rushing QBs. |
| Trade deadline | After the deadline the tool is useless, so trade commands refuse to run (`--force` overrides). |
| Untouchables | None. Elite players get a `*` tag instead (top 12 by value). |
| Trade shapes | Up to 3-for-3 allowed by default (`trade_shapes`). Results ranked top N by score, no per-shape quotas. |
| Uneven trades | The side receiving fewer players claims a free agent: the top projected free agent this week at the position it needs most (claim assumed to succeed, no discount). The side receiving more cuts whoever costs it least. |
| Positional scarcity | RB/WR depth matters more (2+ starters); TE rarely plays FLEX; a backup QB has value because this league rosters about 2 QBs per team even though it starts 1. Modeled with league-derived depth targets, not flat position multipliers. |
| Trade goal | **Balanced**: "your gain" and "their gain" should be as even as possible, and each side should fill its needs from the other's strengths. Suggestions that heavily favor the user are *not* the goal. |
| Ranking | Rank by the smaller of the two gains (maximin). `partners`/`shop` show win-win only. Need-fit is a ranking bonus plus a display, not a hard requirement. Value-gap tolerance 10%. |
| Value sources | The user suggested peakedinhighskool's half-PPR trade chart and asked for additional sources to build a fuller value set. |
| Byes / injuries (revised 2026-10-28) | **Display only**: tags next to the player, no effect on value. FC already prices injuries over the rest of the season. The earlier week weights, status factors and Questionable timing rule were removed. |
| Below-replacement values | Taper gradually instead of dropping to 0 (user request). Still used for the projection fallback and the `^`/`!` flags. |
| Lineup impact of a trade this week | Deferred. Idea: `evaluate`/suggestions show each side's this-week lineup change (e.g. "−14 pts wk 8, McCaffrey on bye"), as a warning only. User said "not in scope right now". |

## League facts (as of 2026-10-22, NFL week 7)

- League "Chuckle Nuts", ID `1389329636416061440`, 12 teams, season 2026.
- User: Sleeper display name `adamDeez`, team name "VladimirTuten", record 6-1 as of week 8
  (2026-10-28).
- Lineup: QB, RB, RB, WR, WR, TE, FLEX, K, DEF, 6 bench slots, 1 IR slot.
- Scoring: `rec` 0.5, `pass_td` **6**, no TE premium.
- `settings.trade_deadline` = 11 (Sleeper week number). The user remembered it as about Nov 11.
- Playoffs start week 15. `final_week` is set to 17 in the config.

## Data sources

| Source | Endpoint | Use | Notes |
|---|---|---|---|
| Sleeper | `api.sleeper.app/v1/league/{id}`, `/users`, `/rosters`, `/state/nfl` | League, rosters, current week | No auth needed. Cached 15 min. |
| Sleeper | `api.sleeper.app/v1/players/nfl` | Player database (~5 MB) | Sleeper asks for at most 1 fetch/day. Cached 24 h. |
| Sleeper (undocumented) | `api.sleeper.app/projections/nfl/{season}/{week}?season_type=regular&position[]=QB...` | Weekly stat projections | Bye-week teams still get rows, but with `opponent: None` (fixed 2026-10-28; the old "team has no rows" check found no byes at all). OUT players project 0 for the current week, IR players 0 for every week. Each row's `date` is the game date. Scored with league scoring (and with FC's assumed scoring, for the factor); falls back to `pts_std`/`pts_half_ppr`/`pts_ppr` by PPR. League-independent, so usable for ESPN leagues too. |
| FantasyCalc | `api.fantasycalc.com/values/current?isDynasty=false&numQbs=1&numTeams=12&ppr=0.5` | Rest-of-season market value | Keyed by `sleeperId`. About 196 players. `ppr` accepts 0, 0.5, 1; values barely change between them for most players. |
| peakedinhighskool | Website chart | Not used automatically | The chart is **images**, so it can't be parsed. The user can type values into `overrides.csv` by hand. |

KeepTradeCut was considered and rejected: it has no API, and scraping breaks easily.

Sleeper's player database has no injury history. `practice_participation`,
`practice_description` and `injury_start_date` exist but are almost never filled (1, 1 and 0
players as of 2026-10-28).

## Architecture

```
config.yaml            leagues (platform, id, username, scoring) + global settings
overrides.csv          optional manual ROS values: player,value
trade_helper/
  config.py            loads YAML + defaults, merges globals with the chosen league's entry
                       (an old single-league file still works); resolves relative paths
  sleeper.py           Cache (JSON on disk in .cache/, stale fallback if offline) + SleeperClient
  league.py            load_league(): fetch, value players, build Team/Player/League objects
  lineup.py            LineupModel: best legal lineup + position-aware depth, waiver claims/cuts
  engine.py            TradeEngine: package enumeration, fairness, gains, classify, partners
  cli.py               argparse commands + rich output
.claude/skills/trade-helper/SKILL.md   skill that runs the CLI and explains the results
```

Run with `python -m trade_helper <command>`. Commands: `partners`, `target`, `shop`, `evaluate`,
`report`, `values`. Global flags: `--league`, `--refresh`, `--force`, `--config`. Python 3.13 on Windows.

**Adding a platform (ESPN):** `load_league()` reads Sleeper directly. An ESPN adapter needs to
supply rosters, users, roster positions, current week, trade deadline and a scoring dict in
Sleeper stat names (config `scoring` is merged on top). FantasyCalc and Sleeper projections
are keyed by Sleeper player id, so ESPN players must be mapped to Sleeper ids (by name +
position + team). Then add the platform to `config.PLATFORMS`.
Dependencies are in `requirements.txt` and already installed.

## Value model (`league.py`)

All values are on the FantasyCalc scale (top player about 10,000).

1. **Scoring**: league `scoring_settings` with config `scoring` merged on top. `fc_ppr` is the
   league's `rec` rounded to 0 / 0.5 / 1. `fc_scoring` = league scoring with `FC_SCORING`
   (`pass_td` 4, `bonus_rec_te` 0) and `rec = fc_ppr`. `League.scoring_changes` lists the
   differences and the header prints them.
2. **market_value**: the override from `overrides.csv`, else the FantasyCalc value (fetched at
   `fc_ppr`), else (not on FC's list) a rest-of-season projection value: value over
   replacement, mapped onto FC's value curve by rank (`_rank_to_curve`, tapering below
   replacement), capped at FC's lowest value.
3. **scoring_factor** (`_scoring_factor`): ROS projected points under league scoring /
   under `fc_scoring`, capped to `FACTOR_LIMITS` (0.75–1.35); 1.0 under `FACTOR_MIN_PTS`
   (20) projected points, and for projection-fallback players (already in league scoring).
4. **value** = `market_value * scoring_factor`.
5. Tags: `*` top `star_count` by value. `proj_value` = the next `short_term_weeks` (3) of
   projected points (byes count 0), mapped to the FC curve like the fallback. `^` when
   value ≥ 1.35 × proj_value, `!` when proj_value ≥ 1.35 × value; both need ≥ 1500. `R`
   marks a rookie. Injury status and byes in that window are shown too. None of this
   affects value.

`week_pts` (this week's projection, league scoring) still picks waiver claims.

K and DEF are excluded from valuation and trades. They still take roster spots.

## Trade model (`lineup.py`, `engine.py`)

- **Team score** = sum of the best legal starters plus position-aware depth.
  - Starters: dedicated slots first (best at each position), then flex. In FLEX a TE is
    compared at `value * te_flex_factor` (0.7), so he only flexes if clearly better than the
    best RB/WR.
  - Depth: `depth_targets` default to the league's average rostered count per position,
    rounded (QB 2, RB 5, WR 5, TE 2 as of week 7), never below starting slots; the config can
    override them. On the bench, the k-th backup at a position earns `bench_weights[k]`
    ([.30, .20, .15, .10]) while starters + backups are under the target. After that, players
    earn `surplus_weight` (0.05).
- **Roster settling** (`LineupModel.settle`): active capacity = lineup slots
  (`len(roster_positions)`) minus K/DEF. IR players (`roster.reserve`) don't count. An
  incoming IR player stays on IR if the receiver has a free IR slot.
  - Over capacity: repeatedly cut the active player whose removal costs the least score.
  - Under capacity: claim free agents, one per open spot, from the `waiver_pool`. The pool
    holds the top `waiver_candidates` (5) per position by this week's projection, excluding
    players who are IR, Out, PUP, Sus, NA or Doubtful. For each spot, the candidates are the
    *highest-projected* untaken free agent at each position. The team claims whichever one
    raises its score most, so the position is chosen by need and the player by projection.
  - The baseline (no trade) is settled the same way, so only claims the trade *causes* count
    toward gain. `Trade.my_claims`/`my_cuts`/`their_claims`/`their_cuts` hold the moves; the
    CLI shows them in the "Roster moves" column.
- **Gain** = team score after the trade minus the score before, for each side.
- **Package value** = values sorted from highest, multiplied by `package_weights`
  [1, .8, .65, .5]. This is the consolidation discount.
- **Fairness filter**: the two package values must be within `fairness_tolerance` (10%).
- **classify()**:
  - `win-win`: both gains ≥ `min_gain` (50).
  - `hurts you`: my gain < 0.
  - `fair`: the partner gets ≥ package value and loses ≤ `max_partner_loss` (150) in lineup value.
  - Anything else is `tough sell`.
- **Needs**: `needs()` marks a position weak when a team's dedicated starters there are below
  90% of the league average (strong above 110%). `_fills()` records the weak positions where
  an incoming player ends up in the receiver's starting lineup (`Trade.my_fills`/`their_fills`).
- **Score** = `min(my_gain, their_gain) * (1 + need_bonus * sides_that_fill_a_need)`, with
  `need_bonus` 0.25. Sort order (`sort_key`): type, then score, then total gain, then smallest
  absolute value gap.
- `partners` and `shop` show only win-win trades (`search(win_win_only=True)`). `target` shows
  every trade that improves the user's lineup, labeled by type.
- `_has_passenger()` drops a trade when removing one player leaves a trade that is still
  fair, still qualifies, and scores within `min_gain` of the original (a pointless throw-in).
  A throw-in that's needed to balance value is kept.
- `distinct()` (used when picking the top N per partner) skips trades that are a picked
  trade plus throw-ins, or that differ from one by one added, removed or swapped player.
- Search covers every shape in `trade_shapes` ("give-get" strings, default all of 1..3 ×
  1..3), with pieces of value ≥ `min_trade_value` (500). An older config with only
  `max_players_per_side` still works. A full `partners` run takes about 7 seconds (was 4;
  the below-replacement taper raised rostered players worth ≥ 500 from 104 to 120). If
  low-end players crowd the suggestions, raise `min_trade_value` to 600–700.

**Key insight:** this scoring is linear, so swapping two starters of equal value is
zero-sum. Win-win only happens when a player moves from one team's bench into the other's
starting lineup, or through consolidation plus a waiver claim. The first version (1-for-1 to
2-for-2, no waiver model) found just 1 win-win partner, so a "fair" tier was added. Once
uneven shapes and waiver claims existed, win-win trades became plentiful. Then the user
tested as `thisisjen` and found results heavily favoring the user (old score
`my_gain + 0.5 * their_gain`, e.g. +1,393 / +334). That led to the balanced ranking and to
win-win only in `partners`/`shop`.

## Positional depth results (2026-10-22)

Problems the user found in testing:
- A top suggestion sent 2 TEs to one team.
- T.J. Hockenson (highest-*value* free agent, 711) was the waiver claim in nearly every trade.

The cause was that bench depth was weighted by rank only, regardless of position. Any team
gained from any extra good player and from the best-value free agent.

After the depth model and the projection-based claims:
- No suggested trade moves more than one TE (checked for both `thisisjen` and `adamDeez`).
- Claims vary by need: Bo Nix (QB) for teams carrying 1 QB, Higbee (TE, top TE projection
  7.8 vs Hockenson's 7.6), Allgeier (RB).
- Shapes are mostly 3-for-3 and 2-for-3.
- `adamDeez` now gets 2 win-win partners (down from ~6), because surplus depth no longer
  counts as value.

## Balanced ranking results (2026-10-22)

As `thisisjen` (Friday Night Lights, 0-6, weak at every position), the top trade went from
+1,393 / +334 to +711 / +700 with a 2% value gap (Fannin + Loveland + London for Jacobs +
Waddle + Parker Washington; fills RB and WR for her, TE and WR for Franks). As `adamDeez`, top
trades are also within about 20% of each other (e.g. +207 / +221). The user sometimes sets
`my_username: "thisisjen"` in `config.yaml` for testing; their own team is `adamDeez`
(set back as of 2026-10-28).

## Simplified value model results (2026-10-28, week 8)

- Nico Collins (bye wk 8): 2,722 under the blend → 3,956 (his FC value), tagged `^`.
- QB scoring factors at 6-pt passing TD: Goff ×1.22, Burrow ×1.20, Purdy ×1.21, Allen ×1.12,
  Daniels ×1.11 (rushing QBs gain less). Non-QBs ×1.00.
- With a test league override `scoring: {rec: 1, pass_td: 4}`, FC is fetched at PPR 1 and
  every factor is ×1.00.
- `partners` still about 7.5 s; top trades stay balanced (e.g. +403 / +460, +425 / +394).
  `adamDeez` now shows as weak at QB and WR.

## Bye / injury results (2026-10-28, week 8; superseded by the simplified model above)

Value before → after the week weights and status factors:
- Christian McCaffrey (bye wk 8): 6691 → 5620
- Mike Evans (Out): 2492 → 2127
- Malik Nabers (bye wk 8): 3792 → 3226, then 3546 after the below-replacement taper
  (his short value went from 0 to 680)

Questionable players (Gibbs, Chase, St. Brown) were unchanged on Wednesday because their
Sunday games' final report was still to come. The taper changed 59 of 159 rostered values,
all players below replacement (e.g. Jordan Love 495 → 988, Kittle 1497 → 1902). Values above
replacement and the top-12 stars didn't move.

## Verified (2026-10-22)

- Every command ran against the live league. Cyrillic team names print correctly because
  stdout is forced to UTF-8.
- Custom scoring is confirmed: Goff's week 8 projection is 19.0 at 6-point passing TDs,
  against Sleeper's 16.4 half-PPR default.
- The deadline cutoff was confirmed by patching the deadline to week 6.
- Sample results:
  - Win-win with "Franks little beauties": give Garrett Wilson + Carnell Tate, get Chris
    Olave + Michael Wilson.
  - Win-win with "ChatGttLookinAhh": give Bhayshul Tuten + Zach Charbonnet, get Sam
    LaPorta + Malik Washington.
  - The user's weakest position is WR.
- No automated tests exist yet.

## Known limitations

- **Byes / injuries:** don't affect value at all; a player Out for the next 2 weeks keeps his
  FC value (FC usually lowers it). The `^` flag and the injury tag are the only signal.
- **Trade-week impact:** a trade's effect on either team's lineup *this week* (byes, Out
  players) isn't shown. This is deferred, see User decisions.
- **Scoring factor:** treats value as proportional to points, though value really tracks
  points over replacement. Only `pass_td` and `bonus_rec_te` are assumed to differ from FC;
  other unusual settings (e.g. −2 per interception) are treated as matching FC.
- **Platforms:** only Sleeper. ESPN is a config placeholder (see Architecture).
- **Lineup rules:** IR placement follows Sleeper's current `reserve` list. There's no check
  that an incoming injured player is actually IR-eligible.
- **Waivers:** claims are assumed to succeed (waiver priority and FAAB are ignored). Each
  side's claims are computed separately, so in rare cases both sides of one trade could
  "claim" the same free agent.
- **Player models:** opponent managers are assumed to value players the same way the tool
  does. Strength of schedule and playoff weeks (15–17) are not weighted.
- **Player names:** lookup uses exact match, then substring, then fuzzy matching. A name
  that matches several players (e.g. "chase") stops with a list of the matches.
- **Projections:** players outside FantasyCalc's list get small values derived from
  projections. A player with no projection and no FantasyCalc entry is worth 0.
- **Rank mapping:** mapping onto the FantasyCalc value curve by rank keeps the order but
  throws away how far apart players actually are in projected points.

## Possible next steps (none requested yet)

- This-week lineup impact warning in `evaluate` and suggestions (discussed, deferred).
- ESPN platform adapter (user has an ESPN league: full PPR, 4-pt passing TD).
- Weight playoff schedule (weeks 15–17) more heavily as the season goes on.
- Add more value sources, such as another public rest-of-season ranking, averaged with
  FantasyCalc.
- Add an HTML report.
- Add unit tests for `LineupModel`, `classify`, `_replacement_levels`, `_scoring_factor`,
  `load_config` (league selection, old format).
- First git commit (repo is initialized on `main`, nothing committed; the user hasn't asked).
