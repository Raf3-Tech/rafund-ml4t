"""Kraken Prop live risk system: cost model, account-state floors, and the
pre-trade gate. All Decimal, no floats — this path guards a real account
with a hard breach consequence (positions closed, account disabled).
"""
