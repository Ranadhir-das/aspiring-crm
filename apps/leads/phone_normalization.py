import re
from typing import Dict, List, Optional, Tuple, Any


def normalize_indian_mobile(raw_value: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Normalize and validate an Indian mobile phone number.

    Returns:
        (normalized_10_digits, None) -> Valid entry (10 digits starting with 6, 7, 8, or 9).
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
        else:
            return None, "Unsupported country code (only Indian +91 numbers are accepted)"

    if "+" in cleaned:
        return None, "Invalid '+' placement in phone number"

    n_digits = len(cleaned)
    if n_digits == 10:
        if re.match(r"^[6-9]\d{9}$", cleaned):
            return cleaned, None
        return None, f"Invalid Indian mobile number (10-digit number must start with 6, 7, 8, or 9; got {cleaned[0]})"

    if n_digits == 12 and cleaned.startswith("91"):
        rest = cleaned[2:]
        if re.match(r"^[6-9]\d{9}$", rest):
            return rest, None
        return None, f"Invalid 12-digit number (remaining 10 digits after 91 must start with 6, 7, 8, or 9; got {rest[0]})"

    if n_digits < 10:
        return None, f"Phone number is too short ({n_digits} digits; expected 10-digit mobile number)"

    return None, f"Phone number is too long ({n_digits} digits; expected 10-digit mobile number)"


def parse_pasted_phone_numbers(text: str) -> Dict[str, Any]:
    """
    Parse a multiline block of pasted phone numbers.

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
        normalized, reason = normalize_indian_mobile(line)
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
