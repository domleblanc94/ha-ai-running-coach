"""Bounded communication preferences; this never changes request or token budgets."""

REVIEW_DETAILS = ('concise', 'standard', 'detailed')


def review_detail(value=None):
    value = (value or 'standard').strip().lower()
    if value not in REVIEW_DETAILS:
        raise ValueError('Review Detail must be concise, standard or detailed.')
    return value


def review_instructions(profile, settings=None):
    from coaching_mode import planning_settings
    settings = settings or planning_settings()
    detail = review_detail(profile.get('review_detail'))
    lengths = {
        'concise': 'Aim for a 40-70 word summary and 1-2 useful sentences per activity.',
        'standard': 'Aim for an 80-140 word summary and 3-4 useful sentences per activity.',
        'detailed': 'Aim for a 140-220 word summary and 4-6 useful sentences per activity.',
    }
    if settings['mode'] == 'advanced':
        lengths.update(standard='Aim for a 200-350 word summary and 4-6 useful sentences per activity.',
                       detailed='Aim for a 400-700 word summary and 6-10 useful sentences per activity.')
    return (
        'The selected review_detail is ' + detail + '. ' + lengths[detail] + ' '
        'Explain what the supplied activity evidence shows, what it means for the athlete goal, '
        'and one practical next action or point to repeat. Use measured pace, duration, elevation, '
        'heart rate, power, splits or athlete-reported effort only when actually supplied; identify '
        'missing evidence rather than inventing measurements or physiology. A useful review can '
        'recognize good execution and explain why it matters, not merely repeat the statistics. '
        'The summary should connect recent sessions, recovery, goal trajectory and the next training '
        f"focus. Short paragraphs are allowed; keep summary within {settings['summary_max_chars']} characters and each activity comment within {settings['feedback_max_chars']} characters. "
        'Distinguish observations, estimates and uncertainty. Honor communication preferences '
        'and avoid repetitive praise or generic advice. Detail uses this same single response and '
        'the configured output limit: prioritize complete JSON, actionable session prescriptions '
        'and the most relevant comments; do not request extra calls or raise a budget. '
    )
