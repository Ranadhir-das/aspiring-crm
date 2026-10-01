# Phase 4: Website lead call outcomes and availability lifecycle

Following Phase 3's explicit claiming model, Phase 4 connects call outcomes submitted
by callers to automatic lead status and queue availability transitions.

## Overview

When an active caller claims a website lead, the lead is assigned to them
(`lead.assigned_caller = caller`, `availability.claimed_by = caller`,
`availability.claimed_at = lead.assigned_at = now`). The caller dials the lead and
submits the call outcome via the existing `POST /api/v1/calls/` endpoint.

All outcome processing is handled atomically within `CallCreateView` with a
PostgreSQL row lock on the Lead record.

## Authorization & Caller Isolation

- **Assigned Claim Enforcement**: Callers can only submit calls for leads they
  currently own (`lead.assigned_caller_id == request.user.pk`). Submissions for
  unclaimed website leads or leads owned by another caller return HTTP 404.
- **Direct Dial Protection**: Resolving by phone number verifies caller ownership via
  `matching_lead`, returning `PermissionDenied` if the phone belongs to an unassigned
  lead or a lead assigned to a different caller.
- **Idempotency & Replay**: Calls use `client_event_id` and submission fingerprints.
  Replaying an already-processed call returns HTTP 200 without duplicating records or
  re-triggering outcome state changes. Once a lead has been released by a retry outcome,
  the previous owner cannot submit new calls for it without claiming it again.

## Outcome Rules & Availability Lifecycle

### 1. INTERESTED
- Lead status updates to `INTERESTED`.
- Caller retains ownership (`assigned_caller` and `availability.claimed_by` remain set).
- Excluded from all other callers' available queues.

### 2. NOT_INTERESTED
- Lead status updates to `NOT_INTERESTED`.
- Caller retains ownership; lead is closed and excluded from all available queues.

### 3. WRONG_NUMBER
- Lead status updates to `WRONG_NUMBER`.
- Caller retains ownership; lead is closed and excluded from all available queues.

### 4. CALL_BACK
- Lead status updates to `CALL_BACK`.
- Caller retains ownership (`assigned_caller` remains set).
- Lead remains removed from the fresh available queue.
- Prior pending follow-ups for this lead and caller are marked `CANCELLED`.
- A new `FollowUp` is created with the required `callback_at` timestamp.
- The caller accesses and manages this follow-up through `/api/v1/mobile/followups/`.

### 5. NO_ANSWER / BUSY (Retry Queue)
- Lead status updates to `NO_ANSWER` or `BUSY`.
- The lead is released back to the eligible caller queue after a configurable cooldown:
  - `BUSY`: `settings.WEBSITE_LEAD_BUSY_RETRY_SECONDS` (default: 900 seconds / 15 minutes)
  - `NO_ANSWER`: `settings.WEBSITE_LEAD_NO_ANSWER_RETRY_SECONDS` (default: 3600 seconds / 1 hour)
- Database updates:
  - `availability.available_at = now + delay`
  - `availability.retry_count += 1`
  - `availability.claimed_by = None`, `availability.claimed_at = None`
  - `lead.assigned_caller = None`, `lead.assigned_at = None`
  - A `LeadAssignmentHistory` entry is created with reason `"Website retry scheduled: <OUTCOME>"`.
- **Zero Duplication**: The existing `Lead` and `LeadAvailability` records are updated in place; no duplicate rows are created.
- **Cooldown Isolation**: While `now < available_at`, the lead is hidden from `/api/v1/mobile/leads/available/` and claims return HTTP 409. Once `available_at` passes, the lead re-appears in the available queue for all active callers mapped to the lead's service.

## Manual Assignment & Batch Import Compatibility

- CSV/XLSX imports and manual assignments are completely decoupled from website outcome retry logic.
- When an administrator manually assigns or reassigns a lead (`bulk_assign_leads`), `lead.assigned_at` is updated to the assignment time. Because `availability.claimed_at != lead.assigned_at`, subsequent call outcomes (`BUSY`, `NO_ANSWER`, etc.) retain manual ownership without triggering website cooldown releases.
- All 13 call outcomes continue to synchronize with `Lead.status` as before.

## Configuration Settings

In `config/settings.py` (and `.env`):
- `WEBSITE_LEAD_BUSY_RETRY_SECONDS`: Seconds to wait before releasing a BUSY lead (default: 900).
- `WEBSITE_LEAD_NO_ANSWER_RETRY_SECONDS`: Seconds to wait before releasing a NO_ANSWER lead (default: 3600).

## Verification

Run test suite:
```bash
python manage.py test apps.leads.test_website_outcomes
python manage.py test
```
All 185 tests pass across calls, leads, web, accounts, followups, and admissions.
