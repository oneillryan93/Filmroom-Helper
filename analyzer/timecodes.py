"""Times as people write them ("4:05", "1:02:03") and as seconds."""


def format_time(seconds):
    """245 -> "4:05", 3723 -> "1:02:03"."""
    total = int(round(seconds))
    hours, minutes, secs = total // 3600, total // 60 % 60, total % 60
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{total // 60}:{secs:02d}"


def parse_time(text):
    """"4:05" -> 245.0, "1:02:03" -> 3723.0, "95" -> 95.0."""
    total = 0.0
    for part in text.strip().split(":"):
        total = total * 60 + float(part)
    return total
