"""Command-line entry point: python -m trade_helper <command>."""
import argparse
import difflib
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .config import load_config
from .engine import TradeEngine, distinct, needs, position_strength, sort_key
from .league import load_league
from .sleeper import SKILL_POSITIONS

for stream in (sys.stdout, sys.stderr):  # team names can contain non-Latin characters
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")
console = Console()
TRADE_COMMANDS = {"partners", "target", "shop", "evaluate"}


# ---------- formatting ----------

def tags(p):
    t = ""
    if p.star:
        t += "*"
    if p.upside:
        t += "^"
    if p.win_now:
        t += "!"
    if p.rookie:
        t += "R"
    extra = []
    if p.injury:
        extra.append(p.injury)
    if p.byes:
        extra.append("bye " + ",".join(map(str, p.byes)))
    return t + (f" [dim]({'; '.join(extra)})[/dim]" if extra else "")


def fmt_player(p):
    return f"{p.name} [dim]{p.pos}[/dim] {p.value:,.0f}{tags(p)}"


def fmt_side(players):
    return "\n".join(fmt_player(p) for p in players)


def signed(v):
    color = "green" if v > 0 else "red" if v < 0 else "white"
    return f"[{color}]{v:+,.0f}[/{color}]"


def header(league):
    console.print(f"[bold]{league.name}[/bold] {league.season} - week {league.week}/{league.final_week}"
                  f"  |  you: [bold]{league.me.name}[/bold] ({league.me.record()})")
    if league.trade_deadline:
        left = league.weeks_to_deadline
        status = ("[red]PASSED[/red]" if league.deadline_passed else
                  "[yellow]this week[/yellow]" if left == 0 else f"{left} week(s) away")
        deadline = f"trade deadline week {league.trade_deadline}: {status}"
    else:
        deadline = "no trade deadline"
    adjust = ", ".join(f"{k} {mine:g} (FC {fc:g})" for k, (mine, fc) in league.scoring_changes.items())
    console.print(f"values: FantasyCalc rest of season ({league.num_teams} teams, {league.fc_ppr:g} PPR)"
                  f"{', scoring adjusted for ' + adjust if adjust else ''}  |  {deadline}")
    console.print(f"[dim]* star  ^ value well above projected wks {league.short_weeks[0]}-{league.short_weeks[-1]}"
                  "  ! projected well above value  R rookie[/dim]\n")


def roster_moves(t):
    """Waiver claims / cuts the trade forces, e.g. 'They claim Tyler Allgeier RB'."""
    lines = []
    for who, claims, cuts in (("You", t.my_claims, t.my_cuts), ("They", t.their_claims, t.their_cuts)):
        lines += [f"{who} claim {p.name} [dim]{p.pos} {p.value:,.0f}[/dim]" for p in claims]
        lines += [f"{who} cut {p.name} [dim]{p.pos} {p.value:,.0f}[/dim]" for p in cuts]
    return "\n".join(lines) or "[dim]-[/dim]"


def trade_table(trades, title, show_partner=False):
    table = Table(title=title, show_lines=True, title_justify="left")
    if show_partner:
        table.add_column("Partner")
    table.add_column("You give")
    table.add_column("You get")
    table.add_column("Your gain", justify="right")
    table.add_column("Their gain", justify="right")
    table.add_column("Value gap", justify="right")
    table.add_column("Type")
    table.add_column("Roster moves")
    for t in trades:
        kind = {"win-win": "[green]win-win[/green]", "tough sell": "[yellow]tough sell[/yellow]",
                "hurts you": "[red]hurts you[/red]"}.get(t.kind, t.kind)
        fills = [f"{who} fill {', '.join(sorted(f))}" for who, f in (("you", t.my_fills), ("they", t.their_fills)) if f]
        if fills:
            kind += "\n[cyan]" + "\n".join(fills) + "[/cyan]"
        row = [fmt_side(t.give), fmt_side(t.get), signed(t.my_gain), signed(t.their_gain), f"{t.fairness:+.0%}", kind,
               roster_moves(t)]
        if show_partner:
            row.insert(0, f"{t.partner.name}\n[dim]{t.partner.record()}[/dim]")
        table.add_row(*row)
    console.print(table)


def needs_summary(strength, team):
    weak, strong = needs(strength, team)
    return f"weak: {', '.join(weak) or '-'}  strong: {', '.join(strong) or '-'}"


# ---------- player lookup ----------

def find_player(league, name, pool=None):
    pool = pool if pool is not None else league.rostered()
    by_name = {p.name.lower(): p for p in pool}
    key = name.strip().lower()
    if key in by_name:
        return by_name[key]
    partial = [p for p in pool if key in p.name.lower()]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        raise SystemExit(f"{name!r} is ambiguous: {', '.join(p.name for p in partial[:8])}")
    close = difflib.get_close_matches(key, by_name, n=1, cutoff=0.6)
    if close:
        return by_name[close[0]]
    raise SystemExit(f"No rostered player matching {name!r}")


def split_names(s):
    return [n for n in (x.strip() for x in s.split(",")) if n]


# ---------- commands ----------

def cmd_partners(league, engine, cfg, args):
    strength = position_strength(league)
    console.print(f"[bold]Your team[/bold] - {needs_summary(strength, league.me)}\n")
    ranked = engine.partners(args.top or cfg["trades_per_partner"])
    if not ranked:
        console.print("No win-win trades found. Try raising fairness_tolerance, lowering min_gain, "
                      "or adding trade_shapes in config.yaml.")
        return
    for team, trades in ranked[: args.teams]:
        trade_table(trades, f"{team.name} ({team.record()}) - {needs_summary(strength, team)}")


def cmd_target(league, engine, cfg, args):
    p = find_player(league, args.player)
    if p.owner is league.me:
        raise SystemExit(f"{p.name} is already on your team. Use `shop` to trade him away.")
    if p.owner is None:
        raise SystemExit(f"{p.name} is a free agent.")
    trades = distinct(engine.search(p.owner, must_get=(p,), win_win_only=False), args.top)
    if not trades:
        console.print(f"No fair offer for {fmt_player(p)} improves your lineup.")
        return
    trade_table(trades, f"Offers for {p.name} ({p.owner.name}) - negative 'their gain' = harder sell")


def cmd_shop(league, engine, cfg, args):
    p = find_player(league, args.player, league.me.players)
    trades = []
    for team in league.teams:
        if team is not league.me:
            trades += distinct(engine.search(team, must_give=(p,)), 2)
    trades.sort(key=sort_key)
    if not trades:
        console.print(f"No fair trade of {fmt_player(p)} helps both sides.")
        return
    trade_table(trades[: args.top], f"Shopping {p.name}", show_partner=True)


def cmd_evaluate(league, engine, cfg, args):
    give = [find_player(league, n, league.me.players) for n in split_names(args.give)]
    get = [find_player(league, n) for n in split_names(args.get)]
    owners = {p.owner.roster_id for p in get if p.owner}
    if len(owners) != 1 or any(p.owner is None or p.owner is league.me for p in get):
        raise SystemExit("All --get players must be on the same other team.")
    t = engine.evaluate(get[0].owner, give, get)
    trade_table([t], f"Trade with {t.partner.name}")
    gap = t.fairness
    if t.my_gain > 0 and t.their_gain > 0:
        verdict = "[green]Win-win[/green]: both starting lineups improve."
    elif t.my_gain > 0:
        verdict = "[yellow]Good for you, not them[/yellow]: expect pushback."
    elif t.their_gain > 0:
        verdict = "[red]Helps them more than you.[/red]"
    else:
        verdict = "[red]Neither lineup improves.[/red]"
    console.print(verdict)
    if abs(gap) > cfg["fairness_tolerance"]:
        side = "you" if gap > 0 else "them"
        console.print(f"Value gap {gap:+.0%} favors {side} (tolerance {cfg['fairness_tolerance']:.0%}).")


def cmd_report(league, engine, cfg, args):
    strength = position_strength(league)
    table = Table(title="Starter strength vs league average (100 = average)", title_justify="left")
    table.add_column("Team")
    table.add_column("Rec", justify="right")
    table.add_column("Lineup", justify="right")
    for pos in SKILL_POSITIONS:
        table.add_column(pos, justify="right")
    table.add_column("Best spare pieces")
    rows = sorted(league.teams, key=lambda t: engine.base[t.roster_id], reverse=True)
    for t in rows:
        s = strength[t.roster_id]
        cells = []
        for pos in SKILL_POSITIONS:
            r = s[pos]["ratio"] * 100
            color = "green" if r >= 110 else "red" if r < 90 else "white"
            cells.append(f"[{color}]{r:.0f}[/{color}]")
        spares = sorted((x["spare"] for x in s.values() if x["spare"]), key=lambda p: p.value, reverse=True)
        name = f"[bold]{t.name}[/bold]" if t is league.me else t.name
        table.add_row(name, t.record(), f"{engine.base[t.roster_id]:,.0f}", *cells,
                      ", ".join(f"{p.name} ({p.pos} {p.value:,.0f})" for p in spares[:3]))
    console.print(table)


def cmd_values(league, engine, cfg, args):
    if args.team:
        team = next((t for t in league.teams if args.team.lower() in (t.name.lower(), t.username.lower())), None)
        if team is None:
            raise SystemExit(f"No team {args.team!r}")
        pool = team.players
    elif args.mine:
        pool = league.me.players
    else:
        pool = list(league.players.values())
    if args.pos:
        pool = [p for p in pool if p.pos == args.pos.upper()]
    pool = sorted(pool, key=lambda p: p.value, reverse=True)[: args.limit]
    table = Table(title_justify="left")
    for col in ("Player", "Pos", "NFL", "Owner"):
        table.add_column(col)
    for col in (f"Proj wk {league.short_weeks[0]}-{league.short_weeks[-1]}", "Market", "Scoring", "Value"):
        table.add_column(col, justify="right")
    for p in pool:
        table.add_row(f"{p.name} {tags(p)}", p.pos, p.nfl_team, p.owner.name if p.owner else "[dim]FA[/dim]",
                      f"{p.short_pts:.1f}", f"{p.market_value:,.0f}", f"x{p.scoring_factor:.2f}",
                      f"[bold]{p.value:,.0f}[/bold]")
    console.print(table)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="trade_helper", description="Sleeper trade helper")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parent.parent / "config.yaml"))
    parser.add_argument("--league", help="league name from config.yaml (default: its `league` setting)")
    parser.add_argument("--refresh", action="store_true", help="ignore cached data")
    parser.add_argument("--force", action="store_true", help="run trade commands after the deadline")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("partners", help="best trade partners and trades for your team")
    p.add_argument("--teams", type=int, default=5, help="partners to show")
    p.add_argument("--top", type=int, help="trades per partner")
    # Names may be quoted or not: `target Kyren Williams` works like `target "Kyren Williams"`.
    p = sub.add_parser("target", help="what it takes to get a player")
    p.add_argument("player", nargs="+")
    p.add_argument("--top", type=int, default=8)
    p = sub.add_parser("shop", help="who wants one of your players")
    p.add_argument("player", nargs="+")
    p.add_argument("--top", type=int, default=8)
    p = sub.add_parser("evaluate", help="grade a specific trade")
    p.add_argument("--give", required=True, nargs="+", help='comma separated, e.g. Player A, Player B')
    p.add_argument("--get", required=True, nargs="+")
    sub.add_parser("report", help="positional strength of every team")
    p = sub.add_parser("values", help="player values")
    p.add_argument("--pos")
    p.add_argument("--team", help="team or owner name")
    p.add_argument("--mine", action="store_true")
    p.add_argument("--limit", type=int, default=40)

    args = parser.parse_args(argv)
    for name in ("player", "give", "get"):
        if isinstance(getattr(args, name, None), list):
            setattr(args, name, " ".join(getattr(args, name)))
    cfg =load_config(args.config, args.league)
    with console.status("Loading league data..."):
        league = load_league(cfg, refresh=args.refresh)
    header(league)
    if args.command in TRADE_COMMANDS and league.deadline_passed and not args.force:
        console.print(f"[red]Trade deadline (week {league.trade_deadline}) has passed - no trades possible.[/red] "
                      "Use --force to run anyway.")
        sys.exit(1)
    engine = TradeEngine(league, cfg)
    globals()[f"cmd_{args.command}"](league, engine, cfg, args)
