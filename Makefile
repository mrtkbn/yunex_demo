.PHONY: check fix

# Run the pre-commit checks over every file and show the results
check:
	uv run pre-commit run --all-files

# Auto-fix lint and formatting issues
fix:
	uv run ruff check --fix .
	uv run ruff format .
