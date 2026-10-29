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

- **Value** blends "Now" (next few weeks of projections) and "ROS" (FantasyCalc
  rest-of-season). The "now" weight is current week / 17, shown in the header.
- **Your gain / Their gain**: change in each team's starting-lineup-plus-depth value.
- **Value gap**: trade-chart difference; + means the user receives more.
- **Type**: `partners`/`shop` only show win-win trades, ranked so both gains are as even
  as possible. `target`/`evaluate` can also show fair, tough sell or hurts you. Lines like
  "you fill RB / they fill WR" mean an incoming player starts at that team's weak position.
- The goal is trades that help both sides about equally. Don't push the user toward the
  most lopsided option.
- **Roster moves**: in uneven trades, the side receiving fewer players claims the top
  projected free agent at its neediest position, and the side receiving more cuts whoever it
  can spare. Mention these, and note that a claim depends on waiver priority.
- Depth is position-aware: a backup QB has value (this league rosters about 2), RB/WR depth
  matters most, and a 3rd TE is nearly worthless.
- Values come from FantasyCalc (rest of season), Sleeper/RotoWire projections (next 3 weeks)
  and the user's `overrides.csv`.
- Tags: `*` star, `^` upside (ROS > current), `!` win-now (current > ROS), `R` rookie,
  plus injury status and upcoming byes.

## Presenting results

For each suggested trade, explain in a sentence or two *why* it fits: which positional need
it fills for each side (see the "weak / strong" summary per team), and any byes, injuries
or upside flags. Mention the trade deadline countdown from the header if it's close. If
nothing qualifies, suggest raising `fairness_tolerance`, lowering `min_gain`, or adding
shapes to `trade_shapes` in `config.yaml`.
