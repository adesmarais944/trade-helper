---
name: trade-helper
description: Find and evaluate fantasy football trades for the user's Sleeper league. Use when the user asks for trade ideas, trade partners, what it would take to get a player, who wants one of their players, whether a trade is good, or how teams in their league stack up.
---

# Sleeper trade helper

The project root contains a Python CLI that does all the analysis. Run it from the project
root and base your answer on its output. Don't invent player values.

| User wants | Command |
|---|---|
| Trade ideas / best partners | `python -m trade_helper partners` (`--teams N`, `--top N`) |
| Acquire a specific player | `python -m trade_helper target "<name>"` |
| Move one of their players | `python -m trade_helper shop "<name>"` |
| Judge a specific deal | `python -m trade_helper evaluate --give "A, B" --get "C"` |
| League overview / team needs | `python -m trade_helper report` |
| Player values | `python -m trade_helper values` (`--mine`, `--pos RB`, `--team NAME`) |

Set `COLUMNS=180` in the environment so tables don't wrap.

## Reading the output

- **Value** is the FantasyCalc rest-of-season market value (or the user's override),
  times a scoring factor for rules FantasyCalc doesn't assume (e.g. 6-pt passing TDs; the
  header lists them). Byes and injuries are shown but don't change value.
- The config can hold several leagues. Add `--league NAME` when the user names one.
- **Your gain / Their gain**: change in each team's starting-lineup-plus-depth value.
- **Value gap**: trade-chart difference; + means the user receives more.
- **Type**: `partners`/`shop` only show win-win trades, ranked so both gains are as even
  as possible. `target`/`evaluate` can also show fair, tough sell or hurts you. Lines like
  "you fill RB / they fill WR" mean an incoming player starts at that team's weak position.
- The goal is trades that help both sides about equally. Don't push the user toward the
  most lopsided option.
- **Roster moves**: in uneven trades, the side receiving more players cuts whoever it can
  spare, and the side receiving fewer gets an open spot. Open spots add nothing to the gains;
  the "best FA" next to one is only a suggestion.
- Depth is position-aware: a backup QB has value (this league rosters about 2), RB/WR depth
  matters most, and a 3rd TE is nearly worthless.
- Values come from FantasyCalc (rest of season) and the user's `overrides.csv`, adjusted by
  Sleeper/RotoWire projections scored with league rules.
- Tags: `*` star, `^` value above the next 3 weeks' projections (buy-low candidate), `!`
  projected above value (sell-high candidate), `R` rookie, plus injury status and upcoming
  byes. Tags are labels only.

## Presenting results

For each suggested trade, explain in a sentence or two *why* it fits: which positional need
it fills for each side (see the "weak / strong" summary per team), and any byes, injuries
or upside flags. Mention the trade deadline countdown from the header if it's close. If
nothing qualifies, suggest raising `fairness_tolerance`, lowering `min_gain`, or adding
shapes to `trade_shapes` in `config.yaml`.
