# Privacy and access rules (what the code enforces)

| Who | May | May not |
|---|---|---|
| **Student** | see and request changes to **their own** profile, fees, scholarship, tickets, conversations, memory; search the handbook; raise tickets; see their own approval requests | see or change another student's data (read or write), approve anything, read audit logs or traces, use admin/staff endpoints |
| **Staff** (Program Office, Accounts) | list and decide approval requests **in their scope**; see and update tickets in their categories; read operational audit events (denials, tool failures, approvals, record changes); read traces and the evaluation report | chat as a student, see student records beyond what a request shows, read login/record-access audit events, decide a request outside scope or their own |
| **Administrator** | everything staff can, across all scopes; manage users (role, active); read all audit events; run the retention purge | change their own role or active status |

* Identity comes only from the server-side session. A `student_id` supplied by a browser or a model is never trusted.
* A refused request is indistinguishable from a request for something that does not exist (conversations, memory items, tickets, approvals), so existence is not leaked.
* The model never receives another person's data: authorization runs before the data is read (`tests/test_llm_client.py::test_a_tool_call_for_another_students_record_is_refused_and_nothing_reaches_the_provider`).
* Secrets (OTP, passwords, PINs, CVV, API keys, tokens, card/Aadhaar numbers, URL credentials) are masked before processing and are never stored, logged, traced, or sent to the model.
* Audit rows record who did what to which resource and the outcome, never the values.
