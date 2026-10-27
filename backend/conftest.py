"""Loaded before tests/conftest.py: keeps the suite independent of the LLM tuning in the developer's .env.

Environment variables take precedence over .env, and an empty effort means "not set".
"""

import os

os.environ["LLM_EFFORT"] = ""
os.environ["LLM_PREP_EFFORT"] = ""
