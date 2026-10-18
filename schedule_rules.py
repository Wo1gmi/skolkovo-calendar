"""Explicit subject exclusions shared by calendar planning and notifications."""
import re


def excluded_title(title):
    subject = title.split('·', 1)[0].casefold().replace('ё', 'е')
    subject = re.sub(r'[^\w\s]', ' ', subject)
    elective = re.search(r'\bэлектив\w*\b', subject)
    physical_education = re.search(
        r'\b(?:физр\w*|физкультур\w*|физическ\w*\s+культур\w*)\b', subject)
    return bool(elective and physical_education)
