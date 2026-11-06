"""Presentation-only compatibility for dashboards saved by earlier releases."""

from pathlib import Path


CORE_ASSET = Path(__file__).resolve().parent.parent / 'core' / 'dashboard_assets' / 'calendar-layout.js'
COMPATIBILITY = Path(__file__).with_name('web') / 'calendar-compat.js'


def saved_dashboard(content):
    """Adapt the response, leaving the last-good HTML and training data intact."""
    content = content.replace(
        b"if (!['http:','https:'].includes(location.protocol)) {", b'if (true) {', 1,
    )
    content += b'<style>#toggleViewBtn,#tableView{display:none!important}</style>'
    # Only fixed, bundled code enters the opaque dashboard frame. No private
    # settings or current-plan content is interpolated into its saved revision.
    content += b'<script>if (!window.CoachCalendar) {\n' + CORE_ASSET.read_bytes() + b'\n}</script>'
    content += b'<script>' + COMPATIBILITY.read_bytes() + b'</script>'
    return content
