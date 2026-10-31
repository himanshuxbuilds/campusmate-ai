"""OFFLINE TEST DOUBLE (LLM_PROVIDER=mock). A deterministic rule-based planner behind the same interface as the real (Groq) client.

It exists so the whole stack (agent loop, tools, authorization, approvals, tracing, memory, RAG) can be tested without network
access or an API key. It is NOT a language model: results obtained with it prove the plumbing, never model quality or tool-selection
quality. Its token counts are local estimates and are flagged `estimated`. It is never selected silently.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from app.services import catalog
from app.services.llm_client import LLMResponse, ToolCall, estimate_tokens
from app.services.memory_service import extract_with_rules

_SID = re.compile(r"\bS\d{4}[A-Z]{2}\d{3}\b")
_YES = re.compile(r"^\s*(yes|yes please|confirm|confirmed|i confirm|go ahead|proceed|do it|sure)\b", re.I)
_NO = re.compile(r"^\s*(no|cancel|stop|never mind|nevermind)\b", re.I)
_PRIVATE = re.compile(r"\b(fee|fees|dues|scholarship|record|profile|attendance|cgpa|phone|mobile|email|address|details|data|ticket|grade|grades)\b", re.I)
_POLICY = re.compile(r"\b(policy|policies|rule|rules|slab|per day|portal|working day|waive|waiver|extension|how is|what happens|could not|couldn't|failed|failure|procedure|process|"
                     r"documents? (?:are )?required|how (?:do|can|to)|deadline|criteria|types of|what scholarships)\b", re.I)
_DOMAIN = re.compile(r"\b(fee|fees|installment|scholarship|exam|exams|trimester|semester|hostel|mess|library|attendance|placement|internship|capstone|policy|handbook|course|courses|"
                     r"subject|subjects|ticket|support request|refund|calendar|holiday|grade|cgpa|aidtm|campus|admission|loan|deadline|elective|programme|program|certificate|"
                     r"transcript|bonafide|ragging|counsel|insurance|wifi|wi-fi|plagiarism|late fee|payment|portal|pay|paying|paid|due|working day|penalty|fine|fines|"
                     r"hall ticket|conduct|discipline|grievance|complaint|safety|dress code|mobile phone|convocation|diploma|bridge course|transport|cafeteria|canteen|menu|food|"
                     r"parking|gym|sports|bus|shuttle|canteen|medical|hospital|alumni|club|clubs)\b", re.I)
_NOT_NAMES = {"i", "my", "me", "the", "a", "an", "trimester", "python", "sql", "ai", "bda", "pgdm", "march", "april", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
_GLOSSARY = {
    "supervised": ("Supervised learning trains a model on labelled examples (inputs with known answers) to predict outputs, e.g. classification and regression. "
                   "Unsupervised learning finds structure in unlabelled data, e.g. clustering and dimensionality reduction.",
                   "Supervised learning learns from labelled data to predict a known target (classification, regression). Unsupervised learning has no labels and discovers structure, such as clusters."),
    "machine learning": ("Machine learning is the study of algorithms that improve at a task by learning patterns from data rather than following hand-written rules. "
                         "Common types are supervised, unsupervised and reinforcement learning, evaluated on data the model has not seen.",
                         "Machine learning is learning patterns from data to make predictions or decisions without explicit rules."),
    "deep learning": ("Deep learning uses multi-layer neural networks that learn features automatically from large datasets, powering image, speech and language models.",
                      "Deep learning is machine learning with multi-layer neural networks."),
}


class MockClient:
    name = "mock"
    model = "mock-planner-v3"

    # ----------------------------------------------------------------------------- plumbing
    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> LLMResponse:
        t0 = time.perf_counter()
        system = messages[0]["content"]
        results = self._results(messages)
        text = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        if results:
            return self._reply(messages, self._answer(results), t0)
        plan = self._plan(text, messages, system)
        if isinstance(plan, str):
            return self._reply(messages, plan, t0)
        calls = [ToolCall(id=f"call_{i + 1}", name=n, arguments=a, raw_arguments=json.dumps(a)) for i, (n, a) in enumerate(plan)]
        am = {"role": "assistant", "content": "", "tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.raw_arguments}} for c in calls]}
        return LLMResponse(tool_calls=calls, input_tokens=estimate_tokens(messages), output_tokens=estimate_tokens(am), estimated=True, model=self.model,
                           latency_ms=round((time.perf_counter() - t0) * 1000), assistant_message=am)

    def _reply(self, messages: list[dict[str, Any]], text: str, t0: float) -> LLMResponse:
        return LLMResponse(text=text, input_tokens=estimate_tokens(messages), output_tokens=estimate_tokens(text), estimated=True, model=self.model,
                           latency_ms=round((time.perf_counter() - t0) * 1000), assistant_message={"role": "assistant", "content": text})

    @staticmethod
    def _results(messages: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
        out = []
        for m in reversed(messages):
            if m["role"] == "user":
                break
            if m["role"] == "tool":
                try:
                    out.append((m.get("name", "?"), json.loads(m["content"])))
                except (json.JSONDecodeError, TypeError):
                    out.append((m.get("name", "?"), {"success": False, "error": {"code": "BAD_RESULT", "message": "unreadable tool result"}}))
        out.reverse()
        return out

    @staticmethod
    def _memory(system: str) -> dict[str, str]:
        block = system.split("<memory>")[-1].split("</memory>")[0]
        return {m.group(1): m.group(2) for m in re.finditer(r"^- (\w+): (.+)$", block, re.M)}

    # ----------------------------------------------------------------------------- planning
    @staticmethod
    def _sensitive(text: str) -> dict[str, Any] | None:
        t = text.strip()
        if m := re.search(r"(?:change|update|set)\s+(?:my\s+|the\s+)?(?:registered\s+)?(?:mobile|phone|contact)(?:\s+number)?(?:\s+of\s+\S+)?\s+(?:to|as)\s+(\+?\d[\d\s-]{7,18}\d)", t, re.I):
            return {"action_type": "UPDATE_CONTACT", "changes": {"mobile": m.group(1).strip()}}
        if m := re.search(r"(?:change|update|set)\s+(?:my\s+)?(?:registered\s+)?email(?:\s+address)?\s+(?:to|as)\s+([^\s,]+@[^\s,]+)", t, re.I):
            return {"action_type": "UPDATE_CONTACT", "changes": {"email": m.group(1).strip()}}
        if m := re.search(r"(?:change|update|set)\s+(?:my\s+)?address\s+(?:to|as)\s+(.+)$", t, re.I):
            return {"action_type": "UPDATE_CONTACT", "changes": {"address": m.group(1).strip().rstrip(".")}}
        if m := re.search(r"(?:change|update)\s+(?:my\s+)?(?:refund\s+)?bank\s+account\s+to\s+([A-Za-z][A-Za-z &.]{1,30}?)\s+(?:ending|last\s*4|last four)?\s*(?:in|with|digits)?\s*(\d{4})\b", t, re.I):
            return {"action_type": "UPDATE_REFUND_BANK", "changes": {"bank_name": m.group(1).strip().title(), "account_last4": m.group(2)}}
        if m := re.search(r"(?:switch|change|move|transfer)\s+(?:my\s+)?(?:programme|program|course|branch)?\s*(?:to|into)\s+(?:pgdm[- ]?)?(ai\s*&?\s*ds|aids|ai and data science|bda|big data analytics)\b", t, re.I):
            return {"action_type": "SWITCH_PROGRAMME", "changes": {"target_programme": "PGDM-AIDS" if "ai" in m.group(1).lower() else "PGDM-BDA"}}
        if re.search(r"withdraw\s+(?:my\s+)?scholarship", t, re.I):
            return {"action_type": "WITHDRAW_SCHOLARSHIP", "changes": {}}
        if m := re.search(r"change\s+(?:my\s+)?(?:registered\s+)?elective\s+from\s+(.+?)\s+to\s+(.+?)\s*[.?!]*$", t, re.I):
            return {"action_type": "CHANGE_COURSE", "changes": {"from_course": m.group(1).strip(), "to_course": m.group(2).strip()}}
        if m := re.search(r"(?:correct|waive|reduce)\s+(?:the\s+)?late\s+fee\s+(?:on|for)\s+(?:my\s+)?(.+?)\s+(?:to|as)\s+(\d+)", t, re.I):
            return {"action_type": "FEE_CORRECTION", "changes": {"installment_name": m.group(1).strip().lower(), "new_late_fee": int(m.group(2))}}
        return None

    @staticmethod
    def _category(t: str) -> str:
        for cat, words in (("FEES", ("fee", "payment", "portal", "receipt", "installment")), ("SCHOLARSHIP", ("scholarship",)), ("LIBRARY", ("library", "book")),
                           ("TECHNICAL_SUPPORT", ("login", "wifi", "wi-fi", "password", "lms")), ("HOSTEL", ("hostel", "mess", "room")), ("EXAMINATION", ("exam", "hall ticket")),
                           ("DOCUMENTS", ("certificate", "bonafide", "transcript", "letter")), ("ATTENDANCE", ("attendance",)), ("ACADEMICS", ("grade", "marks", "cgpa")),
                           ("PLACEMENT", ("placement", "internship")), ("COURSE_CHANGE", ("elective", "course change"))):
            if any(w in t for w in words):
                return cat
        return "OTHER"

    def _plan(self, text: str, messages: list[dict[str, Any]], system: str) -> list[tuple[str, dict[str, Any]]] | str:
        t = text.lower().strip()
        mem = self._memory(system)
        own = (re.search(r"Signed-in student: (.+?) \(", system) or [None, ""])[1]
        users = [m["content"] for m in messages if m["role"] == "user"]
        prev_user = users[-2] if len(users) >= 2 else ""
        prev_assistant = next((m["content"] for m in reversed(messages[:-1]) if m["role"] == "assistant" and m.get("content")), "")
        concise = "concise" in mem.get("response_style", "").lower() or "brief" in mem.get("response_style", "").lower()

        if "[redacted" in t:
            return ("For your safety I never ask for, use or store OTPs, passwords, PINs, API keys or card numbers, so the value you typed was masked before it reached me and it was not "
                    "saved. If you shared it by mistake, change it now and contact the IT Officer (Mr. Jignesh Parekh) [N.1.3].")
        if "reply yes to confirm" in prev_assistant.lower():
            if _YES.search(t) and (intent := self._sensitive(prev_user)):
                return [("request_sensitive_change", {**intent, "reason": "requested in chat"})]
            if _NO.search(t):
                return "Okay, I cancelled that request. Nothing was changed."
        if re.search(r"\b(forget|delete|remove)\b.*\b(memory|remember|preference|everything you know|what you know)", t):
            return [("forget_memory", {})]
        if re.search(r"\b(what do you remember|what did i tell you|what name should you|what (?:contact )?preference did i|do you remember|what do you know about me)", t):
            return ("Here is what I have stored about you: " + "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in mem.items()) + ". You can review or delete these in your profile."
                    ) if mem else "I have nothing stored about you yet. Tell me a preference and I will remember it for future conversations."
        calls: list[tuple[str, dict[str, Any]]] = [("save_memory", {"key": k, "value": v}) for k, v in extract_with_rules(text)]

        sid = _SID.search(text)
        sens = self._sensitive(text)
        if sens and sid and sid.group(0).lower() != "":                                   # attack path: change someone else's record
            return [("request_sensitive_change", {**sens, "student_id": sid.group(0), "reason": "requested in chat"})]
        other = sid.group(0) if sid else None
        if not other and re.search(r"\b(all students|every student|everyone'?s|each student)\b", t):
            other = "ALL"
        if not other and _PRIVATE.search(text):
            for m in re.finditer(r"\b([A-Z][a-z]{2,}(?: [A-Z][a-z]+)?)(?:'s|’s)\s+\w+|\b(?:of|for|about)\s+([A-Z][a-z]{2,}(?: [A-Z][a-z]+)?)\b", text):
                nm = (m.group(1) or m.group(2) or "").strip()
                if nm and nm.split()[0].lower() not in _NOT_NAMES and nm.lower() != own.lower() and nm.split()[0].lower() != own.split()[0].lower():
                    other = nm
                    break
        if other and _PRIVATE.search(text):
            tool = "check_scholarship_status" if "scholarship" in t else ("get_student_profile" if re.search(r"profile|attendance|cgpa|phone|mobile|email|address|details|data", t) else "check_fee_status")
            return calls + [(tool, {"student_id": other})]

        if sens:
            desc = {"UPDATE_CONTACT": "update your registered contact details", "UPDATE_REFUND_BANK": "change your refund bank account", "SWITCH_PROGRAMME": "switch your programme",
                    "WITHDRAW_SCHOLARSHIP": "withdraw your scholarship application", "CHANGE_COURSE": "change your registered elective", "FEE_CORRECTION": "request a late-fee correction"}[sens["action_type"]]
            detail = ", ".join(f"{k}: {v}" for k, v in sens["changes"].items())
            return (f"This is a sensitive change to your record. I will {desc}" + (f" ({detail})" if detail else "") + ". It will be sent to the Program Office for human approval "
                    "and applied only if approved [N.1.2]. Reply YES to confirm, or NO to cancel.")
        if m := re.search(r"\bSR-\d{4}-\d{4}\b", text, re.I):
            return calls + [("get_ticket_status", {"ticket_number": m.group(0).upper()})]
        if re.search(r"\b(raise|create|log|open|register|file|submit)\b.*\b(ticket|support request|request|complaint)\b", t) or re.search(r"\breport (?:a )?(problem|issue)\b", t):
            if not re.search(r"because|about|regarding|failed|not working|problem with|issue with|:|for my|need a|need an|unable|cannot|can't|could not", t):
                return "What is the support request about (for example fees, scholarship, exams, hostel or IT), and what exactly happened? I'll raise it as soon as I know."
            subject = re.sub(r"^(please\s+)?(raise|create|log|open|register|file|submit)\s+(a\s+|an\s+)?(support\s+)?(request|ticket|complaint)\s*(for me\s*)?[:,-]?\s*(because\s+)?", "", text, flags=re.I).strip() or text
            return calls + [("create_support_ticket", {"category": self._category(t), "subject": subject[:120] or "Support request", "description": text[:1000],
                                                       "priority": "HIGH" if re.search(r"\b(urgent|asap|immediately)\b", t) else "NORMAL"})]
        if re.search(r"\b(\d+)\s*days?\s*late\b", t):
            return calls + [("compute_late_fee", {"days_late": int(re.search(r"(\d+)\s*days?\s*late", t).group(1))})]
        if re.search(r"\b(fee|fees|dues|installment|instalment|outstanding|balance|paid|overdue|late fee)\b", t) and not re.search(r"\b(hostel|mess|exam fee|library)\b", t):
            if _POLICY.search(t):
                return calls + [("search_student_policy", {"query": text})]
            return calls + [("check_fee_status", {}), ("search_student_policy", {"query": "fee installment due dates late fee policy consequences of dues", "limit": 2})]
        if re.search(r"\bscholarship", t):
            if "eligib" in t:
                return calls + [("check_scholarship_status", {}), ("search_student_policy", {"query": "scholarship eligibility criteria documents required how to apply", "limit": 3})]
            if _POLICY.search(t) and not re.search(r"\b(my|status)\b", t):
                return calls + [("search_student_policy", {"query": text})]
            return calls + [("check_scholarship_status", {}), ("search_student_policy", {"query": "scholarship status meanings continuation conditions", "limit": 2})]
        if re.search(r"\b(subject|subjects|course|courses|syllabus|curriculum|credits?)\b", t) and re.search(r"\b(trimester|semester|sem|trisem|term|year|programme|program|bda|aids|all)\b", t) \
                and not re.search(r"\b(change|drop|switch|elective)\b", t):
            m = re.search(r"\b(?:trimester|semester|sem|term|trim)\s*-?\s*(i{1,3}|iv|v|vi|[1-6])\b", t)
            args: dict[str, Any] = {}
            if m and not re.search(r"\b(all|every|each|whole|full|entire)\b", t):
                args["trimester"] = m.group(1).upper()
            if re.search(r"\b(bda|big data)\b", t): args["programme"] = "PGDM-BDA"
            elif re.search(r"\b(aids|ai ?& ?ds|ai and ds)\b", t): args["programme"] = "PGDM-AIDS"
            return calls + [("get_curriculum", args)]
        if re.search(r"\b(when|next|upcoming|dates?)\b", t) and re.search(r"\b(exam|exams|holiday|holidays|vacation|due|installment|trimester|capstone|mid-?term|end-?term)\b", t) and not re.search(r"\b(policy|rule|how many|minimum)\b", t):
            q = "exam" if re.search(r"exam|mid-?term|end-?term", t) else ("holiday" if re.search(r"holiday|vacation", t) else ("fee" if re.search(r"due|installment", t) else text[:60]))
            return calls + [("get_current_academic_calendar", {"query": q, "limit": 10})]
        if re.search(r"\b(my (profile|attendance|cgpa|details|programme|program|trimester|email|mobile|phone|address|courses)|who am i)\b", t):
            return calls + [("get_student_profile", {})]
        if re.fullmatch(r"(hi|hello|hey|good (morning|afternoon|evening)|thanks|thank you|ok|okay)[!. ]*", t):
            return calls and "Noted." or f"Hello{', ' + mem['preferred_name'] if mem.get('preferred_name') else ''}! I'm CampusMate AI, the AIDTM student support assistant. Ask me about your fees, scholarship, policies, exams or calendar, or raise a support request."
        if m := re.search(r"what is\s+(-?\d+(?:\.\d+)?)\s*([+\-*/x×])\s*(-?\d+(?:\.\d+)?)\s*\??$", t):
            a, op, b = float(m.group(1)), m.group(2), float(m.group(3))
            val = a + b if op == "+" else a - b if op == "-" else a * b if op in "*x×" else (a / b if b else float("nan"))
            return f"{val:g}"
        if calls and not _DOMAIN.search(t):
            return calls
        if not _DOMAIN.search(t):
            for key, (full, short) in _GLOSSARY.items():
                if key in t:
                    return "(offline test model) " + (short if concise else full)
            return "(offline test model) That is a general question, so I'm answering directly without using any campus tools."
        return calls + [("search_student_policy", {"query": text})]

    # ----------------------------------------------------------------------------- answer synthesis (from tool results only)
    @staticmethod
    def _inr(n: Any) -> str:
        try: v = int(n)
        except (TypeError, ValueError): return "-"
        s, sign = str(abs(v)), "-" if v < 0 else ""
        if len(s) <= 3: return f"INR {sign}{s}"
        head, tail, parts = s[:-3], s[-3:], []
        while len(head) > 2: parts.insert(0, head[-2:]); head = head[:-2]
        if head: parts.insert(0, head)
        return f"INR {sign}{','.join(parts)},{tail}"

    def _answer(self, results: list[tuple[str, dict[str, Any]]]) -> str:
        sections, policy, steps = [], [], []
        for name, env in results:
            if name == "search_student_policy" and env.get("success"):
                policy.extend(env["data"].get("results", []))
                if not env["data"].get("results") and len(results) == 1:
                    sections.append("### Not covered by the handbook\nI searched the Student Handbook and policy documents and none of them cover this, so I won't guess.\n\n"
                                    "- Contact the Program Office on 9099981586.\n- Or ask me to raise a support ticket about it [M.1.1].")
                continue
            if not env.get("success"):
                e = env.get("error", {})
                code = e.get("code", "ERROR")
                if code == "FORBIDDEN":
                    sections.append("### I can't share that\nYou are signed in as yourself, and a student can see only their own private record. Another student's fees, scholarship, grades, "
                                    "attendance or contact details are never disclosed, whoever asks and however the request is worded [N.1.1].\n\n- The attempt was recorded.\n"
                                    "- I can show your own record, for example: *What is my fee status?*")
                elif code == "INVALID_ARGUMENTS":
                    sections.append(f"### I couldn't do that yet\nSome details were missing or not valid: {e.get('message', '')}\n\nPlease correct them and I'll try again.")
                elif code in ("TOOL_FAILED", "UNKNOWN_TOOL"):
                    sections.append("### Something went wrong on our side\nThat request could not be completed" + (f" (reference {e['reference']})" if e.get("reference") else "") +
                                    ". Please try again, or raise a support ticket and the Program Office will help [M.1.1].")
                else:
                    sections.append(f"### That didn't work\n{e.get('message', 'The request could not be completed.')}\n\nYou can raise a support ticket if you need help [M.1.1].")
                continue
            d = env["data"]
            if name == "check_fee_status":
                rows = ["| Installment | Due date | Amount | Status | Paid on | Late fee |", "|---|---|---|---|---|---|"]
                for i in d["installments"]:
                    lf = "-"
                    if i["status"] == "OVERDUE": lf = f"{self._inr(i['late_fee_accrued_as_of_today'])} accrued ({i['days_overdue']} days)" + (" - beyond 6 weeks" if i.get("beyond_6_weeks") else "")
                    elif i.get("late_fee"): lf = self._inr(i["late_fee"]) + (" (unpaid)" if i.get("unpaid_late_fee") else " (paid)")
                    rows.append(f"| {i['name']} | {i['due_date']} | {self._inr(i['amount'])} | {i['status']} | {i['paid_on'] or '-'} | {lf} |")
                nd = d.get("next_due")
                summ = [f"- **Academic year:** {d['academic_year']}", f"- **Total fee:** {self._inr(d['total_fee'])} (tuition {self._inr(d['total_tuition'])} + refundable deposit {self._inr(d['caution_deposit'])})",
                        f"- **Paid:** {self._inr(d['amount_paid'])}", f"- **Due:** {self._inr(d['amount_due'])}  ·  **Status:** {d['status']}", f"- **Outstanding right now (incl. late fees):** {self._inr(d['outstanding_now'])}"]
                if nd: summ.append(f"- **Next due:** {nd['name']} of {self._inr(nd['amount'])} by {nd['due_date']} ({nd['days_remaining']} days away)")
                sections.append(f"### Your fee status as of {d['as_of']}  *(verified record)*\n" + "\n".join(rows) + "\n\n" + "\n".join(summ))
                steps.append("Pay on or before the due date to avoid the late-fee slabs [F.3.1]; ask me *how much late fee for N days late* to calculate a penalty.")
            elif name == "check_scholarship_status":
                st = d["status"]
                rows = ["| Item | Detail |", "|---|---|", f"| Scholarship | {d['scholarship_name'] or '-'} |", f"| Status | **{st}** |", f"| Total amount | {self._inr(d['amount_total'])} |",
                        f"| Adjusted so far | {self._inr(d['amount_adjusted'])} |", f"| Remaining | {self._inr(d['amount_remaining'])} |", f"| Decided on | {d['decided_on'] or '-'} |"]
                sections.append("### Your scholarship status  *(verified record)*\n" + "\n".join(rows) + (f"\n\n{d['notes']}" if d.get("notes") else "") +
                                ("\n\nI can report your record but I don't decide eligibility; see the policy below and the Scholarship Committee." if st in ("NOT_APPLIED", "SUBMITTED", "UNDER_REVIEW") else ""))
            elif name == "get_student_profile":
                rows = ["| Field | Value |", "|---|---|", f"| Name | {d['name']} ({d['student_id']}) |", f"| Programme | {d['programme_name']} |", f"| Batch / trimester | {d['batch']} / Trimester {d['current_trimester']} |",
                        f"| Year 1 CGPA | {d['year1_cgpa']} |", f"| Attendance | {d['attendance_current_trimester_pct']}% |", f"| Mobile | {d['mobile']} |", f"| Email | {d['email']} |",
                        f"| Address | {d['address']} |", f"| Refund bank account | {d['refund_bank_account_masked']} |",
                        "| Registered courses | " + "; ".join(f"{c['course']} ({c['kind']})" for c in d["registered_courses"]) + " |"]
                sections.append("### Your profile  *(verified record)*\n" + "\n".join(rows))
                if (d["attendance_current_trimester_pct"] or 100) < 70: steps.append(f"Attendance is {d['attendance_current_trimester_pct']}%, below the 70% needed for the end-term exam; speak to the Program Office [D.1.1].")
                steps.append("To change contact details, bank account, programme or an elective, tell me the new value; it goes to the Program Office for approval [N.1.2].")
            elif name == "create_support_ticket":
                t = d["ticket"]
                sections.append(("### Support ticket already open\nYou raised the same request moments ago, so I did not create a duplicate.\n" if d.get("duplicate_of_recent_ticket") else "### Support ticket created\n") +
                                "\n".join(["| Field | Value |", "|---|---|", f"| Ticket | **{t['ticket_number']}** |", f"| Category | {t['category_label']} |", f"| Subject | {t['subject']} |", f"| Priority | {t['priority']} |", f"| Status | {t['status']} |"]) +
                                f"\n\nService level: {d['sla']} [M.1.2].")
                steps.append("Ask me *status of ticket " + t["ticket_number"] + "* any time.")
            elif name == "get_ticket_status":
                t = d["ticket"]
                sections.append("### Ticket status\n" + "\n".join(["| Field | Value |", "|---|---|", f"| Ticket | **{t['ticket_number']}** |", f"| Category | {t['category_label']} |", f"| Subject | {t['subject']} |",
                                                                  f"| Status | **{t['status']}** |", f"| Last updated | {t['updated_at']} |"]) + (f"\n\nStaff note: {t['staff_note']}" if t.get("staff_note") else ""))
            elif name == "compute_late_fee":
                rows = ["| Band | Rate | Days | Amount |", "|---|---|---|---|", f"| Up to 2 weeks | INR 100/day | {min(d['days_late'], 14)} | {self._inr(d['band_up_to_2_weeks'])} |",
                        f"| 2 to 4 weeks | INR 200/day | {min(max(d['days_late'] - 14, 0), 14)} | {self._inr(d['band_2_to_4_weeks'])} |", f"| 4 to 6 weeks | INR 400/day | {min(max(d['days_late'] - 28, 0), 14)} | {self._inr(d['band_4_to_6_weeks'])} |"]
                if d["days_beyond_6_weeks"]: rows.append(f"| Beyond 6 weeks (if permitted) | INR 2,000/day | {d['days_beyond_6_weeks']} | {self._inr(d['penalty_beyond_6_weeks'])} |")
                rows.append(f"| **Total** | | **{d['days_late']} days** | **{self._inr(d['total_late_fee'])}** |")
                sections.append(f"### Late fee for {d['days_late']} days late\n" + "\n".join(rows) + (f"\n\n**Warning:** {d['warning']}" if d.get("warning") else "") + "\n\n[F.3.1, F.3.2]")
            elif name == "get_current_academic_calendar":
                ev = d.get("events", [])
                sections.append(("### Upcoming events as of " + d["as_of"] + "\n| Event | Date | Type | Status |\n|---|---|---|---|\n" +
                                 "\n".join(f"| {e['title']} | {e['start']}" + (f" to {e['end']}" if e["end"] != e["start"] else "") + f" | {e['type']} | {e['status'].title()} |" for e in ev) + f"\n\n{d['note']}") if ev
                                else "### Calendar\nNo verified date is configured for that, so I can't give one. Please check with the Program Office.")
            elif name == "get_curriculum":
                lines = [f"### {d['programme_name']}: trimester-wise plan"]
                for tr in d["trimesters"]:
                    lines += [f"\n**Trimester {tr['trimester']}** (Year {tr['year']}) - {tr['start']} to {tr['end']} - {tr['credits']} credits", "| Course | Credits |", "|---|---|"] + [f"| {c['name']} | {c['credits']} |" for c in tr["courses"]]
                    lines.append(f"- Mid-term: {tr['mid_term']}\n- End-term: {tr['end_term']}\n- Source: {tr['status']}")
                sections.append("\n".join(lines) + f"\n\n{d['note']}")
            elif name == "save_memory":
                sections.append("Noted. I'll remember that for future conversations; you can review or delete it in your profile.")
            elif name == "forget_memory":
                sections.append(f"Done. I removed {d['removed']} stored item(s).")
            elif name == "request_sensitive_change":
                ch = ", ".join(f"{k}: {v}" for k, v in d["requested_changes"].items())
                sections.append("### Request sent for approval\n" + "\n".join(["| Field | Value |", "|---|---|", f"| Request | **{d['request_number']}** |", f"| Change | {d['action_description']}" + (f" ({ch})" if ch else "") + " |",
                                                                              "| Status | **PENDING** - waiting for the Program Office |", "| Applied yet? | **No.** Nothing changes unless it is approved |"]) +
                                "\n\nA rejected request leaves your record untouched, and both outcomes are audited [N.1.2].")
                steps.append("Watch the approvals panel for the decision.")
            else:
                sections.append("```\n" + json.dumps(d, ensure_ascii=False)[:600] + "\n```")
        if policy:
            seen, uniq = set(), []
            for c in policy:
                if c["id"] not in seen: seen.add(c["id"]); uniq.append(c)
            body = []
            for c in uniq[:4]:
                txt = c["text"].replace("…", "").strip()
                text = txt if (c.get("type") in ("overview", "course") or "|" in txt) else " ".join(re.split(r"(?<=[.!?])\s+", txt)[:5])
                body.append(f"**{c['title']}** [{c['id']}]" + ("" if c["status"] == "OFFICIAL" else f" *({c['status'].lower().replace('-', ' ')})*") + f"\n{text}")
            note = "\n\nEntries marked *augmented* are helpdesk guidance, not official AIDTM documents; confirm them with the Program Office (9099981586)." if any(c["status"] != "OFFICIAL" for c in uniq[:4]) else ""
            sections.append(("### Policy context" if sections else "### What the handbook says") + "\n" + "\n\n".join(body) + note)
        if steps:
            sections.append("### What you can do next\n" + "\n".join(f"- {s}" for s in dict.fromkeys(steps)))
        return "\n\n".join(dict.fromkeys(sections)) if sections else "I'm not sure how to help with that. Could you rephrase?"
