"""Normalized course vocabulary shared by imports, outcomes and scoring."""
from django.db import models


class Course(models.TextChoices):
    MBBS = 'MBBS', 'MBBS'
    BDS = 'BDS', 'BDS'
    BTECH = 'BTECH', 'BTECH'
    GNM_NURSING = 'GNM_NURSING', 'GNM NURSING'
    BSC_NURSING = 'BSC_NURSING', 'BSC NURSING'
    PHARMACY = 'PHARMACY', 'PHARMACY'
    MBA = 'MBA', 'MBA'
    MD_MS = 'MD_MS', 'MD/MS'
    OTHERS = 'OTHERS', 'Others'


BLOCKED_CONTACT_OUTCOMES = frozenset({'NOT_INTERESTED', 'NO_CANDIDATE', 'WRONG_NUMBER'})


def validate_course(code, custom='', *, required=True):
    custom = ' '.join((custom or '').split())
    if not code and not required:
        return None, ''
    if code not in Course.values:
        raise ValueError('Select a valid course.')
    if code == Course.OTHERS:
        if not custom or len(custom) > 150:
            raise ValueError('Enter a custom course name (1-150 characters).')
    elif custom:
        raise ValueError('Custom course is only allowed for Others.')
    return code, custom


def course_label(code, custom=''):
    return custom if code == Course.OTHERS else dict(Course.choices).get(code, '')


def classify_course(lead, code, custom=''):
    if not lead or not lead.preferred_course or not code:
        return 'UNKNOWN'
    own = lead.preferred_course == code
    if own and code == Course.OTHERS:
        own = ' '.join((lead.preferred_course_custom or '').split()).casefold() == ' '.join(custom.split()).casefold()
    return 'OWN' if own else 'OTHER'
