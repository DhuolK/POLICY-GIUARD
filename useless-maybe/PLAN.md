# POLICY GUARD Master Implementation & Parity Plan

## System Parity & Audit Overview

This plan maps every feature from the 37 detailed reference video frames (`C:\Users\Dhuoljok\Downloads\frames_detailed`) and system screenshots (`WhatsApp Image 2026-07-15`) directly against the Policy Guard codebase.

### Visual & Architectural Constraints
- **Design Language**: Minimalist, clean operational layout while preserving Policy Guard's premium typography, Tailwind styling, glass tokens, and color system.
- **Mobile Responsiveness**: Complete mobile viewport optimization (collapsible mobile navigation drawer, swipeable/horizontally scrollable responsive tables with touch-friendly action tap targets, stacked card views for mobile screens, sticky headers).
- **Core Security Preserved**: Strict RBAC (`admin`, `worker`, `customer`), CSRF protection, centralized visibility scoping (`app/utils/visibility.py`), immutable audit trails, and version snapshotting.

---

## Exhaustive Feature Audit Matrix

| Category | Reference Feature | Current Codebase Status | Gap / Missing Elements | Target Sequence |
| :--- | :--- | :--- | :--- | :--- |
| **Top Navigation** | Grouped Dropdowns (`Listing ▾`, `Vehicles ▾`, `Policy ▾`, `SMS ▾`, `Accounts ▾`, `Users ▾`) | Flat navbar links in `templates/base.html` | Dropdown menus, mobile hamburger drawer, sub-route grouping | Sequence 10 |
| **Header Status** | Live Balance indicator (`Balance: KES -0`) | Missing in navbar | Dynamic balance query & badge in header | Sequence 10 |
| **Policy Registry** | Operational Table Columns (`#`, `Policy Number`, `Vehicle`, `PAX`, `Policy Owner`, `Insurance Company`, `Start Date`, `Exp Date`, `Type`, `Action`) | Basic list without `PAX`, `Insurance Company`, or inline Action menu | Add `pax`, `insurance_company_id`, inline action dropdown/buttons | Sequence 1 & 2 |
| **Policy Registry** | Motor Business vs. Non Motor Business Toggle Tabs | Only "Issued Policies" vs "Available Policy Types" tabs | Add Motor / Non-Motor categorization and filter toggle | Sequence 1 & 2 |
| **Policy Registry** | Inline Policy Actions (`Edit`, `Extend`, `Suspend`) | Only `View` link in table | Add direct modal triggers for `Edit`, `Extend` (Renewal), and `Suspend` | Sequence 3 & 4 |
| **Policy Extension** | Rich Extension Modal with Daraja STK Push Request | No extension modal | Modal with Certificate #, Start/End dates, Months, Premium, Deposit, Balance, Pay Mode, Phone, STK Push trigger | Sequence 4 & 7 |
| **Company Directory** | List of 56 Insurance Underwriters | Only dynamic policy types exist | 56 licensed company directory, vehicle count per company, active/inactive toggles, modal to add provider | Sequence 5 |
| **Payments / Accounts** | Financial Metric Cards (Total Receivable, Total Overdue, Total Transactions) | Exists in `PaymentService` & `templates/dashboard/index.html` | Needs dedicated Accounts view with detailed ledger | Sequence 6 & 9 |
| **Payments / Accounts** | Outstanding Balances & Recent Payments tables | Exists in `PaymentService` / Dashboard | Dedicated sub-views under Accounts dropdown | Sequence 6 & 10 |
| **SMS Module** | Outbound SMS Dispatch & History log | Outbound SMS engine exists in `reminder_service.py` & `sms_service.py` | UI view for SMS history logs, delivery status, and bulk/manual trigger UI | Sequence 8 |
| **Vehicles Module** | Private vs. Public Vehicles classification tabs | Single list view in `templates/vehicles/list.html` | Tab filtering for Private vs. Public (PSV) vehicles, PAX tracking | Sequence 2 & 10 |
| **Client Management** | Client profile with linked vehicles, policies, claims | Implemented in `templates/clients/profile.html` | Add KRA PIN column, responsive stacked cards for mobile | Sequence 10 |

---

## Phased Implementation Sequence

### Sequence 0: Baseline & Test Verification (COMPLETED)
- Verified worker isolation, role enforcement, and reminder thresholds.
- 74 passing automated tests.

### Sequence 1: Policy List Operational Foundation & Mobile Table (COMPLETED)
- Server-side search across policy number, registration, client name, and status.
- Allowlisted server-side sorting and ceiling-divided pagination.
- Dictionary-safe Jinja access (`policies_page['items']`).

### Sequence 2: Domain Schema Reconciliation (Motor/Non-Motor, PAX, Provider Linking)
- Add `pax` (integer) and `category` (`motor` / `non_motor`) fields to policies and vehicles.
- Update policy creation/edit forms (`templates/policies/form.html`, `templates/policies/edit_form.html`) to support dynamic categories and provider selection.
- Update policy table with `PAX`, `Insurance Company`, and `Motor Business` / `Non Motor Business` tab filters.

### Sequence 3: Policy Lifecycle Transitions & Action Endpoints
- Implement `POST /policies/<id>/suspend` endpoint with RBAC, CSRF, audit logging, and versioning.
- Implement inline action menu (`Edit`, `Extend`, `Suspend`) in policy list.
- Reconcile status labels (`published` -> Active, `suspended` -> Suspended, `dormant` -> Dormant, `expired` -> Expired).

### Sequence 4: Rich Policy Extension Modal & Server Calculation
- Build mobile-responsive `Policy Extension` modal with fields:
  - Policy ID, Policy Number, Reg No, Insurance Company, Certificate #.
  - Start Date, Months (duration), auto-computed Exp Date.
  - Premium, Deposit, auto-computed Balance (`Premium - Deposit`).
  - Pay Mode (`CASH`, `MPESA`), Phone (Kenyan format), Reference.
- Implement server-side calculation and extension persistence with audit trail and version snapshot.

### Sequence 5: Insurance Provider / Company Management
- Seed 56 licensed Kenyan insurance companies (AAR, Africa Merchant / AMACO, AIG, Allianz, APA, Britam, Directline, Invesco, Trident, Occidental, etc.).
- Build Provider Management screen (`/policies/providers`):
  - Table: `#`, `Company Name`, `Vehicles` (count), `Short Name`, `Status`, `Action` (`Activate` / `Deactivate`).
  - `Add New Company` modal.
  - Active-status enforcement in policy issuance dropdowns.

### Sequence 6: Transaction & Accounts Ledger
- Create dedicated Accounts route (`/accounts`) and views:
  - KPI summary: Total Receivable, Total Overdue, Total Transactions.
  - Outstanding Balances table with client contact info.
  - Recent Payments & M-Pesa transaction log.

### Sequence 7: Daraja M-Pesa STK Push Integration
- Implement `DarajaService` with sandbox/simulation support and OAuth token management.
- Connect the `Request` button on the Policy Extension modal to trigger an STK Push to the customer's phone.
- Implement idempotent callback webhook handler with receipt reconciliation.

### Sequence 8: SMS History & Operations
- Build SMS dashboard and logs view (`/sms`):
  - Message log table: Recipient, Phone, Policy Number, Message Content, Status (Sent / Simulated / Failed), Provider Ref, Sent At.
  - Manual SMS trigger and resend capabilities.

### Sequence 9: Admin Analytics & Financial Metrics
- Real-time policy breakdown by underwriter, motor vs. non-motor, and status.
- Revenue collection summaries and payment reconciliation stats.

### Sequence 10: Top Navigation Restructuring & Mobile Experience
- Redesign `templates/base.html` header:
  - Grouped desktop dropdown menus: `Listing ▾`, `Vehicles ▾`, `Policy ▾`, `SMS ▾`, `Accounts ▾`, `Users ▾`.
  - Live `Balance: KES -0` indicator.
  - Mobile hamburger menu drawer with smooth slide-in transition and touch-friendly navigation tree.
  - Mobile-responsive table wrappers with horizontal scrolling and card-based responsive fallbacks.

### Sequence 11: End-to-End Verification & Manual Test Suite
- Automated regression test suite execution across all routes and services.
- Verification of mobile responsiveness and touch interactions.
- Generation of the comprehensive operational test checklist.
