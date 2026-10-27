import re
from typing import Dict, List, Optional, Tuple, Any


import re
from typing import Dict, List, Optional, Tuple, Any


def normalize_pasted_phone_number(raw_value: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Normalize and validate an Indian or Bangladesh mobile phone number from copy-paste input.

    Indian mobile format:
        Returns normalized 10 digits (starting with 6, 7, 8, or 9).
    Bangladesh mobile format:
        Returns normalized E.164 format '+8801XXXXXXXXX' (11-digit national number starting with 01,
        or variations with +880, +88, 880, 88, or 10-digit 1XXXXXXXXX).

    Returns:
        (normalized_str, None) -> Valid entry.
        (None, None) -> Ignored entry (blank line or standalone/surrounding wrapper brace).
        (None, error_reason) -> Invalid entry with human-readable reason.
    """
    if raw_value is None:
        return None, None

    raw = str(raw_value).strip()
    if not raw or raw in ("{", "}", "{,", "},"):
        return None, None

    # Strip optional surrounding curly braces, quotes, commas, and semicolons
    raw = raw.strip("{}[];,\"\'").strip()
    if not raw:
        return None, None

    # Remove formatting spaces, hyphens, and parentheses
    cleaned = re.sub(r"[\s\-\(\)]", "", raw)

    # Check for disallowed characters
    if re.search(r"[^\d+]", cleaned):
        return None, "Contains invalid characters (only digits, spaces, hyphens, parentheses and + are permitted)"

    if "+" in cleaned[1:]:
        return None, "Invalid '+' placement in phone number"

    # With '+' prefix
    if cleaned.startswith("+"):
        if cleaned.startswith("+91"):
            rest = cleaned[3:]
            if not rest.isdigit():
                return None, "Invalid format after +91 country code"
            if len(rest) < 10:
                return None, f"Phone number is too short ({len(rest)} digits after +91; expected 10)"
            if len(rest) > 10:
                return None, f"Phone number is too long ({len(rest)} digits after +91; expected 10)"
            if not re.match(r"^[6-9]\d{9}$", rest):
                return None, f"Invalid Indian mobile number (must start with 6, 7, 8, or 9; got {rest[0]})"
            return rest, None

        if cleaned.startswith("+880"):
            rest = cleaned[4:]
            if rest.startswith("0"):
                rest = rest[1:]
            if len(rest) == 10 and rest.startswith("1"):
                return f"+880{rest}", None
            if len(rest) < 10:
                return None, f"Bangladesh phone number is too short ({len(rest)} digits after +880; expected 10)"
            if len(rest) > 10:
                return None, f"Bangladesh phone number is too long ({len(rest)} digits after +880; expected 10)"
            return None, f"Invalid Bangladesh mobile number (must start with 1; got {rest[0] if rest else 'empty'})"

        if cleaned.startswith("+88"):
            rest = cleaned[3:]
            if rest.startswith("0"):
                rest = rest[1:]
            if len(rest) == 10 and rest.startswith("1"):
                return f"+880{rest}", None
            if len(rest) < 10:
                return None, f"Bangladesh phone number is too short ({len(rest)} digits after +88; expected 10 or 11)"
            if len(rest) > 10:
                return None, f"Bangladesh phone number is too long ({len(rest)} digits after +88; expected 10 or 11)"
            return None, f"Invalid Bangladesh mobile number (must start with 1; got {rest[0] if rest else 'empty'})"

        return None, "Unsupported country code (only Indian +91 and Bangladesh +880 / +88 numbers are accepted)"

    # Without '+' prefix
    n_digits = len(cleaned)

    # 1. 880 prefix
    if cleaned.startswith("880"):
        rest = cleaned[3:]
        if rest.startswith("0"):
            rest = rest[1:]
        if len(rest) == 10 and rest.startswith("1"):
            return f"+880{rest}", None
        if len(rest) < 10:
            return None, f"Bangladesh phone number is too short ({len(rest)} digits after 880; expected 10)"
        if len(rest) > 10:
            return None, f"Bangladesh phone number is too long ({len(rest)} digits after 880; expected 10)"
        return None, f"Invalid Bangladesh mobile number (must start with 1; got {rest[0] if rest else 'empty'})"

    # 2. 88 prefix with length 12 or 13
    if cleaned.startswith("88") and n_digits in (12, 13):
        rest = cleaned[2:]
        if rest.startswith("0"):
            rest = rest[1:]
        if len(rest) == 10 and rest.startswith("1"):
            return f"+880{rest}", None

    # 3. 91 prefix with length 12
    if cleaned.startswith("91") and n_digits == 12:
        rest = cleaned[2:]
        if re.match(r"^[6-9]\d{9}$", rest):
            return rest, None
        return None, f"Invalid 12-digit number (remaining 10 digits after 91 must start with 6, 7, 8, or 9; got {rest[0]})"

    # 4. Leading 0 with length 11
    if cleaned.startswith("0") and n_digits == 11:
        rest = cleaned[1:]
        if re.match(r"^[6-9]\d{9}$", rest):
            return rest, None
        if len(rest) == 10 and rest.startswith("1"):
            return f"+880{rest}", None
        return None, "Invalid 11-digit number with leading 0 (expected Indian starting with 6-9 or Bangladesh starting with 01)"

    # 5. 10 digits
    if n_digits == 10:
        if re.match(r"^[6-9]\d{9}$", cleaned):
            return cleaned, None
        if cleaned.startswith("1"):
            return f"+880{cleaned}", None
        return None, f"Invalid 10-digit number (must start with 6, 7, 8, or 9 for India or 1 for Bangladesh; got {cleaned[0]})"

    if n_digits < 10:
        return None, f"Phone number is too short ({n_digits} digits; expected at least 10 digits)"

    return None, f"Phone number is too long ({n_digits} digits; expected 10-digit Indian or Bangladesh mobile number)"


# Backward compatibility alias
normalize_indian_mobile = normalize_pasted_phone_number


def parse_pasted_phone_numbers(text: str) -> Dict[str, Any]:
    """
    Parse a multiline block of pasted phone numbers (supporting India and Bangladesh).

    Returns:
        {
            "valid_numbers": [str, ...],
            "invalid_entries": [{"entry": str, "original": str, "reason": str}, ...],
            "ignored_count": int,
            "total_submitted": int,
        }
    """
    if not text:
        return {
            "valid_numbers": [],
            "invalid_entries": [],
            "ignored_count": 0,
            "total_submitted": 0,
        }

    valid_numbers: List[str] = []
    invalid_entries: List[Dict[str, str]] = []
    ignored_count = 0

    for line in text.splitlines():
        orig = line.strip()
        normalized, reason = normalize_pasted_phone_number(line)
        if normalized is not None:
            valid_numbers.append(normalized)
        elif reason is not None:
            invalid_entries.append({"entry": orig, "original": orig, "reason": reason})
        else:
            ignored_count += 1

    return {
        "valid_numbers": valid_numbers,
        "invalid_entries": invalid_entries,
        "ignored_count": ignored_count,
        "total_submitted": len(valid_numbers) + len(invalid_entries),
    }
