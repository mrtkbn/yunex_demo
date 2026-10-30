# yunex_demo

Short description of the project.

## Getting started

```bash
git clone https://github.com/<your-username>/yunex_demo.git
cd yunex_demo
```

## Development

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync                      # create .venv and install dependencies
uv run pytest                # tests
uv run ruff check .          # lint
uv run ruff format .         # format
uv run mypy                  # type check

uv run pre-commit install    # run checks automatically before each commit
make check                   # run the pre-commit checks on all files
make fix                     # auto-fix lint and formatting issues
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Released under the [MIT License](LICENSE).
