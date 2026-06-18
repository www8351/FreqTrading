"""Entry point: ``python -m orb ...`` -> orb.cli.main()."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
