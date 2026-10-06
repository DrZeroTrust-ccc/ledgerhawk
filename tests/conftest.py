import os

# Screen jobs (awards, outside context, re-check) run in a background thread on the server. Tests run them inside the
# request instead, so a POST returns with the job already finished.
os.environ.setdefault("LEDGERHAWK_JOBS_INLINE", "1")
