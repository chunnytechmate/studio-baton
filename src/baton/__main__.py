"""Allow ``python -m baton`` alongside the installed ``baton`` script."""

from .cli.app import main

if __name__ == "__main__":
    main()
