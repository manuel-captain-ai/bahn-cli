---
name: bahn-de
description: >-
  Find the cheapest way from A to B in Europe from the command line: train prices from bahn.de, coach and FlixTrain from Flixbus, and an Interrail pass calculator. Runs locally, no server, no API key.
---

# bahn-de

A CLI for **bahn.de** - the unofficial, undocumented Deutsche Bahn web API, providing station search, connection search with prices, and day-best-price (Tagesbestpreis) lookups. No authentication required.

## Disclaimer

This tool uses the inofficial, undocumented bahn.de web API. It is **not affiliated with, endorsed by, or supported by Deutsche Bahn AG**. Use for personal purposes only, do not run bulk or automated mass queries, and be aware that the API can change or block access at any time without notice.

Originally scaffolded in the CLI-Anything format ([HKUDS/CLI-Anything](https://github.com/HKUDS/CLI-Anything), Apache-2.0); reimplemented as a standalone package.

## Installation

Runs entirely locally: no server, no token, no API key.

```bash
uv tool install git+https://github.com/manuel-captain-ai/bahn-cli
```

From the workspace instead: `pip install -e apps/bahn-de`.

A thin remote client (`bahn-remote`, talks to bahn.captain-ai.de) also exists, but
prefer the local install: the server runs on a datacenter IP that bahn.de's Akamai
blocks most of the time.

## Usage

### Basic Commands

```bash
# Show help
bahn-de --help

# Start interactive REPL mode
bahn-de

# Search for a station
bahn-de stations "Hamburg"

# Search for connections
bahn-de search "Hamburg Hbf" "Berlin Hbf" --date 2026-06-19 --time 09:00

# Cheapest connections across a day
bahn-de best "Hamburg Hbf" "Berlin Hbf" --date 2026-06-19

# JSON output (for agent consumption)
bahn-de --json search "Hamburg Hbf" "Berlin Hbf" --date 2026-06-19
```

## Command Groups

### Stations

Search for stations and locations.

| Command | Description |
|---------|-------------|
| `stations QUERY [--limit N]` | Search stations/locations by name, shows name, extId, LID |

### Cheapest across all sources

**For any "what is the cheapest way to get to X" question, use `cheapest`.** One call
fans out to bahn.de and Flixbus in parallel, merges into a single price-sorted list,
and appends an Interrail verdict. Do not run `best` twice and join the results by hand.

| Command | Description |
|---------|-------------|
| `cheapest FROM TO [--date YYYY-MM-DD] [--time HH:MM] [--limit N] [--interrail-trips N] [--age N]` | Price comparison across rail, coach and Interrail pass |

- `--interrail-trips N`: total travel days planned. A pass almost never pays off for
  a single journey, so ask before assuming a number other than 1.
- `--age N`: enables the Interrail youth (under 28, −25 %) or senior (60+, −10 %) rate.

**Coverage, state it when it matters:** bahn.de prices what Deutsche Bahn retails
(Germany plus routes into FR/AT/NL/IT/CZ/DK). Purely non-German routes
(Kraków→Budapest, Paris→Lisbon) usually return a schedule but `preis: null` — DB
does not sell them. `preis: null` means "not bookable through this source", **never
"free"**; never treat it as 0 or present it as the cheapest option. No source covers
the whole European rail market, so do not imply the comparison is exhaustive.

### Connections

Search and price train connections.

| Command | Description |
|---------|-------------|
| `search FROM TO [--date YYYY-MM-DD] [--time HH:MM] [--arrival] [--before N] [--after N] [--around N] [--limit N] [--via VIA] [--source auto\|bahn\|flix\|navitia]` | Search connections with prices |
| `best FROM TO --date YYYY-MM-DD [--top N]` | Cheapest connections across the whole day (Tagesbestpreis, bahn.de only) |

`--source`: `auto` (bahn.de, falling back to Flixbus and then navitia when bahn.de
finds nothing), `bahn`, `flix` (coach and FlixTrain, real prices, strong on
non-German routes), `navitia` (pan-European schedules, no prices, needs a key).

`--before N` / `--after N` add departures up to `N` minutes before/after the requested time; `--around N` sets both at once.

`--via VIA` enables a journey with a long stay at an intermediate station (bahn.de "Reise mit Zwischenhalt"). Two formats:
- `Station@YYYY-MM-DDTHH:MM` -- continue at that timestamp; the CLI pre-searches to compute the minimum stay.
- `Station:72h` / `Station:3d` / `Station:4320m` -- fixed minimum stay in hours, days, or minutes.
Use `--via` up to 2 times. Because prices require a `/recon` call per connection, always use `--json` and expect ~1.2 s per connection. Example: `--json search "Rendsburg" "Brussel-Noord" --date 2026-07-03 --time 09:00 --via "Neanderthal@2026-07-06T08:00"`.

Both `search` and `best` accept the fare options below.

### Schema

Emit the JSON Schema of the output models (for agent tool definitions).

| Command | Description |
|---------|-------------|
| `schema [--model connection\|station]` | Print the JSON Schema of the `Connection` (default) or `Station` model |

The output schema is defined by pydantic v2 models in `bahn_de/models.py`; the CLI, the service layer (`bahn_de/service.py`), and any frontend share them.

### Config

Default fare parameters, stored at `~/.config/bahn-de/config.json` (chmod 600).

| Command | Description |
|---------|-------------|
| `config set KEY VALUE` | Set a default fare parameter (`VALUE = none` removes it) |
| `config get [KEY]` | Get a configuration value, or show all |
| `config path` | Show the config file path |

Config keys: `bahncard` (`none`/`25`/`50`), `bahncard_class` (`1`/`2`), `first_class` (`true`/`false`), `deutschlandticket` (`true`/`false`).

## Fare Options

Available on `search` and `best`. Precedence: CLI flag > `config.json` > default (1 adult, 2nd class, no discount).

| Option | Description |
|--------|-------------|
| `--bahncard [25\|50]` | Apply BahnCard discount |
| `--bahncard-class [1\|2]` | Class of the BahnCard itself (not the travel class) |
| `--first-class` / `--no-first-class` | Travel in 1st class |
| `--deutschlandticket` / `--no-deutschlandticket` | Apply Deutschlandticket pricing |
| `--d-ticket-only` | Only connections bookable with Deutschlandticket |
| `--max-transfers N` | Maximum number of transfers |
| `--direct-only` | Direct connections only (= `--max-transfers 0`) |
| `--min-transfer-time N` | Minimum transfer time in minutes (server-side) |
| `--max-transfer-time N` | Maximum transfer time in minutes (client-side filter) |
| `--bike` | Require bike carriage availability |
| `--via VIA` | Via stop with long stay; up to 2x; see above |

## Examples

### Find a station and its LID

```bash
bahn-de stations "Hamburg"
bahn-de --json stations "Hamburg Hbf" --limit 5
```

### Search connections

```bash
# Departure search
bahn-de search "Hamburg Hbf" "Berlin Hbf" --date 2026-06-19 --time 09:00

# Arrival-time search
bahn-de search "Hamburg Hbf" "München Hbf" --date 2026-06-19 --time 18:00 --arrival

# With BahnCard 50, 2nd class, direct only
bahn-de search "Hamburg Hbf" "Köln Hbf" --date 2026-06-19 --bahncard 50 --bahncard-class 2 --direct-only

# Time window: also show departures up to 60 minutes before/after 09:00
bahn-de search "Hamburg Hbf" "Berlin Hbf" --date 2026-06-19 --time 09:00 --around 60

# Transfer time filter: only 30-80 minute layovers
bahn-de search "Hamburg Hbf" "München Hbf" --date 2026-06-19 --time 09:00 --min-transfer-time 30 --max-transfer-time 80
```

### Day-best-price

```bash
bahn-de best "Hamburg Hbf" "Berlin Hbf" --date 2026-06-19
bahn-de --json best "Hamburg Hbf" "Berlin Hbf" --date 2026-06-19 --top 3
```

### Configure defaults

```bash
bahn-de config set bahncard 50
bahn-de config set bahncard_class 2
bahn-de config set deutschlandticket true
bahn-de config get
bahn-de config set bahncard none
```

## Output Formats

All commands support dual output modes:

- **Human-readable** (default): formatted tables
- **Machine-readable** (`--json` flag): structured JSON for agent consumption

```bash
# Human output
bahn-de search "Hamburg Hbf" "Berlin Hbf"

# JSON output for agents
bahn-de --json search "Hamburg Hbf" "Berlin Hbf"
```

## For AI Agents

When using this CLI programmatically:

1. **Always use `--json`** for parseable output.
2. **`FROM`/`TO` can be plain names or LIDs.** Plain names (e.g. `"Hamburg Hbf"`) work for `search` and `best`, but a full LID string from `stations --json` (form `A=1@O=Hamburg Hbf@...@L=8002549@`) is unambiguous and can be reused directly as `FROM`/`TO`.
3. **`search`/`best` output schema** - each connection:
   ```json
   {
     "trip_id": "...",
     "abfahrt": "2026-06-19T09:02:00",
     "abfahrt_ort": "Hamburg Hbf",
     "ankunft": "2026-06-19T11:48:00",
     "ankunft_ort": "Berlin Hbf",
     "dauer_minuten": 166,
     "umstiege": 1,
     "umstiegszeiten_minuten": [13],
     "produkte": ["ICE", "RE"],
     "abschnitte": [
       {"abfahrt": "...", "abfahrt_ort": "...", "ankunft": "...", "ankunft_ort": "...", "dauer_minuten": 73, "produkt": "ICE", "typ": "PUBLICTRANSPORT"},
       {"abfahrt": "...", "abfahrt_ort": "...", "ankunft": "...", "ankunft_ort": "...", "dauer_minuten": 80, "produkt": "RE", "typ": "PUBLICTRANSPORT"}
     ],
     "preis": 49.90,
     "waehrung": "EUR",
     "via_halte": [],
     "has_teilpreis": false,
     "teilpreise": []
   }
   ```
   `preis` may be `null` if no DB-bookable price exists (e.g. some Flixtrain connections). The full schema (including `via_halte`/`teilpreise` entry shapes) is available via `bahn-de --json schema` and defined in `bahn_de/models.py`.

   `umstiegszeiten_minuten` and `abschnitte` are useful for agents doing their own filtering: `umstiegszeiten_minuten` gives the layover (minutes) per transfer directly (e.g. `[13]`, or `[]` for a direct connection), and `abschnitte[].typ == "PUBLICTRANSPORT"` identifies the actual train/bus legs (other `typ` values are footpaths/platform transfers) - filter on these to inspect specific legs, products per leg, or layover durations without re-deriving them.
4. **Time-window search**: `--before N` / `--after N` / `--around N` on `search` widen the result set around the requested `--time` (e.g. to find earlier or later alternatives for a commute or connecting trip). `--around N` is the convenient shortcut when you just want "anything within N minutes of this time" - handy for commute/connection questions where the exact minute doesn't matter. Note this can issue a second request under the hood (see BAHN_DE.md).
5. **Transfer-time filters**: `--min-transfer-time N` (server-side, via `minUmstiegszeit`) and `--max-transfer-time N` (client-side, computed from `umstiegszeiten_minuten`) let you require a comfortable but not excessive layover, e.g. `--min-transfer-time 30 --max-transfer-time 80`.
6. **`best --json` wraps the list**: `{"source": "bestpreis-endpoint", "connections": [...]}`. The `source` field reports which endpoint produced the result (bahn.de, int.bahn.de, or the `/fahrplan` paging fallback).
7. **`best` can be slow.** If the primary Tagesbestpreis endpoints fail, the CLI falls back to paging over `/fahrplan` (05/09/13/17/21 Uhr), which involves multiple sequential requests with sleeps between them and can take several seconds.
8. **Check return codes** - `0` for success, non-zero for errors.
9. **Error format**:
   - With `--json`: `{"error": "...", "type": "..."}` as valid JSON on stdout, exit code `1`.
   - Without `--json`: `Error: ...` on stderr, exit code `1`.
10. **Use absolute paths** for all file operations.

## More Information

- Full documentation: See [README.md](../README.md)
- Full SOP (endpoints, payload reference, pitfalls): See [BAHN_DE.md](../../../BAHN_DE.md)

## Version

0.3.0
