"""Local downloads of saved athlete and plan data; no provider calls."""

from collections import defaultdict
from datetime import date, timedelta
from html import escape
from io import BytesIO
from pathlib import Path


def plain(value):
    text = str(value or '').replace('\u2011', '-').replace('\u2013', '-').replace('\u2014', '-')
    return ''.join(char for char in text if ord(char) >= 32 or char in '\n\t')


def calendar_pdf(plan, athlete_name='', *, conflicts=()):
    """One printable calendar week per page, with full session detail afterward."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph
    from reportlab.pdfgen.canvas import Canvas

    import reportlab
    font_dir = Path(reportlab.__file__).parent / 'fonts'
    for name, filename in (('Coach', 'Vera.ttf'), ('CoachBold', 'VeraBd.ttf')):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(font_dir / filename)))
    pdfmetrics.registerFontFamily('Coach', normal='Coach', bold='CoachBold', italic='Coach', boldItalic='CoachBold')
    sessions = plan.get('sessions')
    if not isinstance(sessions, list) or not sessions:
        raise ValueError('Generate a plan with scheduled sessions before exporting the calendar.')
    if len(sessions) > 4000:
        raise ValueError('The saved plan is too large to export as a calendar.')
    by_day = defaultdict(list)
    for session in sessions:
        if not isinstance(session, dict):
            raise ValueError('The saved plan has an invalid session.')
        try:
            day = date.fromisoformat(session['date'])
        except (ValueError, TypeError, KeyError):
            raise ValueError('The saved plan contains an invalid date.') from None
        by_day[day].append(session)
    first, last = min(by_day), max(by_day)
    # Export only weeks that contain sessions, avoiding years of empty pages.
    weeks = sorted({day - timedelta(days=day.weekday()) for day in by_day})
    stream = BytesIO()
    width, height = landscape(A4)
    canvas = Canvas(stream, pagesize=(width, height), pageCompression=1)
    canvas.setTitle('AI Running Coach - training calendar')
    canvas.setAuthor('AI Running Coach')
    ink, muted, accent = colors.HexColor('#152b3b'), colors.HexColor('#526573'), colors.HexColor('#117e72')
    paragraph = ParagraphStyle('CoachText', fontName='Coach', fontSize=9, leading=13, textColor=ink, alignment=TA_LEFT, allowWidows=0, allowOrphans=0)
    small = ParagraphStyle('CoachSmall', parent=paragraph, fontSize=8, leading=11)
    bold = ParagraphStyle('CoachBold', parent=paragraph, fontName='CoachBold', fontSize=10, leading=14)
    page = 0

    def text(value, style=paragraph):
        return Paragraph(escape(plain(value)).replace('\n', '<br/>'), style)

    def header(title, subtitle):
        nonlocal page
        page += 1
        canvas.setFillColor(accent)
        canvas.setFont('CoachBold', 9)
        canvas.drawString(30, height - 30, 'AI RUNNING COACH')
        canvas.setFillColor(ink)
        canvas.setFont('CoachBold', 20)
        canvas.drawString(30, height - 58, title)
        canvas.setFillColor(muted)
        label = plain(athlete_name).strip()
        label = label if len(label) <= 90 else label[:87] + '...'
        line = (label + ' | ' if label else '') + subtitle
        p = text(line, small)
        _, h = p.wrap(width - 60, 34)
        p.drawOn(canvas, 30, height - 72 - h)
        if conflicts:
            p = text('CHECK TRAINING DAYS: This saved plan includes sessions outside your current availability. Regenerate the plan before following those sessions.', small)
            _, warning_height = p.wrap(width - 60, 30)
            p.drawOn(canvas, 30, height - 104 - warning_height)
        canvas.setFont('Coach', 8)
        canvas.drawString(30, 18, 'Saved plan revision ' + plain(plan.get('revision', 'unknown')) + ' | No session on a date does not imply rest.')
        canvas.drawRightString(width - 30, 18, str(page))

    # Card contents are deliberately summaries. Full targets and every session
    # appear in the following pages, where text can flow without clipping.
    for week in weeks:
        header('Training calendar', week.strftime('%d %b %Y') + ' - ' + (week + timedelta(days=6)).strftime('%d %b %Y'))
        gap, left, top, bottom = 7, 30, height - (140 if conflicts else 116), 44
        cell_width = (width - 60 - 6 * gap) / 7
        for index in range(7):
            day = week + timedelta(days=index)
            x = left + index * (cell_width + gap)
            canvas.setFillColor(colors.HexColor('#f3f8f7') if by_day.get(day) else colors.HexColor('#f7f8f9'))
            canvas.setStrokeColor(colors.HexColor('#d7e2e7'))
            canvas.roundRect(x, bottom, cell_width, top - bottom, 6, stroke=1, fill=1)
            canvas.setFillColor(accent)
            canvas.setFont('CoachBold', 10)
            canvas.drawString(x + 9, top - 20, day.strftime('%a').upper())
            canvas.setFillColor(muted)
            canvas.setFont('Coach', 8)
            canvas.drawString(x + 9, top - 35, day.strftime('%d %b'))
            y = top - 54
            rows = by_day.get(day, [])
            if not rows:
                p = text('No session scheduled', small)
                _, h = p.wrap(cell_width - 18, y - bottom)
                p.drawOn(canvas, x + 9, y - h)
            for number, session in enumerate(rows):
                title = plain(session.get('workout_type', 'Session'))
                amount = plain(session.get('amount', ''))
                title = title if len(title) <= 90 else title[:87] + '...'
                amount = amount if len(amount) <= 90 else amount[:87] + '...'
                p = text(title, bold)
                _, h = p.wrap(cell_width - 18, 500)
                a = text(amount, small)
                _, ah = a.wrap(cell_width - 18, 500)
                if y - h - ah - 18 < bottom + 45:
                    p = text(str(len(rows) - number) + ' more session(s). See details.', small)
                    _, h = p.wrap(cell_width - 18, 100)
                    p.drawOn(canvas, x + 9, y - h)
                    break
                p.drawOn(canvas, x + 9, y - h)
                a.drawOn(canvas, x + 9, y - h - ah - 4)
                y -= h + ah + 18
            if rows:
                p = text('Full targets in session details.', small)
                _, h = p.wrap(cell_width - 18, 100)
                p.drawOn(canvas, x + 9, bottom + 10)
        canvas.showPage()

    y = 0

    def detail_page():
        nonlocal y
        header('Session details', first.strftime('%d %b %Y') + ' - ' + last.strftime('%d %b %Y'))
        y = height - (140 if conflicts else 116)

    def flowing(value, style=paragraph):
        nonlocal y
        remaining = text(value, style)
        while remaining:
            available = y - 46
            _, required = remaining.wrap(width - 60, available)
            if required <= available:
                remaining.drawOn(canvas, 30, y - required)
                y -= required + 6
                break
            pieces = remaining.split(width - 60, available)
            if pieces:
                _, used = pieces[0].wrap(width - 60, available)
                pieces[0].drawOn(canvas, 30, y - used)
                remaining = pieces[1] if len(pieces) > 1 else None
            canvas.showPage()
            detail_page()

    detail_page()
    for day in sorted(by_day):
        if y < 130:
            canvas.showPage()
            detail_page()
        flowing(day.strftime('%A %d %B %Y'), bold)
        for session in by_day[day]:
            if y < 150:
                canvas.showPage()
                detail_page()
                flowing(day.strftime('%A %d %B %Y') + ' (continued)', bold)
            flowing(plain(session.get('workout_type', 'Session')) + ' | ' + plain(session.get('amount', '')), bold)
            flowing(session.get('target') or 'No target recorded.')
            workout = session.get('workout')
            if isinstance(workout, dict):
                for index, step in enumerate(workout.get('steps', []), 1):
                    if not isinstance(step, dict):
                        continue
                    parts = [str(index) + '. ' + plain(step.get('name') or step.get('intensity') or 'Workout step')]
                    duration = step.get('duration', {})
                    target = step.get('target', {})
                    if duration.get('type') == 'time':
                        parts.append(str(duration.get('seconds', '?')) + ' seconds')
                    elif duration.get('type') == 'distance':
                        parts.append(str(duration.get('metres', '?')) + ' metres')
                    elif duration.get('type') == 'open':
                        parts.append('Open duration')
                    if target.get('type') == 'heart_rate':
                        parts.append(str(target.get('lowBpm', '?')) + '-' + str(target.get('highBpm', '?')) + ' bpm')
                    elif target.get('type') == 'speed':
                        parts.append(str(target.get('lowMps', '?')) + '-' + str(target.get('highMps', '?')) + ' m/s')
                    elif target.get('type') == 'open':
                        parts.append('Open target')
                    if step.get('notes'):
                        parts.append(plain(step['notes']))
                    flowing(' | '.join(parts), small)
            y -= 8
    canvas.save()
    return stream.getvalue()
