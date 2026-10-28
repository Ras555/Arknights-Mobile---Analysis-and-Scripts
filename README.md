# Arknights Damage and Stat Calculation

Personal utility scripts for the Arknights mobile game. The program pulls
your operator roster from the Arknights (Yostar EN) servers and game data
from the HellaAPI (https://awedtan.ca). It then calculates your operators'
stats and DPS, and how each operator matches up against the enemies in a
chosen stage. See [CLAUDE.md](CLAUDE.md) for the full functional spec and
the API reference.

## Getting Started

1. Create a virtual environment and install dependencies:
   ```
   python -m venv .venv
   .venv\Scripts\activate           # Windows
   pip install -r requirements-dev.txt
   ```
2. Copy [.env.example](.env.example) to `.env` in the project root and set
   `USER_EMAIL` to your Yostar account email. `.env` is gitignored.
3. Run a step (or `all` to run every step in order):
   ```
   python app/runner.py roster                     # log in and save your Elite 2 roster
   python app/runner.py stats                      # calculate operator stats and DPS
   python app/runner.py matchups --stage main_06-09
   python app/runner.py all --stage main_06-09
   ```
   If `--stage` is left out, the matchups step asks for a stage id. The
   roster step emails you a login code and asks for it in the console.

   Logs go to stdout and to `logs/arknights.log` (rotating).

## Project Structure

- `app/` - the runnable program: config loading (`config.py`), one flow
  module per step (`roster_flow.py`, `operator_stats_flow.py`,
  `stage_matchup_flow.py`), and `runner.py` (entry point).
- `lib/` - reusable modules: the HellaAPI client, the Arknights account
  helpers (login, roster, trust), operator and enemy stat calculations,
  and the damage formulas.
- `ref/` - outputs that the program reads back on later runs
  (`raw_data.json`, `elite2_operators.csv`, `operator_stats_output.csv`).
- `io/` - outputs meant for you to view (`operator_stats_output.csv`, and
  the `stage_<id>_enemy_stats.csv` / `stage_<id>_matchups.csv` files).
- `test/` - the pytest suite.

## Testing

Run the test suite:
```
pytest test/
```
The tests use fake game data, so they never call the HellaAPI or log into
your account.

### Mutation testing

Mutation testing (`mutmut`) is configured in `pyproject.toml` to mutate
`lib/` and `app/`, running `pytest test/` against each mutant. Lines with
`logger.info(` are skipped (`do_not_mutate_patterns`), since only their text
would change. Tests must import project modules by their full names
(`from lib.damage import ...`, `from app import runner`) or mutmut can't match
tests to the code they cover. `mutmut` 3.x
does not support running natively on Windows - run it from WSL or Linux:
```
wsl
python3 -m venv .venv-wsl && source .venv-wsl/bin/activate
pip install -r requirements-dev.txt
mutmut run
mutmut results     # list surviving mutants
mutmut show <id>   # inspect a specific one
```
Add a test for any mutant that survives in `lib/` or `app/`.
