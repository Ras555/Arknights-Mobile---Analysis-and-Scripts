#File Documentation
'''
File: trust.py
Description: Converts an operator's raw favor points into a trust
    percentage.
Created: 2026-09-18
Author(s): Ryan Sommerville (ryan.sommerville@bosspac.com)
Modified by: Claude

Last Modified: 2026-10-29
Last Modified by: Claude
'''

# Imports

import logging

logger = logging.getLogger(__name__)

# Constants

# Trust percentage milestones mapped to the raw favor points needed to reach them.
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
    '''
    max_favor_point = TRUST_MILESTONES[200]
    if favor_point > max_favor_point:
        logger.error('Favor point %s exceeds the maximum of %s. Capping trust at 200.', favor_point, max_favor_point)
        return 200
    if favor_point == max_favor_point:
        return 200

    last_milestone = None
    for milestone, favor in TRUST_MILESTONES.items():
        if favor_point < favor:
            upper_milestone = milestone
            break
        last_milestone = milestone

    if last_milestone is None:
        lower_milestone = 0
        min_favor = 0
    else:
        lower_milestone = last_milestone
        min_favor = TRUST_MILESTONES[lower_milestone]
    max_favor = TRUST_MILESTONES[upper_milestone]

    progress = (favor_point - min_favor) / (max_favor - min_favor)
    trust = lower_milestone + progress * (upper_milestone - lower_milestone)
    return trust
