# Arknights Damage and Stat Calculation

Personal utility scripts for the Arknights mobile game. The program pulls
your operator roster from the Arknights (Yostar EN) servers and game data
from the HellaAPI (https://awedtan.ca). It then calculates your operators'
stats and DPS, and how each operator matches up against the enemies in a
chosen stage. See [CLAUDE.md](CLAUDE.md) for the full functional spec and
the API reference.

## Getting Started

1. Create a virtual environment and install the project with its dev tools:
   ```
   python -m venv .venv
   .venv\Scripts\activate           # Windows
   pip install -e .[dev]
   ```
   `-e` (editable) installs the packages from `src/`, so code changes take
   effect without reinstalling. Rerun it only when `pyproject.toml` changes.
2. Copy [.env.example](.env.example) to `.env` in the project root and set
   `USER_EMAIL` to your Yostar account email. `.env` is gitignored.
3. Run a step (or `all` to run every step in order):
   ```
   arknights-calc roster                     # log in and save your Elite 2 roster
   arknights-calc stats                      # calculate operator stats and DPS
   arknights-calc matchups --stage main_06-09
   arknights-calc all --stage main_06-09
   ```
   `python -m arknights_calc <step>` works the same way. If `--stage` is left
   out, the matchups step asks for a stage id. The roster step emails you a
   login code and asks for it in the console.

   Logs go to stdout and to `logs/arknights.log` (rotating).

## Project Structure

- `src/arkdata/` - a generic Arknights library, with no knowledge of this
  program:
  - `calculations/` - damage formulas, operator and enemy stats, trust,
    stage contents, and operator-vs-enemy matchups.
  - `sources/` - the HellaAPI client and the Arknights account helpers
    (login, roster).
- `src/arknights_calc/` - this program: the command line (`cli.py`),
  settings, logging, file storage (`storage.py`), and one flow per step in
  `flows/`.
- `ref/` - outputs that the program reads back on later runs
  (`raw_data.json`, `elite2_operators.csv`, `operator_stats_output.csv`).
- `io/` - outputs meant for you to view (`operator_stats_output.csv`, and
  the `stage_<id>_enemy_stats.csv` / `stage_<id>_matchups.csv` files).
- `tests/` - the pytest suite, split into `arkdata/` and `arknights_calc/`.

`arkdata` must never import from `arknights_calc`; a test checks this.

## Testing

Run the test suite:
```
pytest
```
The tests use fake game data, so they never call the HellaAPI or log into
your account.

### Mutation testing

Mutation testing (`mutmut`) is configured in `pyproject.toml` to mutate
everything in `src/`, running the test suite against each mutant. Lines with
`logger.info(` are skipped (`do_not_mutate_patterns`), since only their text
would change. `mutmut` 3.x does not support running natively on Windows -
run it from WSL or Linux:
```
wsl
python3 -m venv .venv-wsl && source .venv-wsl/bin/activate
pip install -e .[dev]
mutmut run
mutmut results     # list surviving mutants
mutmut show <id>   # inspect a specific one
```
Add a test for any mutant that survives in `src/`.
