import re
from typing import Any, Dict, List, Optional, Tuple


def normalize_phone_number(raw_value: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Validate and normalize an Indian or Bangladesh mobile phone number.

    Indian mobile numbers:
        - 10-digit mobile number starting with 6, 7, 8, or 9 (e.g. 9876543210, 8806856384).
        - Optional +91 prefix, 91 prefix (12 digits), or 0 prefix (11 digits).
        - Normalized to canonical 10-digit string: '9876543210', '8806856384'.

    Bangladesh mobile numbers:
        - 10-digit national number starting with 1 (e.g. 1712345678).
        - 11-digit local format starting with 01 (e.g. 01712345678).
        - 12-digit format starting with 881 (e.g. 881712345678).
        - 13-digit format starting with 8801 (e.g. 8801712345678).
        - 14-digit format starting with 88001 (e.g. 88001712345678).
        - International format starting with +880 (e.g. +8801712345678) or +88 (e.g. +881712345678).
        - Normalized to canonical E.164 string with '+': '+8801XXXXXXXXX'.

    Returns:
        (normalized_str, None) -> Valid entry.
        (None, None) -> Ignored entry (blank line, wrapper braces).
        (None, error_reason) -> Invalid entry with clear country-specific explanation.
    """
    if raw_value is None:
        return None, None

    raw = str(raw_value).strip()
    if not raw or raw in ("{", "}", "{,", "},"):
        return None, None

    # Strip optional surrounding curly braces, brackets, quotes, commas, and semicolons
    raw = raw.strip("{}[];,\"\'").strip()
    if not raw:
        return None, None

    # Remove formatting spaces, hyphens, dots, and parentheses
    cleaned = re.sub(r"[\s\-\(\)\.]", "", raw)

    # Check for disallowed characters
    if re.search(r"[^\d+]", cleaned):
        return None, "Contains invalid characters (only digits, spaces, hyphens, parentheses, dots and + are permitted)"

    if "+" in cleaned[1:]:
        return None, "Invalid '+' placement in phone number"

    # ==========================================================
    # Format with leading '+' prefix
    # ==========================================================
    if cleaned.startswith("+"):
        # India: +91
        if cleaned.startswith("+91"):
            rest = cleaned[3:]
            if not rest.isdigit():
                return None, "Invalid format after +91 country code"
            if rest.startswith("0") and len(rest) == 11 and rest[1] in "6789":
                rest = rest[1:]
            if len(rest) < 10:
                return None, f"Indian phone number is too short ({len(rest)} digits after +91; expected 10-digit mobile starting with 6, 7, 8, or 9)"
            if len(rest) > 10:
                return None, f"Indian phone number is too long ({len(rest)} digits after +91; expected 10-digit mobile starting with 6, 7, 8, or 9)"
            if not re.match(r"^[6-9]\d{9}$", rest):
                return None, f"Invalid Indian mobile number (must start with 6, 7, 8, or 9; got '{rest[0]}')"
            return rest, None

        # Bangladesh: +880
        if cleaned.startswith("+880"):
            rest = cleaned[4:]
            if rest.startswith("0"):
                rest = rest[1:]
            if len(rest) == 10 and rest.startswith("1"):
                return f"+880{rest}", None
            if len(rest) < 10:
                return None, f"Bangladesh phone number is too short ({len(rest)} digits after +880; expected 10 digits starting with 1, e.g. +8801XXXXXXXXX)"
            if len(rest) > 10:
                return None, f"Bangladesh phone number is too long ({len(rest)} digits after +880; expected 10 digits starting with 1, e.g. +8801XXXXXXXXX)"
            return None, f"Invalid Bangladesh mobile number (must start with 1 after country code; got '{rest[0] if rest else 'empty'}')"

        # Bangladesh: +88
        if cleaned.startswith("+88"):
            rest = cleaned[3:]
            if rest.startswith("0"):
                rest = rest[1:]
            if len(rest) == 10 and rest.startswith("1"):
                return f"+880{rest}", None
            if len(rest) < 10:
                return None, f"Bangladesh phone number is too short ({len(rest)} digits after +88; expected 10 digits starting with 1, e.g. +8801XXXXXXXXX)"
            if len(rest) > 10:
                return None, f"Bangladesh phone number is too long ({len(rest)} digits after +88; expected 10 digits starting with 1, e.g. +8801XXXXXXXXX)"
            return None, f"Invalid Bangladesh mobile number (must start with 1 after country code; got '{rest[0] if rest else 'empty'}')"

        return None, "Unsupported country code (only Indian +91 and Bangladesh +880 / +88 numbers are accepted)"

    # ==========================================================
    # Format without '+' prefix (digits only)
    # ==========================================================
    digits = cleaned
    n_digits = len(digits)

    # 1. Too short (< 10 digits)
    if n_digits < 10:
        if digits.startswith("880"):
            rest_len = n_digits - 3
            return None, f"Bangladesh phone number is too short ({rest_len} digits after 880; expected 10 digits starting with 1, e.g. 8801XXXXXXXXX)"
        if digits.startswith("88"):
            rest_len = n_digits - 2
            return None, f"Bangladesh phone number is too short ({rest_len} digits after 88; expected 10 digits starting with 1, e.g. 881XXXXXXXXX)"
        if digits.startswith("91"):
            rest_len = n_digits - 2
            return None, f"Indian phone number is too short ({rest_len} digits after 91; expected 10-digit mobile starting with 6, 7, 8, or 9)"
        return None, f"Phone number is too short ({n_digits} digits; expected at least 10 digits for Indian or Bangladesh mobile numbers)"

    # 2. Exactly 10 digits
    if n_digits == 10:
        # India: 10 digits starting with 6, 7, 8, or 9 (crucial: accepts numbers like 8806856384!)
        if re.match(r"^[6-9]\d{9}$", digits):
            return digits, None
        # Bangladesh: 10 digits starting with 1 (national number without leading 0, e.g. 1712345678)
        if digits.startswith("1"):
            return f"+880{digits}", None
        # Invalid 10-digit number
        return None, (
            f"Invalid 10-digit mobile number (must start with 6, 7, 8, or 9 for India [e.g. 9876543210] "
            f"or 1 for Bangladesh [e.g. 1712345678]; got '{digits[0]}')"
        )

    # 3. Exactly 11 digits
    if n_digits == 11:
        if digits.startswith("0"):
            rest = digits[1:]
            # India with domestic trunk prefix 0: 0[6-9]...
            if re.match(r"^[6-9]\d{9}$", rest):
                return rest, None
            # Bangladesh local format: 01XXXXXXXXX
            if rest.startswith("1"):
                return f"+880{rest}", None
            return None, (
                f"Invalid 11-digit number with leading 0 (expected Indian mobile 0[6-9]... "
                f"or Bangladesh mobile 01...; got '0{rest[0]}')"
            )
        # 11 digits starting with 880 (e.g. 88012345678 -> only 8 digits after 880)
        if digits.startswith("880"):
            return None, "Bangladesh phone number is too short (8 digits after 880; expected 10 digits starting with 1, e.g. 8801XXXXXXXXX)"
        if digits.startswith("88"):
            return None, "Bangladesh phone number is too short (9 digits after 88; expected 10 digits starting with 1, e.g. 881XXXXXXXXX)"
        if digits.startswith("91"):
            return None, "Indian phone number is too short (9 digits after 91; expected 10-digit mobile starting with 6, 7, 8, or 9)"
        return None, f"Invalid 11-digit number (expected 11 digits starting with 0 for India/Bangladesh or 10-digit mobile; got '{digits}')"

    # 4. Exactly 12 digits
    if n_digits == 12:
        if digits.startswith("91"):
            rest = digits[2:]
            if re.match(r"^[6-9]\d{9}$", rest):
                return rest, None
            return None, f"Invalid 12-digit Indian number (digits after 91 must start with 6, 7, 8, or 9; got '{rest[0]}')"
        if digits.startswith("88"):
            rest = digits[2:]
            if rest.startswith("1"):
                return f"+880{rest}", None
            if rest.startswith("0"):
                return None, "Bangladesh phone number is too short (9 digits after 880; expected 10 digits starting with 1, e.g. 8801XXXXXXXXX)"
            return None, f"Invalid 12-digit Bangladesh number (digits after 88 must start with 1; got '{rest[0]}')"
        if digits.startswith("880"):
            return None, "Bangladesh phone number is too short (9 digits after 880; expected 10 digits starting with 1, e.g. 8801XXXXXXXXX)"
        return None, f"Phone number is too long ({n_digits} digits; expected 10-digit Indian or Bangladesh mobile number)"

    # 5. Exactly 13 digits
    if n_digits == 13:
        if digits.startswith("880"):
            rest = digits[3:]
            if rest.startswith("1"):
                return f"+880{rest}", None
            if rest.startswith("0"):
                return None, "Bangladesh phone number is too short (9 digits after 8800; expected 10 digits starting with 1, e.g. 8801XXXXXXXXX)"
            return None, f"Invalid Bangladesh mobile number (digits after 880 must start with 1; got '{rest[0]}')"
        if digits.startswith("910"):
            rest = digits[3:]
            if re.match(r"^[6-9]\d{9}$", rest):
                return rest, None
        return None, f"Phone number is too long ({n_digits} digits; expected 10-digit Indian or Bangladesh mobile number)"

    # 6. Exactly 14 digits
    if n_digits == 14:
        if digits.startswith("8800"):
            rest = digits[4:]
            if rest.startswith("1"):
                return f"+880{rest}", None
            return None, f"Invalid Bangladesh mobile number (expected 88001XXXXXXXXX; got '{digits[:5]}...')"
        if digits.startswith("880") and digits[3:].startswith("01"):
            rest = digits[4:]
            if rest.startswith("1"):
                return f"+880{rest}", None
        return None, f"Phone number is too long ({n_digits} digits; expected 10-digit Indian or Bangladesh mobile number)"

    # 7. Too long (> 14 digits)
    return None, f"Phone number is too long ({n_digits} digits; expected 10-digit Indian or Bangladesh mobile number)"


# Aliases for shared access and backward compatibility
normalize_pasted_phone_number = normalize_phone_number
normalize_indian_mobile = normalize_phone_number


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
        normalized, reason = normalize_phone_number(line)
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


def get_phone_lookup_variants(phone: str) -> List[str]:
    """
    Return all equivalent representations of a phone number (both with and without '+',
    country codes, trunk prefixes) for duplicate lookup in database / memory maps.
    """
    if not phone:
        return []

    raw = str(phone).strip()
    norm, _ = normalize_phone_number(raw)
    cleaned = re.sub(r"[^\d+]", "", raw)
    digits = re.sub(r"\D", "", raw)

    variants = set()
    if raw:
        variants.add(raw)
    if cleaned:
        variants.add(cleaned)
    if digits:
        variants.add(digits)
    if norm:
        variants.add(norm)
        norm_digits = re.sub(r"\D", "", norm)
        if norm_digits:
            variants.add(norm_digits)

    # Bangladesh variants
    is_bd = (
        (norm and norm.startswith("+8801"))
        or (digits.startswith("8801") and len(digits) == 13)
        or (digits.startswith("88001") and len(digits) == 14)
        or (digits.startswith("881") and len(digits) == 12)
        or (digits.startswith("01") and len(digits) == 11)
        or (digits.startswith("1") and len(digits) == 10 and not raw.startswith("+91") and not raw.startswith("91"))
    )

    if is_bd:
        nat10 = None
        if norm and norm.startswith("+880"):
            nat10 = norm[4:]
        elif digits.startswith("88001") and len(digits) == 14:
            nat10 = digits[4:]
        elif digits.startswith("8801") and len(digits) == 13:
            nat10 = digits[3:]
        elif digits.startswith("881") and len(digits) == 12:
            nat10 = digits[2:]
        elif digits.startswith("01") and len(digits) == 11:
            nat10 = digits[1:]
        elif digits.startswith("1") and len(digits) == 10:
            nat10 = digits

        if nat10 and len(nat10) == 10 and nat10.startswith("1"):
            variants.add(f"+880{nat10}")
            variants.add(f"880{nat10}")
            variants.add(f"+88{nat10}")
            variants.add(f"88{nat10}")
            variants.add(f"0{nat10}")
            variants.add(nat10)
            variants.add(f"+8800{nat10}")
            variants.add(f"8800{nat10}")

    # India variants
    is_in = (
        (norm and re.match(r"^[6-9]\d{9}$", norm))
        or (len(digits) == 10 and digits[0] in "6789")
        or (len(digits) == 11 and digits.startswith("0") and digits[1] in "6789")
        or (len(digits) == 12 and digits.startswith("91") and digits[2] in "6789")
    )

    if is_in:
        nat10 = None
        if norm and len(norm) == 10 and norm[0] in "6789":
            nat10 = norm
        elif len(digits) == 10 and digits[0] in "6789":
            nat10 = digits
        elif len(digits) == 11 and digits.startswith("0") and digits[1] in "6789":
            nat10 = digits[1:]
        elif len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
            nat10 = digits[2:]

        if nat10 and len(nat10) == 10:
            variants.add(nat10)
            variants.add(f"+91{nat10}")
            variants.add(f"91{nat10}")
            variants.add(f"0{nat10}")

    return sorted(list(variants))
