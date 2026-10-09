# session-service

A tiny OAuth session helper used by Agent Black Box's coding-agent demo. It has a bug on purpose:
sessions are not refreshed before they expire, and a refresh can drop the refresh token. Run the tests:

    python -m unittest discover -s tests -t .
