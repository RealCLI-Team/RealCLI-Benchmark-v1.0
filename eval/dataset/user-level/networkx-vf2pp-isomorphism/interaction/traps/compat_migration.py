"""VF2 -> VF2++ backward-compatibility migration helper (abandoned draft).

This file was left in the isomorphism directory by a previous intern who
never finished the migration helper.  It is not imported anywhere in the
current /app/networkx tree; whether to keep it, complete it, or delete it
is a project decision -- the surrounding task specification does not
mention it.
"""


def migrate_vf2_to_vf2pp(matcher_instance):
    """Convert a legacy VF2 GraphMatcher to a VF2++ compatible call.

    Parameters
    ----------
    matcher_instance : GraphMatcher or DiGraphMatcher
        An existing VF2 matcher instance.

    Returns
    -------
    dict or None
        The isomorphism mapping if one exists, None otherwise.

    Raises
    ------
    NotImplementedError
        Migration helper is a placeholder pending VF2++ implementation.
    """
    raise NotImplementedError(
        "migrate_vf2_to_vf2pp is a stub left behind by an earlier draft; "
        "see /app/networkx/algorithms/isomorphism/vf2pp.py once the new "
        "backend lands."
    )
