# bahn-cli

Find the cheapest way from A to B in Europe, from your terminal. Compares **train
tickets (bahn.de)**, **coach and FlixTrain (Flixbus)**, **foreign rail and coach
inventory (Omio)** and tells you whether an **Interrail pass** would be cheaper.

Runs entirely on your own machine. No server, no account, no API key.

```
$ bahn-de cheapest "Berlin Hbf" "München Hbf" --date 2026-08-20

Preisvergleich (guenstigste zuerst)

    30.48 EUR  07:40->15:35    7:55h  direkt  [Flixbus]  BUS
    41.47 EUR  07:15->17:35   10:20h      1x  [Flixbus]  BUS
    54.74 EUR  08:36->12:46    4:10h  direkt  [bahn.de]  ICE 1005
    56.24 EUR  09:36->13:43    4:07h  direkt  [bahn.de]  ICE 1505

Interrail-Check
  Einzeltickets sind guenstiger: 85.22 EUR gegen 283.00 EUR Pass.
```

## Install

```bash
uv tool install git+https://github.com/manuel-captain-ai/bahn-cli
```

Or with pipx:

```bash
pipx install git+https://github.com/manuel-captain-ai/bahn-cli
```

## Commands

| Command | What it does |
| --- | --- |
| `cheapest FROM TO --date` | Compare all sources, plus the Interrail verdict |
| `search FROM TO --date` | Connections with prices (`--source auto\|bahn\|flix\|omio\|navitia`) |
| `best FROM TO --date` | Cheapest fares across a whole day (bahn.de) |
| `stations QUERY` | Resolve a station name |
| `group --origin A --origin B --to C` | Several people, one destination or a meeting point |
| `config set bahncard 25` | Persist your fare defaults |

Add `--json` before the command for machine-readable output:
`bahn-de --json cheapest …`

Useful flags: `--bahncard 25|50`, `--first-class`, `--deutschlandticket`,
`--direct-only`, `--max-transfers N`, `--age N` (Interrail youth/senior discount),
`--interrail-trips N` (how many travel days you are planning).

## Coverage, honestly

- **bahn.de** sells international tickets wherever Deutsche Bahn retails them:
  Germany plus routes into FR, AT, NL, IT, CZ, DK and more.
- **Purely non-German routes** (Kraków→Budapest, Vienna→Zagreb) return a schedule
  but often **no price** from bahn.de — DB does not sell those. The tool says so
  rather than guessing.
- **Flixbus** covers coach and FlixTrain across Europe with real, bookable prices,
  including the routes above.
- **Omio** covers foreign rail inventory (ÖBB, RENFE, PKP, HŽ and others), which is
  where the other two go quiet. Vienna→Zagreb returns a real 31.75 EUR train fare
  where bahn.de returns no price at all.
- Omio also resells Flix coaches at a small markup, so you will see the same
  departure twice under two sources. That is deliberate: the source column makes it
  visible, and the cheaper row sorts first.
- No source short of a commercial Trainline partner contract covers the whole
  European rail retail market. The output states its coverage; it never implies
  completeness.

Interrail prices are a static table with a visible `stand` (as-of) date. Check
interrail.com before you buy.

## Use with Claude Code / Cowork

The package ships a skill at `bahn_de/skills/SKILL.md`. Copy it into your
`.claude/skills/bahn/` and ask in plain language ("cheapest way to Vienna on the
20th"). The skill knows the defaults and calls the CLI for you.

## Disclaimer

Uses the unofficial, undocumented bahn.de, Flixbus and Omio web APIs. **Not
affiliated with, endorsed by, or supported by Deutsche Bahn AG, Flix SE or Omio
GmbH.** For personal use only. Do not run bulk or automated mass queries — Omio in
particular rate-limits by IP. These APIs can change or block access at any time.

## Licence

See `LICENSE`.
