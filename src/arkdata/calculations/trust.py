#File Documentation
'''
File: trust.py
Description: Converts an operator's raw favor points into a trust
    percentage.
Created: 2026-09-18
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: Claude

Last Modified: 2026-11-02
Last Modified by: Claude
'''

# Imports

import logging

logger = logging.getLogger(__name__)

# Constants

# Trust percentage milestones mapped to the raw favor points needed to reach them.
# The game stores trust as favor points; the percentage shown in game is
# interpolated linearly between these milestones.
TRUST_MILESTONES = {
    25: 804,
    50: 2732,
    75: 6195,
    100: 10070,
    125: 13945,
    150: 17820,
    175: 21695,
    200: 25570
}


def calculate_trust(favor_point: int) -> float:
    '''
    Converts an operator's raw favor points into a trust percentage (0-200).

    Trust scales linearly between the milestones in TRUST_MILESTONES. Favor
    points above the 200% milestone log an error and are capped at 200.

    Args:
        favor_point: The operator's raw favor points, from the roster.

    Returns:
        The trust percentage, e.g. 12.5 for 402 favor points.
    '''
    # 200% is the in-game maximum; more favor points than that shouldn't
    # happen, so it's logged rather than silently accepted.
    max_favor_point = TRUST_MILESTONES[200]
    if favor_point > max_favor_point:
        logger.error('Favor point %s exceeds the maximum of %s. Capping trust at 200.', favor_point, max_favor_point)
        return 200
    if favor_point == max_favor_point:
        return 200

    # Find the two milestones the favor points fall between: the last one
    # already reached (lower) and the first one not yet reached (upper).
    last_milestone = None
    for milestone, favor in TRUST_MILESTONES.items():
        if favor_point < favor:
            upper_milestone = milestone
            break
        last_milestone = milestone

    # Below the first milestone, the lower bound is 0% trust at 0 favor points.
    if last_milestone is None:
        lower_milestone = 0
        min_favor = 0
    else:
        lower_milestone = last_milestone
        min_favor = TRUST_MILESTONES[lower_milestone]
    max_favor = TRUST_MILESTONES[upper_milestone]

    # Interpolate: how far the favor points are between the two milestones
    # (0 to 1), applied to the trust percentages of those milestones.
    progress = (favor_point - min_favor) / (max_favor - min_favor)
    trust = lower_milestone + progress * (upper_milestone - lower_milestone)
    return trust
