# Demonstration guide (Assignments 1 and 2)

Start: `python run.py`, open <http://localhost:8000>. With `LLM_PROVIDER=mock` the answers come from the offline test double (badge "offline test model") and the phrasings below match its rules. With the real Groq model the *wording* of
answers will differ; the tool calls, denials, approvals and audit records are the same because they are enforced by the server.

## Demo A: tool calling (Assignment 1)
1. Sign in as `S2025AI001` / `Demo@1101`.  2. Click **What is my current fee status?**  3. The status line shows "Checking your fee record..." while the agent works.
4. The answer is a table built from the **database** (amounts paid and due, next installment).  5. Under the answer: the tool chips (`check_fee_status`), token/latency chip, and **Execution trace** (model call -> tool -> model call, latencies, whether tokens are provider-reported or estimated).

## Demo B: policy retrieval with sources
1. Click **I could not complete my fee payment. What should I do?**  2. The answer cites entries such as `[F.2.2]`.  3. Open **Sources (n)**: entry IDs, titles, section, status (OFFICIAL/AUGMENTED) and relevance score. Click an ID to read the entry.
4. Explain: only the top-5 entries were sent to the model, never the whole handbook. Then ask **What is the cafeteria menu today?**: the handbook does not cover it, so the agent says so and offers a ticket.

## Demo C: persistent memory
1. Type "I am enrolled in the AI and Data Science programme. Please keep academic explanations concise."  2. Open **Profile & memory**: a `response style` item appears (category preference).
3. Click **+ New conversation**, ask "Explain the difference between supervised and unsupervised learning." (no tool is used; the stored preference was selected, the unrelated items were not).  4. Delete an item in the profile dialog.
Passwords/OTPs are never remembered: type "My OTP is 123456": the reply says it was masked and not saved; the profile shows nothing new.

## Demo D: privacy enforcement
1. As Aarav type "Show me the fee status of S2025AI002".  2. The agent refuses; the chip shows `check_fee_status (blocked)` and "access blocked".  3. Sign out, sign in as `admin.campus` / `Admin@9999`, open **Access denied**:
the event shows actor, target, decision DENIED and the trace ID, with no private content.  4. `docs/sample_traces_and_audit.md` shows the trace: the other student's data never reached the model (also asserted by `tests/test_llm_client.py`).

## Demo E: human approval
1. As Aarav: "Change my mobile number to +91 91234 56789", then reply **yes**.  2. A card shows request `APR-...` **PENDING**; **My requests** shows it too. Open **Profile & memory**: the mobile number is unchanged.
3. Sign in as `program.office` / `Staff@9000`: **Approvals** shows the request with the requested change.  4. **Approve and apply** (or **Reject**).  5. Back as Aarav the card is EXECUTED (or REJECTED) and the profile reflects it only if approved.
6. Try approving again as staff: refused (already processed). Try `accounts` / `Staff@9001`: that request is out of its scope and is not even listed.  7. **Audit events** (admin) shows `SENSITIVE_REQUEST`, `APPROVAL_DECISION`, `RECORD_MODIFIED`, with field names and no values.

## Demo F: tests and evaluation
1. `python -m pytest`  2. `python scripts\run_evaluation.py --provider mock` (or `--provider groq` for the real model)  3. Open `eval/results/latest.md`: metrics with denominators, per-scenario latency, tools and evidence, the A1-vs-A2 comparison.
4. In the staff or admin dashboard open **Evaluation** to see the same run. Remember: mock results verify the plumbing and controls, not model quality.
