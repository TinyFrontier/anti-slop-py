"""Opt-in rule groups (PLAN.md section 3.5).

Empty in v1: the mechanism is a subpackage per group with its own id prefix
(``anti-slop-<group>/...``), enabled only by explicit configuration. Groups are
never added to :data:`anti_slop.CORE_RULES`.
"""
