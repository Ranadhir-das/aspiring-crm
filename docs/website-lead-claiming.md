# Phase 3: shared website queues and explicit claiming

New leads created by `POST /api/v1/public/leads/` receive one `LeadAvailability`
record in the same transaction as the Lead. No duplicate Lead or per-caller copy
is created. A lead appears in the queues of all active CALLER users currently
mapped to its active Service, provided it is unassigned and has never been
claimed. This is availability routing, not assignment to an employee.

No caller mappings means an empty audience; the lead remains stored. Adding an
eligible caller later makes it visible. Removing a mapping or disabling the
service removes unclaimed leads from that caller's queue. Such changes do not
revoke ownership of previously claimed or manually assigned leads.

## Endpoints

Both use existing `Authorization: Token ...` authentication with a verified,
unexpired work session. Anonymous/expired sessions receive 401; inactive users
or non-CALLER roles cannot use these endpoints. No caller-ID query parameter is
accepted to select someone else's queue.

### GET /api/v1/mobile/leads/available/

Returns an array, oldest first:

```json
[
  {
    "id": 123,
    "name": "Example Visitor",
    "service": {"id": 1, "name": "MBBS", "code": "MBBS", "description": ""},
    "source": "website",
    "campaign": "September admissions",
    "created_at": "2026-09-28T06:00:00Z"
  }
]
```

Contact details and notes are omitted until a successful claim. This endpoint
is separate from the existing assigned-lead list; that list is unchanged.

### POST /api/v1/mobile/leads/123/claim/

No request body is needed. Caller identity always comes from the authenticated
user, never from client-supplied ownership fields.

HTTP 200 returns `claimed_by`, `claimed_at` and `lead` (the existing mobile lead
serializer, including the phone). A successful claim sets `assigned_caller` and
`assigned_at` on Lead, sets the claim fields on its availability record, and
creates one existing `LeadAssignmentHistory` entry. The lead disappears from
every available queue and appears in the winner's normal assigned-lead list.

- Repeat by the same claimant/current owner: 200, original timestamp, no extra
  history record. This supports recovery from a lost success response.
- Another eligible caller or a manually assigned lead with an availability
  record: 409 with a conflict message, no owner/contact details.
- Caller lacks an active matching service: 403.
- Lead does not exist or has no website availability record: 404.

The frontend should show available summaries, POST the claim on an explicit
Claim action, and enable dialing only after a 200 response. On 409 refresh the
queue. These changes provide the backend contract; they do not add a mobile
screen. The API cannot prevent someone independently dialing a number through
the device's Phone app, but CRM call creation/preflight and lead access enforce
the existing assigned-owner permission, including phone-only call requests.

### POST /api/v1/mobile/leads/123/call-started/

Mark that an outgoing phone call has reached `OFFHOOK` on the holding caller's device.
Transitions the claim from a temporary claim to active real-call ownership.

- Caller identity is determined from authentication token.
- Only the holding caller can call this endpoint; another caller receives 403.
- If the lead is not claimed or already released, returns 409.
- Idempotent: repeated calls by the holding caller return 200 with the existing timestamp.
- Once marked, `release-claim` will reject any attempt to release this lead.

Response (200 OK):
```json
{
  "call_started": true,
  "lead_id": 123,
  "call_started_at": "2026-09-29T10:15:00Z"
}
```

### POST /api/v1/mobile/leads/123/release-claim/

Releases a temporary website lead claim before any call is made (e.g. if the caller cancels
before Android reaches `OFFHOOK` or dialer launch fails).

- Allowed only for the caller who currently holds the temporary claim; another caller receives 403.
- If lead is not claimed, returns 409.
- If a real call has started (`call-started` was called, or a Call record exists, or lead status is not PENDING), returns 409 (`Cannot release claim after a real call has started`).
- Manually or batch assigned leads without website availability tracking cannot be released through this flow (returns 403).
- Atomically clears `assigned_caller`, `assigned_at`, `availability.claimed_by`, `availability.claimed_at`, `availability.call_started_at`, and ensures `status=PENDING`.
- Creates a `LeadAssignmentHistory` entry and logs `LEAD_CLAIM_RELEASED_BEFORE_CALL`.
- Does not create any `Call` or `FollowUp` records.

Response (200 OK):
```json
{
  "released": true,
  "status": "PENDING",
  "lead_id": 123,
  "message": "Lead claim released successfully."
}
```

## Concurrency and compatibility

Claiming locks the caller first (NO KEY UPDATE, compatible with manual assignment's
foreign-key checks), then takes a PostgreSQL row lock on the Lead, using the
same row as existing bulk manual assignment and call creation. Eligibility and
current ownership are checked after acquiring the lock. Claim metadata,
assignment and history commit together; failure rolls them back together.
Two concurrent eligible claimants receive one 200 and one 409.

The availability record is an explicit origin marker. No old/imported/manual
leads are backfilled into queues, even when their `source` says `website`.
Duplicate website submissions do not add availability to existing leads or
change their service/owner. The migration only adds a table.

CSV/XLSX import, quick entry, manual assignment and existing lead APIs keep their
existing behavior. If normal manual assignment wins the row lock, a claim gets
409. If a claim wins, normal assignment skips the already assigned lead.
An administrator's explicit manual reassignment (`reassign=True`) continues to
work: `assigned_caller` is always the authoritative current owner. The original
claim metadata remains historical and does not let an old claimant regain access.

Deleting a claimant sets `claimed_by` to null but retains `claimed_at`, so it does
not silently release the lead back into queues. There is no release, retry
schedule, automatic expiry, claim outcome logic or reassignment policy added by
this phase. No existing outcome logic was modified.

## Deployment and verification

Run `python manage.py migrate` (new migration:
`leads.0009_website_lead_availability`) and restart the backend.

Run `python manage.py check`, `python manage.py makemigrations --check --dry-run`,
and `python manage.py test`. `apps.leads.test_claiming` includes real concurrent
PostgreSQL claim/manual-assignment tests, caller/service isolation, verified
authentication, rollback, duplicate handling, deleted-owner behavior and existing
call API ownership checks. Existing import/manual-assignment and Phase 1/2 tests
remain part of the full suite.
