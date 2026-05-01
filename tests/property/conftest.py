"""Hypothesis settings for property-based tests.

Fast mode (CI / dev):  pytest -m "not slow"  — skips all property tests
Normal mode:           pytest                 — runs all tests
Property only:         pytest -m slow

Hypothesis max_examples is tuned per environment:
  - CI (HYPOTHESIS_MAX_EXAMPLES env var): use that value
  - Default: 20 examples (fast enough for dev, still catches bugs)
  - Full suite: set HYPOTHESIS_MAX_EXAMPLES=100
"""
import os
from hypothesis import settings, HealthCheck

# Allow overriding via env var for CI
_max_examples = int(os.environ.get("HYPOTHESIS_MAX_EXAMPLES", "20"))

settings.register_profile(
    "dev",
    max_examples=_max_examples,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    deadline=None,
)
settings.register_profile(
    "ci",
    max_examples=50,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    deadline=None,
)
settings.register_profile(
    "full",
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    deadline=None,
)

# Use dev profile by default; CI can set HYPOTHESIS_PROFILE=ci
_profile = os.environ.get("HYPOTHESIS_PROFILE", "dev")
settings.load_profile(_profile)
