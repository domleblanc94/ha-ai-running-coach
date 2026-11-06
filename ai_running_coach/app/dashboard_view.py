"""Presentation-only compatibility for dashboards saved by earlier releases."""

from pathlib import Path
import re


CORE_ASSET = Path(__file__).resolve().parent.parent / 'core' / 'dashboard_assets' / 'calendar-layout.js'
COMPATIBILITY = Path(__file__).with_name('web') / 'calendar-compat.js'
LEGACY_TITLE = re.compile(
    rb'\A(<!DOCTYPE html>\s*<html lang="en" class="dark">\s*<head>\s*'
    rb'<meta charset="UTF-8">\s*'
    rb'<meta name="viewport" content="width=device-width, initial-scale=1\.0">\s*'
    rb'<title>)AI Endurance Coach - Calendar Dashboard(</title>)'
)


def saved_dashboard(content):
    """Adapt the response, leaving the last-good HTML and training data intact."""
    # Match the generated document's first head/title before scripts or imported
    # content. A quoted old title in a body, script or custom page is untouched.
    content = LEGACY_TITLE.sub(rb'\g<1>AI Multisport Coach - Calendar Dashboard\g<2>', content, count=1)
    content = content.replace(
        b"if (!['http:','https:'].includes(location.protocol)) {", b'if (true) {', 1,
    )
    content += b'<style>#toggleViewBtn,#tableView{display:none!important}</style>'
    # Only fixed, bundled code enters the opaque dashboard frame. No private
    # settings or current-plan content is interpolated into its saved revision.
    content += b'<script>if (!window.CoachCalendar) {\n' + CORE_ASSET.read_bytes() + b'\n}</script>'
    content += b'<script>' + COMPATIBILITY.read_bytes() + b'</script>'
    return content
