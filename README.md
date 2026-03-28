# goose-runtime-map

A CLI wrapper around `goose run` for runtime-map analysis prompts, with:

- fixed prompt template for runtime-map style analysis
- markdown rendering via `glow`
- answer history storage and read-only history viewer
- optional global alias installer (`grm`)

## Requirements

- `python3`
- `goose` CLI (in `PATH`)
- `glow` CLI (optional but recommended)
- `fzf` (optional; used for history picker)

## Usage

Run a new question:

```bash
python3 grm.py --question "What roles does this project have?"
# or positional
python3 grm.py "What roles does this project have?"
```

Open history picker (read-only view):

```bash
python3 grm.py
# same as:
python3 grm.py --history
```

Useful flags:

```bash
python3 grm.py --status stream|stage|min
python3 grm.py --width 120
python3 grm.py --no-glow
```

## Alias

Install global alias:

```bash
bash install_alias.sh
source ~/.zshrc
```

After install:

```bash
grm "What roles does this project have?"
grm
```

Remove alias:

```bash
bash uninstall_alias.sh
source ~/.zshrc
```

## History

Saved answers are stored under:

- `./.grm-history/<timestamp>-<question-slug>.md`

History is isolated by your current working directory. Running `grm` in different directories creates separate history sets.

History mode only re-renders existing markdown answers. It does **not** call `goose`.

## License

Apache-2.0
