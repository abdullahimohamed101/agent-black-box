"""Entrypoint for the background worker: `python -m abb_api.worker`."""

from abb_api.jobs.worker import main

if __name__ == "__main__":
    main()
