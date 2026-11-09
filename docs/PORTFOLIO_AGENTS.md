# Portfolio agents — what they do for you

The NSE Dividend Tracker includes **portfolio agents**: small, deterministic
programs that watch your **local** holdings and suggest the next best action.
They are **not** a chatbot, **not** an LLM, and **not** connected to a cloud
service. Every agent runs on your machine from data you already stored (plus
dividend fetches you trigger yourself).

**Why they exist:** dividend tracking is repetitive — blank quarters, stale
prices, a new payout line on the website, or optimistic quantity assumptions.
Agents turn those patterns into a short, prioritized to-do list so you spend less
time hunting problems and more time fixing the few rows that matter.

## Where you see them

| Surface | What you get |
| --- | --- |
| **Briefing** (header) | Main agent UI: tasks, urgent badge, 12-month cashflow snapshot, dividend context |
| **Analysis** | Income bridge, forward cashflow, monthly cashflow (feeds stress and attribution logic) |
| **Import / Reconcile / Export** | Agent-backed previews and gates before you change data |
| **After refresh** | Dividend-history change message on the dashboard when payouts update |

Open **Briefing**, pick a **year** (calendar year, FY, or all years). Tasks
filter to what matters for that period. Nothing in Briefing writes to the
database until you click an action that saves (for example dismiss alert,
autofill, or editing a quantity).

## How tasks work

Each task has:

- **Priority** — **P0** urgent (missing data, failed fetch), **P1** important
  (cuts, big stress), **P2** follow-up (attribution, optional spread).
- **Message** — plain language, one screen.
- **Action** — jump to the right place: quarter cell, refresh, Analysis, Insight,
  import, reconcile, export, or dismiss an alert.

The red badge on **Briefing** counts **P0** items for the dashboard year.

## Agent catalogue

### 1. Data hygiene (built into coach)

**Missing quarters** — you recorded payouts in a year but left a quarter
quantity empty, so income for that payout is understated.  
**Action:** focus the quarter and enter shares held.

**Failed or stale refresh** — last dividend fetch failed or is older than about
a week. Your table may show outdated DPS or prices.  
**Action:** refresh that ticker (or fix the symbol if invalid).

**Dividend cuts** — per-share dividend dropped sharply in a past year (Insight
rules).  
**Action:** open Insight and review that holding.

**Concentration** — one stock supplies a large share of income in the selected
period.  
**Action:** open Insight for the concentration picture.

**Next-year scenario spread** — conservative and growth next-year projections
differ a lot (quantity or CAGR assumptions).  
**Action:** open Analysis and review scenarios.

### 2. Reconcile agent

Compares **computed** dividend income for a year (or FY) with **actual** amounts
you enter from bank statements.

**When it helps:** you use the tracker for tax or cash reconciliation, not only
planning.

**Tasks:** `reconcile-gap` when computed and actual diverge beyond tolerance.  
**Action:** open the reconcile panel for that ticker and year.

**You control:** actual amounts and notes live on the holding; the agent only
flags mismatches.

### 3. Refresh diff agent

Runs when a **save** includes **changed dividend history** (usually after
**Refresh**).

**Detects:**

- **New payout** — a new ex-date/amount row vs what was stored.
- **DPS change** — same date, different amount per share.

**Stores:** `dividendAlerts` on the holding (until you dismiss).  
**Tasks:** confirm the quarter quantity for a new payout; review Insight for DPS
changes.  
**Action:** focus quarter, open Insight, or **Dismiss** when done.

**Value:** you see upstream changes without comparing JSON or old screenshots.

### 4. Income attribution agent

For a **calendar year** (e.g. 2026, not “All years” or FY-only briefing), explains
**why one stock’s income moved** versus the previous year.

**Drivers:**

| Driver | Meaning |
| --- | --- |
| Share quantities | You held more or fewer shares in paying quarters |
| Dividend per share | Company changed DPS; your quantities were similar |
| New or removed payouts | First year of income from a name, or income went to zero |

**Tasks:** up to a few largest movers plus optional portfolio-level bridge task.  
**Actions:** focus quarter (quantity-led) or Insight / Analysis (DPS or
portfolio bridge).

**Value:** answers “why did ITC income jump?” without rebuilding the bridge
yourself.

### 5. Cashflow stress agent

Uses the **rolling 12-month forward cashflow** model (conservative / base /
growth), same as Briefing and Analysis.

**Checks:**

- **Base vs conservative** — if base assumes peak quantities and full DPS while
  conservative uses softer assumptions, a **≥10%** gap triggers a warning when
  portfolio base cash is material.
- **Severe shock** — combined **−10% quantity and −10% DPS** on the base path;
  large drop vs base triggers **P1**-style messaging.
- **Per-ticker gaps** — names that drive most of the base/conservative
  difference.

**Actions:** open Analysis (forward cashflow) or Insight for the ticker.

**Value:** sanity-check that peak quantities and dividend history are not
silently optimistic for the year ahead.

**Note:** illustrative timing only — not a guarantee of future dividends.

### 6. Autofill agent

For the **current calendar year**, finds quarters still blank where last year’s
same quarter (or peak prior-year quantity) can be copied.

**Tasks:** `autofill-quantity` per ticker.  
**Action:** apply autofill (writes quantities — review before you rely on it).

### 7. Export and FY tax agents

**Export readiness** — blocks or warns on bulk export when quantities, errors,
or gaps would make a CSV misleading for the chosen year.

**FY tax playbook** — when Briefing year is an India financial year (`fy:…`),
checklist tasks (income computed, export, etc.) for a simple filing prep
workflow.

**Actions:** run export, open tax pack in Analysis.

### 8. Calendar agent

Builds upcoming **dividend window** hints from payment patterns and offers
**download calendar (.ics)** for reminders.

**Tasks:** upcoming payout events in Briefing; optional calendar download from
Analysis.

## What agents do *not* do

- They **do not** buy or sell stocks, connect to brokers, or move money.
- They **do not** call the network on their own in Briefing (only your refresh
  and imports hit the network).
- They **do not** replace tax or investment advice — same disclaimer as Insight.
- They **do not** auto-edit quantities except when you confirm **autofill** or
  save from the UI.

## Typical workflows

### Weekly upkeep (5–10 minutes)

1. Glance at the **Briefing** badge; fix **P0** items first.
2. **Refresh** tickers that are stale or failed.
3. Dismiss or act on **refresh diff** alerts after updates.

### Start of a calendar year

1. Enter real quantities for the new year (rollover may pre-fill from last year
   — verify).
2. Run **autofill** only where last year’s pattern still matches.
3. Open Briefing for **2026** (or the new year) for **attribution** once you have
   a full prior year.

### Before tax or export

1. Set Briefing year to the **FY** you need.
2. Complete **reconcile** tasks per ticker.
3. Use **export** / **tax playbook** tasks as a checklist.

### Planning cash for the next year

1. Open **Analysis** → forward cashflow.
2. Read Briefing **cashflow-stress** tasks if base ≫ conservative.
3. Adjust peak quarter assumptions if stress messages look too harsh or too rosy.

## Privacy and storage

- Agent **tasks** are computed on demand — they are not stored as a separate
  database.
- **Refresh diff** alerts are stored on each holding (`dividendAlerts`) so
  Briefing can remember what you have not dismissed.
- Everything else lives in your normal portfolio records in ChromaDB and the
  `data/portfolio.json` mirror.

## Related documentation

- [User guide](USER_GUIDE.md) — buttons, table, import, Analysis, Insight.
- [Architecture](ARCHITECTURE.md) — technical contract for developers and AI
  assistants (API, module names).
- [AGENTS.md](../AGENTS.md) — instructions for coding assistants working in the
  repo (not end-user focused).
