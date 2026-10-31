"""Static reference data used for validation (not student data)."""
from __future__ import annotations

TICKET_CATEGORIES = ["FEES", "SCHOLARSHIP", "ACADEMICS", "ATTENDANCE", "EXAMINATION", "TECHNICAL_SUPPORT", "OTHER",
                     "COURSE_CHANGE", "DOCUMENTS", "LIBRARY", "HOSTEL", "PLACEMENT", "GRIEVANCE"]
TICKET_CATEGORY_LABELS = {"FEES": "Fees & Payments", "SCHOLARSHIP": "Scholarships", "ACADEMICS": "Academics, Grades & Attendance",
                          "ATTENDANCE": "Attendance", "EXAMINATION": "Examinations", "TECHNICAL_SUPPORT": "IT & Login", "OTHER": "Other",
                          "COURSE_CHANGE": "Course Change", "DOCUMENTS": "Certificates & Documents", "LIBRARY": "Library",
                          "HOSTEL": "Hostel & Facilities", "PLACEMENT": "SIP & Placements", "GRIEVANCE": "General Grievance"}
TICKET_PRIORITIES = ["NORMAL", "HIGH", "URGENT"]
TICKET_STATUSES = ["OPEN", "IN_PROGRESS", "AWAITING_STUDENT", "RESOLVED", "CLOSED", "ESCALATED"]
SLA = {"URGENT": "acknowledged within 4 working hours", "HIGH": "acknowledged within 1 working day",
       "NORMAL": "acknowledged within 3 working days and resolved within 7 working days"}
ELECTIVE_BASKET = ["Marketing Management", "Business Management", "HR Analytics", "Fundamentals of Finance",
                   "Entrepreneurship Essentials", "Digital Marketing Analytics"]
PROGRAMMES = {"PGDM-BDA": "PGDM (Big Data Analytics)", "PGDM-AIDS": "PGDM (Artificial Intelligence & Data Science)"}
SCHOLARSHIP_STATUSES = ["NOT_APPLIED", "SUBMITTED", "UNDER_REVIEW", "APPROVED", "REJECTED", "ADDITIONAL_DOCUMENTS_REQUIRED", "WITHDRAWN"]

# Sensitivity policy: which actions are sensitive (need human approval), their schema keys and who may decide.
# Anything NOT listed here (tickets, preferences, reads) is non-sensitive and runs immediately.
SENSITIVE_ACTIONS = {
    "UPDATE_CONTACT": {"label": "Update registered contact details", "resource": "student_record", "approvers": ["staff", "admin"]},
    "UPDATE_REFUND_BANK": {"label": "Change refund bank account", "resource": "student_record", "approvers": ["staff", "admin"]},
    "SWITCH_PROGRAMME": {"label": "Switch programme", "resource": "student_record", "approvers": ["staff", "admin"]},
    "WITHDRAW_SCHOLARSHIP": {"label": "Withdraw scholarship application", "resource": "scholarship_record", "approvers": ["staff", "admin"]},
    "CHANGE_COURSE": {"label": "Change a registered elective", "resource": "course_registration", "approvers": ["staff", "admin"]},
    "FEE_CORRECTION": {"label": "Fee-related correction (late fee)", "resource": "fee_installment", "approvers": ["staff", "admin"]},
}
PROGRAM_OFFICE_ACTIONS = ["UPDATE_CONTACT", "SWITCH_PROGRAMME", "CHANGE_COURSE", "WITHDRAW_SCHOLARSHIP"]
ACCOUNTS_ACTIONS = ["FEE_CORRECTION", "UPDATE_REFUND_BANK", "WITHDRAW_SCHOLARSHIP"]
PROGRAM_OFFICE_TICKETS = ["ACADEMICS", "ATTENDANCE", "EXAMINATION", "SCHOLARSHIP", "COURSE_CHANGE", "DOCUMENTS", "PLACEMENT", "GRIEVANCE",
                          "HOSTEL", "LIBRARY", "TECHNICAL_SUPPORT", "OTHER"]
ACCOUNTS_TICKETS = ["FEES", "SCHOLARSHIP", "DOCUMENTS"]
