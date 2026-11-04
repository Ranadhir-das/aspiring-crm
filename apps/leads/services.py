import csv
import io
import logging
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from openpyxl import load_workbook

from .models import Lead, LeadAssignmentHistory, LeadImportBatch
from .utils import normalize_phone


REQUIRED_COLUMNS = {
    "phone",
}


OPTIONAL_COLUMNS = {
    "name",
    "email",
    "location",
    "college",
    "neet_status",
    "pcb_percentage",
    "preferred_intake",
    "source",
    "campaign",
    "notes",
}


def normalize_column_name(column):
    """
    Convert Excel/CSV column names into a consistent format.
    """

    if column is None:
        return ""

    key = str(column).strip().lower().replace(" ", "_").replace("-", "_")
    alias_map = {
        "mobile": "phone",
        "mobile_number": "phone",
        "mobile_no": "phone",
        "phone_number": "phone",
        "phone_no": "phone",
        "contact": "phone",
        "contact_no": "phone",
        "contact_number": "phone",
        "full_name": "name",
        "student_name": "name",
        "candidate_name": "name",
        "lead_name": "name",
    }
    return alias_map.get(key, key)


def read_csv_file(file):
    """
    Read a CSV file and return rows as dictionaries.
    """

    content = file.read()

    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            content = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(content))

    headers = [
        normalize_column_name(header)
        for header in reader.fieldnames or []
    ]

    rows = []

    for row in reader:
        normalized_row = {}

        for key, value in row.items():
            normalized_key = normalize_column_name(key)

            normalized_row[normalized_key] = (
                value.strip()
                if isinstance(value, str)
                else value
            )

        rows.append(normalized_row)

    return headers, rows


def read_xlsx_file(file):
    """
    Read an XLSX file and return rows as dictionaries.
    """

    workbook = load_workbook(
        filename=file,
        read_only=True,
        data_only=True,
    )

    worksheet = workbook.active

    rows_data = list(
        worksheet.iter_rows(values_only=True)
    )

    if not rows_data:
        return [], []

    headers = [
        normalize_column_name(header)
        for header in rows_data[0]
    ]

    rows = []

    for row in rows_data[1:]:
        normalized_row = {}

        for index, value in enumerate(row):
            if index >= len(headers):
                continue

            header = headers[index]

            if header:
                if isinstance(value, float) and value.is_integer():
                    val_str = str(int(value))
                elif value is not None:
                    val_str = str(value).strip()
                else:
                    val_str = ""
                normalized_row[header] = val_str

        rows.append(normalized_row)

    return headers, rows


def read_import_file(file):
    """
    Read CSV or XLSX file based on its extension.
    """

    filename = getattr(file, "name", "").lower()

    if filename.endswith(".csv"):
        return read_csv_file(file)

    if filename.endswith(".xlsx"):
        return read_xlsx_file(file)

    raise ValueError(
        "Unsupported file format. Please upload a CSV or XLSX file."
    )


def validate_columns(headers):
    """
    Validate required and unknown columns.
    """

    header_set = set(headers)

    missing_columns = REQUIRED_COLUMNS - header_set

    unknown_columns = (
        header_set
        - REQUIRED_COLUMNS
        - OPTIONAL_COLUMNS
    )

    return {
        "valid": not missing_columns,
        "missing_columns": sorted(missing_columns),
        "unknown_columns": sorted(unknown_columns),
    }


def validate_row(row, row_number):
    """
    Validate one imported lead row.
    Supports Indian and Bangladesh mobile phone numbers.
    """

    errors = []
    warnings = []

    name = str(row.get("name", "")).strip() if row.get("name") is not None else ""
    phone = str(row.get("phone", "")).strip() if row.get("phone") is not None else ""

    # Name is optional, blank lead names are permitted.

    if not phone:
        errors.append("Phone is required.")

    normalized_phone = ""
    if phone:
        from .phone_normalization import normalize_phone_number
        norm, reason = normalize_phone_number(phone)
        if norm is not None:
            normalized_phone = norm
        else:
            errors.append(f"Phone number is invalid: {reason}")

    email = str(row.get("email", "")).strip()

    if email and "@" not in email:
        warnings.append(
            "Email format may be invalid."
        )

    # Validate PCB percentage if supplied.
    pcb_percentage = row.get("pcb_percentage")

    if pcb_percentage not in (None, ""):
        try:
            percentage = Decimal(
                str(pcb_percentage).strip()
            )

            if percentage < 0 or percentage > 100:
                errors.append(
                    "PCB percentage must be between 0 and 100."
                )

        except (InvalidOperation, ValueError):
            errors.append(
                "PCB percentage must be a valid number."
            )

    return {
        "row_number": row_number,
        "errors": errors,
        "warnings": warnings,
        "normalized_phone": normalized_phone,
    }


def get_existing_phone_map():
    """
    Load existing lead phone numbers once.

    Returns:
        dict[str, Lead]
    """

    phone_map = {}

    for lead in (
        Lead.objects
        .only("id", "name", "phone")
        .iterator()
    ):
        if not lead.phone:
            continue

        raw_phone = str(lead.phone).strip()
        phone_map[raw_phone] = lead
        digits = normalize_phone(raw_phone)
        if digits:
            phone_map[digits] = lead

        from .phone_normalization import get_phone_lookup_variants
        for v in get_phone_lookup_variants(raw_phone):
            phone_map.setdefault(v, lead)
            v_digits = normalize_phone(v)
            if v_digits:
                phone_map.setdefault(v_digits, lead)

    return phone_map


def check_duplicate_from_map(phone, phone_map):
    """
    Check for a duplicate using an in-memory phone map.
    Matches exact normalized phone as well as standard Indian (91, 0) and Bangladesh (880, 88, 0) prefixes.
    """

    if not phone:
        return None

    str_phone = str(phone).strip()
    if str_phone in phone_map:
        return phone_map[str_phone]

    from .phone_normalization import get_phone_lookup_variants
    variants = get_phone_lookup_variants(str_phone)
    for v in variants:
        if v in phone_map:
            return phone_map[v]
        v_digits = normalize_phone(v)
        if v_digits and v_digits in phone_map:
            return phone_map[v_digits]

    normalized = normalize_phone(phone)

    if not normalized:
        return None

    if normalized in phone_map:
        return phone_map[normalized]

    # Indian formats (10 digits starting with 6-9)
    if len(normalized) == 10 and normalized[0] in "6789":
        match = phone_map.get(f"91{normalized}") or phone_map.get(f"0{normalized}")
        if match:
            return match
    elif len(normalized) == 12 and normalized.startswith("91") and normalized[2] in "6789":
        match = phone_map.get(normalized[2:])
        if match:
            return match
    elif len(normalized) == 11 and normalized.startswith("0") and normalized[1] in "6789":
        match = phone_map.get(normalized[1:])
        if match:
            return match

    # Bangladesh formats (13, 12, 11, or 10 digits starting with BD mobile prefixes)
    if len(normalized) == 13 and normalized.startswith("8801"):
        national_11 = normalized[2:]  # 01XXXXXXXXX
        national_10 = normalized[3:]  # 1XXXXXXXXX
        code_88_12 = f"88{national_10}"
        return (
            phone_map.get(national_11)
            or phone_map.get(national_10)
            or phone_map.get(code_88_12)
        )
    elif len(normalized) == 12 and normalized.startswith("881"):
        national_10 = normalized[2:]  # 1XXXXXXXXX
        full_13 = f"880{national_10}"
        national_11 = f"0{national_10}"
        return (
            phone_map.get(full_13)
            or phone_map.get(national_11)
            or phone_map.get(national_10)
        )
    elif len(normalized) == 11 and normalized.startswith("01"):
        national_10 = normalized[1:]  # 1XXXXXXXXX
        full_13 = f"88{normalized}"
        code_88_12 = f"88{national_10}"
        return (
            phone_map.get(full_13)
            or phone_map.get(national_10)
            or phone_map.get(code_88_12)
        )
    elif len(normalized) == 10 and normalized.startswith("1"):
        full_13 = f"880{normalized}"
        national_11 = f"0{normalized}"
        code_88_12 = f"88{normalized}"
        return (
            phone_map.get(full_13)
            or phone_map.get(national_11)
            or phone_map.get(code_88_12)
        )

    return None


def prepare_pcb_percentage(value):
    """
    Convert PCB percentage into a Decimal or None.

    Validation is performed separately by validate_row().
    """

    if value in (None, ""):
        return None

    return Decimal(str(value).strip())


def preview_import(file):
    """
    Read and validate an import file without creating leads.
    """

    headers, rows = read_import_file(file)

    column_result = validate_columns(headers)

    if not column_result["valid"]:
        return {
            "success": False,
            "columns": column_result,
            "total_rows": len(rows),
            "rows": [],
        }

    preview_rows = []

    valid_count = 0
    warning_count = 0
    duplicate_count = 0
    error_count = 0

    # Load existing database phones only once.
    existing_phone_map = get_existing_phone_map()

    # Track duplicate phones inside the uploaded file itself.
    seen_phones = set()

    for index, row in enumerate(rows, start=2):

        validation = validate_row(row, index)

        duplicate = None

        if not validation["errors"]:

            normalized_phone = validation["normalized_phone"]

            # Check duplicates already stored in database.
            duplicate = check_duplicate_from_map(
                normalized_phone,
                existing_phone_map,
            )

            # Check duplicates within the same uploaded file.
            if (
                not duplicate
                and normalized_phone in seen_phones
            ):
                validation["warnings"].append(
                    "Duplicate phone number in uploaded file."
                )

                duplicate = True

            if normalized_phone:
                seen_phones.add(normalized_phone)
                from .phone_normalization import get_phone_lookup_variants
                for v in get_phone_lookup_variants(normalized_phone):
                    seen_phones.add(v)
                    v_digits = normalize_phone(v)
                    if v_digits:
                        seen_phones.add(v_digits)
                    existing_phone_map[v] = True
                    if v_digits:
                        existing_phone_map[v_digits] = True

        if duplicate:
            duplicate_count += 1

            if isinstance(duplicate, Lead):
                validation["warnings"].append(
                    f"Duplicate lead already exists: "
                    f"{duplicate.name} ({duplicate.phone})"
                )

        if validation["errors"]:
            error_count += 1
        else:
            valid_count += 1

        if validation["warnings"]:
            warning_count += 1

        preview_rows.append({
            "row_number": index,
            "data": row,
            "errors": validation["errors"],
            "warnings": validation["warnings"],
            "duplicate": bool(duplicate),
            "normalized_phone": validation["normalized_phone"],
        })

    return {
        "success": True,
        "columns": column_result,
        "total_rows": len(rows),
        "valid_count": valid_count,
        "warning_count": warning_count,
        "duplicate_count": duplicate_count,
        "error_count": error_count,
        "rows": preview_rows,
    }


def commit_import(file, imported_by, assigned_caller=None, *, preferred_course=None, preferred_course_custom=""):
    """
    Import valid leads into the database from a CSV or XLSX file.

    Duplicate phone numbers (both within the file and against existing CRM leads)
    are permitted and imported as separate leads, with duplicates tracked in summary metrics.

    Returns a summary of the import.
    """

    from .courses import validate_course
    preferred_course, preferred_course_custom = validate_course(
        preferred_course, preferred_course_custom, required=False)

    headers, rows = read_import_file(file)

    column_result = validate_columns(headers)

    if not column_result["valid"]:
        raise ValueError(
            f"Missing required columns: "
            f"{', '.join(column_result['missing_columns'])}"
        )

    created_ids = []
    created_count = 0
    duplicate_count = 0
    warning_count = 0
    skipped_count = 0

    with transaction.atomic():

        batch = LeadImportBatch.objects.create(
            filename=file.name,
            imported_by=imported_by,
            total_rows=len(rows),
        )

        # Load existing database phones only once.
        existing_phone_map = get_existing_phone_map()

        # Track phones from the current upload.
        seen_phones = set()

        for index, row in enumerate(rows, start=2):

            validation = validate_row(row, index)

            if validation["errors"]:
                skipped_count += 1
                continue

            if validation["warnings"]:
                warning_count += 1

            normalized_phone = validation["normalized_phone"]

            # Check existing database leads or duplicate within the uploaded file.
            duplicate = check_duplicate_from_map(
                normalized_phone,
                existing_phone_map,
            )

            if duplicate or (normalized_phone and normalized_phone in seen_phones):
                duplicate_count += 1

            if normalized_phone:
                seen_phones.add(normalized_phone)
                from .phone_normalization import get_phone_lookup_variants
                for v in get_phone_lookup_variants(normalized_phone):
                    seen_phones.add(v)
                    v_digits = normalize_phone(v)
                    if v_digits:
                        seen_phones.add(v_digits)
                    existing_phone_map[v] = True
                    if v_digits:
                        existing_phone_map[v_digits] = True

            pcb_percentage = prepare_pcb_percentage(
                row.get("pcb_percentage")
            )

            lead = Lead(
                import_batch=batch,
                preferred_course=preferred_course,
                preferred_course_custom=preferred_course_custom,
                name=str(row.get("name", "")).strip() if row.get("name") is not None else "",
                phone=str(row.get("phone", "")).strip() if row.get("phone") is not None else "",
                email=str(row.get("email", "")).strip() if row.get("email") is not None else "",
                location=str(row.get("location", "")).strip() if row.get("location") is not None else "",
                college=str(row.get("college", "")).strip() if row.get("college") is not None else "",
                neet_status=str(row.get("neet_status", "")).strip() if row.get("neet_status") is not None else "",
                pcb_percentage=pcb_percentage,
                preferred_intake=str(row.get("preferred_intake", "")).strip() if row.get("preferred_intake") is not None else "",
                source=str(row.get("source", "")).strip() if row.get("source") is not None else "",
                campaign=str(row.get("campaign", "")).strip() if row.get("campaign") is not None else "",
                notes=str(row.get("notes", "")).strip() if row.get("notes") is not None else "",
            )
            lead._changed_by = imported_by
            lead.save()

            created_ids.append(lead.pk)
            created_count += 1

            # Add the newly created lead to the map.
            # This allows subsequent duplicate tracking in the same batch.
            existing_phone_map[normalized_phone] = lead

        if assigned_caller:
            User = get_user_model()
            from apps.accounts.capabilities import is_caller_capable
            if not isinstance(assigned_caller, User) or not is_caller_capable(assigned_caller):
                raise ValueError("Selected user is not an active caller.")

        if assigned_caller and created_ids:
            bulk_assign_leads(created_ids, assigned_caller, imported_by,
                              reason=f'Assigned during import {batch.pk}',
                              notify=True)

        batch.created_count = created_count
        batch.duplicate_count = duplicate_count
        batch.warning_count = warning_count

        batch.save(
            update_fields=[
                "created_count",
                "duplicate_count",
                "warning_count",
            ]
        )

    return {
        "success": True,
        "batch_id": batch.id,
        "filename": file.name,
        "total_rows": len(rows),
        "created_count": created_count,
        "duplicate_count": duplicate_count,
        "warning_count": warning_count,
        "skipped_count": skipped_count,
    }


def bulk_assign_leads(
    lead_ids,
    new_caller,
    assigned_by,
    reassign=False,
    reason="",
    notify=True,
):
    """
    Assign multiple leads to a caller.

    If reassign=False:
        Already assigned leads are skipped.

    If reassign=True:
        Already assigned leads are reassigned.

    Returns a summary of the operation.
    """

    User = get_user_model()
    from apps.accounts.capabilities import is_caller_capable
    if not isinstance(new_caller, User) or not is_caller_capable(new_caller):
        raise ValueError(
            "Selected user is not a caller."
        )

    if not lead_ids:
        raise ValueError(
            "No leads were selected."
        )

    assigned_count = 0
    skipped_count = 0
    reassigned_count = 0
    assigned_leads = []

    with transaction.atomic():

        leads = (
            Lead.objects
            .select_for_update()
            .filter(id__in=lead_ids)
        )

        from .apostille_leads import is_apostille_caller
        if leads.filter(service_type__code='APOSTILLE').exists() and not is_apostille_caller(new_caller):
            raise ValueError('Select an active Apostille caller.')

        for lead in leads:

            previous_caller = lead.assigned_caller

            # Already assigned and reassignment is disabled.
            if previous_caller and not reassign:
                skipped_count += 1
                continue

            # Already assigned to the same caller.
            if previous_caller == new_caller:
                skipped_count += 1
                continue

            lead.assigned_caller = new_caller
            lead.assigned_at = timezone.now()

            lead.save(
                update_fields=[
                    "assigned_caller",
                    "assigned_at",
                    "updated_at",
                ]
            )

            LeadAssignmentHistory.objects.create(
                lead=lead,
                previous_caller=previous_caller,
                new_caller=new_caller,
                assigned_by=assigned_by,
                reason=reason,
            )

            if previous_caller:
                reassigned_count += 1
            else:
                assigned_count += 1

            assigned_leads.append(lead)

        if notify and assigned_leads:
            assigned_lead_ids = [l.pk for l in assigned_leads]
            caller_pk = new_caller.pk
            print(f"[Push Assignment] on_commit registered for caller_id={caller_pk} count={len(assigned_lead_ids)}")
            logger.info("[Push Assignment] on_commit registered for caller_id=%s count=%s", caller_pk, len(assigned_lead_ids))

            def _on_commit_notify():
                print(f"[Push Assignment] on_commit executing for caller_id={caller_pk}")
                logger.info("[Push Assignment] on_commit executing for caller_id=%s", caller_pk)
                try:
                    from apps.accounts.push_notifications import notify_caller_about_assigned_leads
                    notify_caller_about_assigned_leads(caller_pk, assigned_lead_ids)
                except Exception as exc:
                    print(f"[Push Assignment] unexpected exception: {exc}")
                    logger.exception("[Push Assignment] unexpected exception: %s", exc)

            transaction.on_commit(_on_commit_notify)

    return {
        "success": True,
        "total_selected": len(lead_ids),
        "assigned_count": assigned_count,
        "reassigned_count": reassigned_count,
        "skipped_count": skipped_count,
    }


def generate_paste_batch_name(preferred_course, preferred_course_custom=""):
    from .courses import course_label
    import zoneinfo
    kolkata_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
    now = timezone.now().astimezone(kolkata_tz)
    label = course_label(preferred_course, preferred_course_custom) or "General"
    base_name = f"{label} - {now.strftime('%Y-%m-%d %H:%M:%S')}"
    batch_name = base_name
    counter = 1
    while LeadImportBatch.objects.filter(filename=batch_name).exists():
        batch_name = f"{base_name} ({counter})"
        counter += 1
    return batch_name


def commit_paste_import(
    pasted_text,
    imported_by,
    assigned_caller=None,
    *,
    preferred_course=None,
    preferred_course_custom="",
    idempotency_key=None,
    batch_name=None,
):
    """
    Import leads from multiline pasted phone numbers.

    - Duplicate phone numbers (both within the pasted list and against existing CRM leads)
      are permitted and imported as separate leads.
    - Tracks and reports duplicate entries and invalid numbers in summary metrics.
    - Supports request-level idempotency via idempotency_key.
    - Validates assigned caller server-side.
    - Accepts an optional manual batch_name (whitespace-trimmed). If blank or omitted,
      falls back to generate_paste_batch_name (format: <Selected Course> - YYYY-MM-DD HH:mm:ss).
    - Names are left blank (name="").
    """
    from django.core.cache import cache
    from .courses import course_label, validate_course
    from .phone_normalization import parse_pasted_phone_numbers

    import hashlib
    user_id = getattr(imported_by, 'pk', None) or 'anon'
    cache_key = f"paste_import:idempotency:{user_id}:{idempotency_key}" if idempotency_key else None
    payload_hash = None
    custom_batch_name = str(batch_name).strip() if batch_name is not None else ""

    if idempotency_key:
        caller_id = getattr(assigned_caller, 'pk', '') if assigned_caller else ''
        payload_signature = f"{str(pasted_text).strip()}|{preferred_course}|{preferred_course_custom}|{caller_id}|{custom_batch_name}"
        payload_hash = hashlib.sha256(payload_signature.encode('utf-8')).hexdigest()

        cached_entry = cache.get(cache_key)
        if cached_entry is not None:
            if isinstance(cached_entry, dict) and 'payload_hash' in cached_entry:
                if cached_entry['payload_hash'] != payload_hash:
                    raise ValueError("Idempotency key reused with different request payload.")
                logger.info("Idempotency hit for user=%s key=%s", user_id, idempotency_key)
                return cached_entry['result']
            logger.info("Idempotency hit for user=%s key=%s", user_id, idempotency_key)
            return cached_entry

    User = get_user_model()
    if assigned_caller:
        from apps.accounts.capabilities import is_caller_capable
        if not isinstance(assigned_caller, User) or not is_caller_capable(assigned_caller):
            raise ValueError("Selected user is not an active caller.")

    preferred_course, preferred_course_custom = validate_course(
        preferred_course, preferred_course_custom, required=True
    )
    label = course_label(preferred_course, preferred_course_custom)
    parsed = parse_pasted_phone_numbers(pasted_text)
    valid_numbers = parsed["valid_numbers"]
    invalid_entries = parsed["invalid_entries"]
    submitted_count = parsed["total_submitted"]

    if not valid_numbers:
        return {
            "success": False,
            "batch_id": None,
            "batch_name": None,
            "course_label": label,
            "submitted_count": submitted_count,
            "created_count": 0,
            "duplicate_count": 0,
            "skipped_count": len(invalid_entries),
            "invalid_entries": invalid_entries,
            "duplicate_entries": [],
            "created_ids": [],
        }

    existing_phone_map = get_existing_phone_map()
    seen_in_paste = set()
    leads_to_create = []
    duplicate_entries = []

    for phone in valid_numbers:
        if phone in seen_in_paste:
            duplicate_entries.append({
                "phone": phone,
                "reason": "Duplicate within pasted list",
            })
        else:
            existing_lead = check_duplicate_from_map(phone, existing_phone_map)
            if existing_lead:
                duplicate_entries.append({
                    "phone": phone,
                    "reason": "Already exists in CRM",
                })

        seen_in_paste.add(phone)
        leads_to_create.append(phone)
        existing_phone_map[phone] = True
        from .phone_normalization import get_phone_lookup_variants
        for v in get_phone_lookup_variants(phone):
            existing_phone_map[v] = True
            v_digits = normalize_phone(v)
            if v_digits:
                existing_phone_map[v_digits] = True

    if not leads_to_create:
        result = {
            "success": True,
            "batch_id": None,
            "batch_name": None,
            "course_label": label,
            "submitted_count": submitted_count,
            "created_count": 0,
            "duplicate_count": len(duplicate_entries),
            "skipped_count": len(invalid_entries),
            "duplicate_entries": duplicate_entries,
            "invalid_entries": invalid_entries,
            "created_ids": [],
        }
        if idempotency_key and cache_key:
            cache.set(cache_key, {'payload_hash': payload_hash, 'result': result}, timeout=86400)
        return result

    if custom_batch_name:
        final_batch_name = custom_batch_name
    else:
        final_batch_name = generate_paste_batch_name(preferred_course, preferred_course_custom)

    with transaction.atomic():
        batch = LeadImportBatch.objects.create(
            filename=final_batch_name,
            imported_by=imported_by,
            total_rows=len(leads_to_create),
            created_count=0,
            duplicate_count=len(duplicate_entries),
            warning_count=len(invalid_entries),
        )
        created_ids = []
        for phone in leads_to_create:
            lead = Lead(
                import_batch=batch,
                preferred_course=preferred_course,
                preferred_course_custom=preferred_course_custom,
                name="",
                phone=phone,
                status=Lead.Status.PENDING,
            )
            lead._changed_by = imported_by
            lead.save()
            created_ids.append(lead.pk)

        if assigned_caller and created_ids:
            bulk_assign_leads(
                created_ids,
                assigned_caller,
                imported_by,
                reason=f"Assigned during paste import {batch.pk}",
                notify=True,
            )

        batch.created_count = len(created_ids)
        batch.save(update_fields=["created_count"])

    result = {
        "success": True,
        "batch_id": batch.id,
        "batch_name": batch.filename,
        "course_label": label,
        "submitted_count": submitted_count,
        "created_count": len(created_ids),
        "duplicate_count": len(duplicate_entries),
        "skipped_count": len(invalid_entries),
        "duplicate_entries": duplicate_entries,
        "invalid_entries": invalid_entries,
        "created_ids": created_ids,
    }
    if idempotency_key and cache_key:
        cache.set(cache_key, {'payload_hash': payload_hash, 'result': result}, timeout=86400)
    return result
