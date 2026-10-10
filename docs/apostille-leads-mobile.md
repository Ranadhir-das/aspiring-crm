# Apostille enquiries and counselor authentication

## Existing architecture inspected

- `/api/v1/mobile/login/`, `login/verify/`, `me/`: User roles, AttendancePhotoChallenge, approved enrollment, CallerSession and VerifiedSessionAuthentication. Active counselors are already exempt from photo attendance, but the pending-enrollment check ran before the exemption.
- `/api/v1/mobile/leads/`, `<id>/`, `<id>/update/`: assigned caller Lead access.
- `/api/v1/leads/bulk-assign/` and CRM lead assignment: bulk_assign_leads, Lead.assigned_caller, LeadAssignmentHistory. Website claiming separately uses LeadAvailability.
- `/api/v1/calls/`, `resolve/`, lead history and `mine/`: Call, idempotent client_event_id, outcome/status synchronization, FollowUp.
- `leads.Apostille` and `/apostilles/`: paid document work with amount_received, caller points and financial reconciliation. This must not become an enquiry or grant points from a Yes/No conversion choice.
- User.services through EmployeeService already identifies service eligibility; designation is free text and is not an authorization boundary.

## Implementation contract

Use the existing Lead with an optional one-to-one ApostilleLeadDetails extension. No second lead database, dialer, assignment or points system. Active CALLER users mapped to active APOSTILLE can use the feature; ADMIN/SUPER_ADMIN manage it in CRM. Caller-created leads await manual assignment. Creators can see their own unassigned enquiries; after reassignment only the current assignee can see them. Calls require assignment (or the existing website claim).

Authenticated `/api/v1/mobile/apostille-leads/` supports GET/POST; `<id>/` supports GET/PATCH. Create requires name, phone, country, document_name, number_of_documents, conversion and client_event_id (UUID). No-conversion requires reason; Yes accepts notes. Inactive conditional fields are cleared. Replays of identical event data return the same lead; conflicting reuse/duplicate phone returns 409 without disclosing another caller's lead. PATCH requires the returned revision and complete business fields, preventing lost updates.

The existing call API accepts Converted and Follow-up Required only for Apostille leads. Follow-up Required uses the existing callback_at/FollowUp flow. Interested on Apostille does not require an academic course/year. Conversion remains an independent enquiry field and does not create paid Apostille records or points.

Account approval remains mandatory. Pending counselor registration returns PENDING without creating a photo challenge; an active approved counselor's obsolete pending photo no longer blocks sign-in. Challenge expiry, replay protection, token rotation and work sessions remain unchanged.
