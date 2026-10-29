# Sleeper Trade Helper

Suggests trades for your team in a Sleeper redraft league: who to target, what to offer, and
whether a deal helps both lineups.

## Setup

Requires Python 3.10 or newer. Check with `python --version`.

### 1. Create a virtual environment

Run these from the project folder (`trade-helper/`). A virtual environment keeps this
project's packages separate from the rest of your system.

```
python -m venv .venv
```

### 2. Activate it

Windows (PowerShell):
```
.venv\Scripts\Activate.ps1
```
If PowerShell blocks the script, run this once, then activate again:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

Windows (Command Prompt):
```
.venv\Scripts\activate.bat
```

macOS / Linux / Git Bash:
```
source .venv/bin/activate      # Git Bash on Windows: source .venv/Scripts/activate
```

When it's active, your prompt starts with `(.venv)`.

### 3. Install the packages

```
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configure

Edit `config.yaml`: set `league_id` (the number in your Sleeper league URL) and
`my_username` (your Sleeper username or display name).

### 5. Run

```
python -m trade_helper partners
```

The first run downloads Sleeper's player database (~5 MB) and caches it in `.cache/`.

Activate the environment again (step 2) each time you open a new terminal. Run
`deactivate` to leave it.

## Commands

```
python -m trade_helper partners                 # best partners + trades, win-win first
python -m trade_helper target "Ja'Marr Chase"   # what it takes to get a player
python -m trade_helper shop "Jared Goff"        # who wants one of your players
python -m trade_helper evaluate --give "Garrett Wilson" --get "Chris Olave"
python -m trade_helper report                   # every team's strength by position
python -m trade_helper values --mine            # player values (--pos RB, --team NAME)
```

Global flags: `--refresh` (ignore cache), `--force` (run after the trade deadline),
`--config PATH`.

## How values work

Where the numbers come from:

| Source | What it provides |
|---|---|
| [FantasyCalc](https://www.fantasycalc.com) API | Rest-of-season trade value (12-team, half-PPR, 1-QB redraft) |
| Sleeper projections (supplied by RotoWire) | Weekly stat projections, re-scored with your league's rules |
| `overrides.csv` | Your manual values, e.g. from a trade value chart |

Every player gets two values on the FantasyCalc scale (top player ~10,000):

- **Now**: projected points over the next `short_term_weeks`, as value over replacement.
  - Nearer weeks count more (`short_term_week_weights`: 1.0 / 0.7 / 0.5), so a bye or injury
    this week costs more than one two weeks out.
  - Byes count as 0.
  - This week's projection is scaled by injury status (`status_factors`): Out and Doubtful
    0%, Questionable 85%. Questionable only counts once the final injury report is out (2
    days before kickoff, 1 day for Thursday games), so a midweek rest day doesn't count.
    Later weeks are not affected.
  - Players below replacement level still get a small, tapering value instead of 0.
- **ROS**: the FantasyCalc value, or your override from `overrides.csv`.

**Value = w x Now + (1 - w) x ROS**, where `w = current week / final_week`. Week 7 of 17 is
41% now. The weight shifts toward "now" as the season goes on. After the league's trade
deadline, trade commands stop.

Flags: `*` top-12 star, `^` ROS value well above current output (breakout/return candidate),
`!` producing above ROS value (bye-week fill / sell high), `R` rookie.

## How trades are scored

Each roster is scored as its best legal starting lineup plus **position-aware depth**. A
trade's **gain** is the change in that score for each side.

- **Depth targets** come from how many players at each position teams in your league
  actually roster (currently QB 2, RB 5, WR 5, TE 2). Backups up to the target count
  (`bench_weights`: 30%, 20%, 15%, 10% of value). Players beyond it count only
  `surplus_weight` (5%). So a backup QB has real value, RB and WR depth matters more
  (you start 2 or 3), and a 3rd TE is nearly worthless.
- **TE in FLEX**: a TE only takes the FLEX spot if he beats the best RB/WR option by a clear
  margin (`te_flex_factor`).

Trades can be any shape in `trade_shapes` (default: 1-for-1 up to 3-for-3). In uneven trades
the roster has to balance out, and the **Roster moves** column shows how:

- The team that receives fewer players **claims a free agent** for each open spot: the
  highest-projected free agent this week at the position it needs most. For example, a team
  with 1 QB claims a backup QB. This assumes the claim succeeds; waiver priority can block
  it in real life.
- The team that receives more players **cuts** whoever costs its roster the least (a
  surplus 3rd TE before a 5th RB). Cutting deep bench costs almost nothing.

IR players don't use active roster spots. Trades with a pointless throw-in (removing the
player gives about the same result) are dropped, and near-duplicates (one player swapped)
are collapsed.

The goal is trades that help **both** teams about equally. `partners` and `shop` show only
**win-win** trades (both lineups improve by at least `min_gain`), ranked by the *smaller* of
the two gains. A +600 / +600 trade beats a +1,400 / +300 one. A trade gets a boost
(`need_bonus`, +25% per side) when an incoming player starts at one of that team's weak
positions; the Type column shows these as "you fill RB / they fill WR". Ties go to the
larger total gain, then the smaller value gap. Package values must be within
`fairness_tolerance` (10%) of each other.

`target` and `evaluate` also label trades that aren't win-win:

- **fair**: you improve; the partner gets more trade-chart value and loses at most
  `max_partner_loss` of lineup value.
- **tough sell**: you improve at the partner's expense.
- **hurts you**: your lineup gets worse.

Package value applies a consolidation discount (`package_weights`), so 2 decent players
don't automatically equal 1 star. All thresholds live in `config.yaml`.
