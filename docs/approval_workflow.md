# Human approval workflow

Sensitive changes are never applied by the agent. They are *requested*, then a person with the right scope decides. All of it is enforced in code (`services/approval_service.py`); the model has no say.

## State machine
```
PENDING --reject------------------------------> REJECTED   (final; nothing executed)
PENDING --approve--> APPROVED --executes ok----> EXECUTED   (final; applied exactly once)
                              \--execution error-> FAILED   (final; NOTHING applied, no partial write)
PENDING --not decided before expires_at--------> EXPIRED    (final)
PENDING --proposal hash mismatch---------------> FAILED    (final; integrity check)
```
Terminal states have no exits (`approval_service.TRANSITIONS`, tested). A failed or expired request is never retried: the student files a new one.

## Rules enforced
| Rule | Mechanism | Test |
|---|---|---|
| Only a student requests, only for their own record | `create_request` + `resolve_student_target` | `test_only_students_request_changes_to_their_own_record`, scenario 13 |
| The requester can never decide their own request | `can_decide_approval` (explicit `requester_id == actor`), plus role checks | `test_requester_can_never_decide_their_own_request`, scenario 17 |
| Approvers act only inside their scope; out-of-scope staff learn nothing | `staff_scope.approval_actions`; "not found" for out-of-scope | `test_approver_scope_is_enforced_for_listing_and_deciding` |
| The proposal cannot be altered after submission | SHA-256 of (action, requester, target, change) stored at creation and re-verified before executing | `test_a_tampered_proposal_is_never_executed` |
| A request cannot be executed twice | Atomic `UPDATE ... WHERE status='PENDING'` (compare-and-set) + SQLite `BEGIN IMMEDIATE` so writers queue | `test_simultaneous_approvals_execute_exactly_once`, scenario 18 |
| Expired requests cannot be approved | `expires_at` (default 72 h), lazily marked EXPIRED | `test_expired_requests_cannot_be_approved` |
| Change + status commit together; a failure leaves no partial write | Executor runs in a savepoint; on error the savepoint rolls back, then FAILED is recorded | `test_partial_writes_are_rolled_back_when_execution_fails_midway` |
| Failed execution is never recorded as success | status `FAILED`, generic reason (`execution failed: <ExceptionType>`) | `test_failed_execution_is_recorded_as_failed_and_changes_nothing` |
| Everything is audited without values | `SENSITIVE_REQUEST`, `APPROVAL_DECISION`, `RECORD_MODIFIED` rows with field names only | `test_each_approved_change_is_audited_exactly_once_with_field_names_only` |

## Actions, validation and approvers
| Action | Fields | Pre-checks (re-checked at execution) | Approver scope |
|---|---|---|---|
| `UPDATE_CONTACT` | mobile / email / address (at least one; format-validated) | email not already used | Program Office |
| `UPDATE_REFUND_BANK` | `bank_name`, `account_last4` (4 digits only; a full number is rejected) | n/a | Accounts |
| `SWITCH_PROGRAMME` | `target_programme` | not already in that programme | Program Office |
| `WITHDRAW_SCHOLARSHIP` | none | an active scholarship/application exists | Program Office, Accounts |
| `CHANGE_COURSE` | `from_course`, `to_course` | from-course is a *registered elective*; to-course is in the elective basket and not already registered; core courses cannot be swapped | Program Office |
| `FEE_CORRECTION` | `installment_name`, `new_late_fee` | installment exists; fee record is recomputed afterwards | Accounts |
Admins may decide any request (never their own). Anything not listed (tickets, preferences, reads) is non-sensitive and runs immediately.

## Adding an action
Add the Pydantic model in `schemas.py` (`CHANGE_MODELS`), an entry in `catalog.SENSITIVE_ACTIONS`, a pre-check in `_precheck`, an executor in `approval_service.EXECUTORS`, and add it to the right staff scope in `seed_service.py`. Add a test next to the six in `tests/test_approvals.py`.
