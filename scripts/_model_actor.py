"""Validate caller-supplied inference attribution without choosing a model."""

POLICY = {'selection': 'caller', 'fallback': 'forbidden', 'inference': 'outside gis-kit'}


def validate_actor(actor):
    if not isinstance(actor, dict) or not isinstance(actor.get('model'), str) or not actor['model'].strip():
        raise ValueError('Inference attribution requires an explicit nonempty model name')
    if 'reasoning_effort' not in actor:
        raise ValueError('Inference attribution requires reasoning_effort (null if unavailable or not applicable)')
    effort = actor['reasoning_effort']
    if effort is not None and (not isinstance(effort, str) or not effort.strip()):
        raise ValueError('reasoning_effort must be a nonempty string or null')
    # This records a declaration, not proof of the model actually invoked.
