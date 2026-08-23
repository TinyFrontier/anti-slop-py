"""Opt-in rule groups.

A group is a subpackage here whose ``__init__`` exposes two names:

* ``GROUP_RULES`` -- the group's rules, every id prefixed with the group name
  (``fastapi/no-dict-body-parameters``), so a group can never collide with a core
  rule and a diagnostic always says which group it came from;
* ``CORE_MESSAGE_OVERRIDES`` -- replacement message templates for *core* rules, which
  is how a group gives an existing rule a framework-specific recipe without forking
  it.

Groups are never added to :data:`anti_slop.CORE_RULES`. They exist only when named in
configuration::

    [tool.anti-slop]
    groups = ["fastapi"]

``anti_slop.engine.config`` resolves that list into the effective registry
(:attr:`~anti_slop.engine.config.Config.registry`); :data:`KNOWN_GROUPS
<anti_slop.engine.config.KNOWN_GROUPS>` is the list of groups this distribution
ships, and anything else is a configuration error.

Shipped groups: ``fastapi``.
"""
