"""Shared timestamp tolerance for provider-observed external signals.

A bounded five-minute future tolerance accommodates provider/collector clock skew.
It is not permission to accept arbitrarily future-dated events; validity still ends
against the actual reconciliation or execution clock.
"""
from datetime import timedelta

MAX_FUTURE_OBSERVED_AT_SKEW = timedelta(minutes=5)
