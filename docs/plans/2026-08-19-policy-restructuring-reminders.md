# Policy Restructuring & Expiration Reminders Implementation Plan

> **For Gemini:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Modify the policy management system to support custom Policy Numbers, Effective Dates, and Expiry Dates on draft policies, and create a comprehensive "Expiring Soon" policies dashboard with automated and manual notification reminder workflows.

**Architecture:** 
1. **Editing Module:** We'll add a new `edit` route/form to update core policy properties (`policy_number`, `effective_date`, `expiry_date`, `policy_type`, `premium_amount`) before they are published.
2. **Date & Uniqueness Validations:** We'll enforce that the effective date must be before the expiry date and ensure updated policy numbers remain globally unique in the database.
3. **Reminders Collection:** Create a separate `reminders` collection in MongoDB to track automated/manual expiration reminders sent to policyholders (containing fields: `policy_id`, `policy_number`, `client_id`, `client_name`, `expiry_date`, `days_remaining`, `status`, `sent_at`, `channel`).
4. **Reminders Dashboard & Processor:** Add an `/policies/expiring` route and UI showing policies expiring soon (days remaining <= 180 so seed data is visible), tracking their notification status, and offering both automated batch sending and individual manual triggers.

**Tech Stack:** Flask (Python), MongoDB (PyMongo), Tailwind CSS, GSAP for UI animations.

---

### Task 1: Create the Policy Edit Form HTML template

**Files:**
- Create: `templates/policies/edit_form.html`

**Step 1: Write the edit form template**
Create a new file `templates/policies/edit_form.html` that allows updating a draft policy's details with strict user validation (like calendar inputs for dates).

---

### Task 2: Implement Policy Edit Route and Schema Validations

**Files:**
- Modify: `app/routes/policies.py`
- Modify: `templates/policies/detail.html`

**Step 1: Add GET and POST handlers for `/policies/<policy_id>/edit`**
Implement date comparisons (`effective_date < expiry_date`), policy status gating (only allowing edits on `draft` or `pending_review`), and global uniqueness validation for the modified policy number. Add edit logging via the `AuditService`.
Also, add an "Edit Details" button on `templates/policies/detail.html` if the policy is in a editable state.

---

### Task 3: Create the Reminder Tracker Collection & Logic

**Files:**
- Create: `app/services/reminder_service.py`

**Step 1: Create ReminderService class**
Implement a service class with methods:
- `get_expiring_soon_policies()`: Fetches active/published policies and computes remaining days.
- `send_automatic_reminders()`: Scans expiring policies and creates `reminders` collection records, marking them as `sent`.
- `send_manual_reminder(policy_id)`: Triggers/sends a manual reminder for a single policy.
- `get_reminders_for_policies(policy_ids)`: Retrieves reminder documents for a set of policy IDs to map sent/pending status in the UI.

---

### Task 4: Create the "Expiring Soon & Reminders" Dashboard

**Files:**
- Modify: `app/routes/policies.py`
- Create: `templates/policies/expiring.html`
- Modify: `templates/policies/list.html`
- Modify: `templates/base.html`

**Step 1: Add `/policies/expiring` and reminder trigger endpoints**
- `GET /policies/expiring`: Displays the list of expiring soon policies and their notification statuses.
- `POST /policies/expiring/trigger-auto`: Processes bulk auto-reminders.
- `POST /policies/expiring/<policy_id>/trigger-manual`: Sends/retries a single reminder.
- Add links to `templates/policies/list.html` and `templates/base.html` to navigate to the page.

---

### Task 5: Add Unit Tests & Verify Correctness

**Files:**
- Create: `tests/test_policy_restructuring.py`

**Step 1: Write integration and unit tests**
Test:
- Policy edit validations (date ranges, uniqueness).
- Correct state gating (blocking edits on published policies).
- Reminder service correct expiration filtering and reminder record insertion.
