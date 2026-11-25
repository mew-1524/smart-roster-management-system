# Smart Roster Management System

A browser-based workforce scheduling application for a
customer-experience and operations company managing support processes
across multiple D2C brands.

The system generates monthly employee rosters using a deterministic,
constraint-based scheduling engine. It validates the generated roster,
identifies coverage gaps and scheduling conflicts, and gives operations
managers information to make informed decisions.

**Built for the DataStraw Technologies AI + Tech Intern assessment.**

> **Project status:** The application has been run locally using Django
> and MySQL. Database migrations and sample-data seeding completed
> successfully against the local MySQL database. The application has
> also been run locally in a browser. A public deployment must be
> completed separately before sharing a public application URL.

------------------------------------------------------------------------

## Table of contents

-   [Features](#features)
-   [Technology stack](#technology-stack)
-   [Project structure](#project-structure)
-   [How the system works](#how-the-system-works)
-   [Data model](#data-model)
-   [Scheduling algorithm](#scheduling-algorithm)
-   [Conflict handling](#conflict-handling)
-   [Sample data](#sample-data)
-   [Run locally on Windows](#run-locally-on-windows)
-   [MySQL configuration](#mysql-configuration)
-   [Demo scenario](#demo-scenario)
-   [Testing](#testing)
-   [Assessment questions: how I
    think](#assessment-questions-how-i-think)
-   [Deployment](#deployment)
-   [Known limitations and future
    improvements](#known-limitations-and-future-improvements)

------------------------------------------------------------------------

## Features

### 1. Master data management

-   Create, view, edit, search, filter, activate and deactivate
    employees, brands, processes, pods and assignments.
-   Assign employees to pods and define pod leads.
-   Connect employees to brands and processes through assignments.
-   Support primary and backup assignments.

### 2. Brand and process operating rules

-   Configure operating calendars for each brand-process combination.
-   Support operating days, Sundays off, public holidays and
    brand-specific holidays.
-   Configure minimum staffing requirements.
-   Support weekday-specific staffing overrides, such as requiring two
    employees on Saturdays.
-   Keep brand operating calendars separate from an employee's personal
    availability.

### 3. Employee availability

-   Support fixed and rotating weekly offs.
-   Store preferred weekly-off days.
-   Manage leave requests with approval, rejection and pending states.
-   Track compensatory offs.
-   Configure public holidays globally or for selected brands.

### 4. Monthly roster generation

-   Generate and save a monthly roster.
-   Consider employee assignments, availability, approved leave,
    comp-offs, weekly offs, holidays, operating calendars, minimum
    staffing and workload rules.
-   Regenerate a roster while preserving supported manual edits.

### 5. Conflict detection and validation

-   Identify coverage gaps and other scheduling conflicts.
-   Display conflict ID, type, date, brand, process, employee, severity,
    explanation and suggested resolution.
-   Allow managers to review, acknowledge and reopen conflicts.
-   Revalidate roster data after relevant changes.

### 6. Monthly roster views

The roster interface provides: - Company view - Pod view - Brand view -
Process view - Employee view

It also supports filters, status colours, conflict markers, daily
coverage information and editing of roster entries with validation.

### 7. Change and leave impact handling

-   Analyse the impact of employee leave on roster coverage.
-   Suggest possible replacement employees.
-   Support assigning a replacement and applying leave to the roster.
-   Block publishing while open critical conflicts remain.

### 8. Dashboard and reports

-   Dashboard charts based on application data.
-   Employee, brand coverage, pod staffing and conflict reports.
-   CSV and Excel roster exports.
-   CSV report exports.

### 9. Authentication

-   Django authentication protects application pages.
-   Users must sign in to access the application.

### 10. Conflict explanations

-   Conflict explanations and suggested actions are generated using
    application rules.
-   The scheduling engine does not require an external AI API or send
    application data to an AI service.

------------------------------------------------------------------------

## Technology stack

  Layer               Technology
  ------------------- --------------------------------------------------------
  Backend             Python 3.12, Django 5.2
  Database            MySQL (configured) or SQLite (local zero-setup option)
  ORM                 Django ORM
  Frontend            HTML, CSS, Bootstrap 5.3, Bootstrap Icons
  Charts              Chart.js
  Excel export        openpyxl
  Static files        WhiteNoise
  Production server   Gunicorn

------------------------------------------------------------------------

## Project structure

``` text
smart_roster/
├── manage.py
├── requirements.txt
├── requirements-mysql.txt
├── .env.example
├── Procfile
├── build.sh
├── runtime.txt
├── config/
│   ├── settings.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
├── apps/
│   ├── core/
│   │   ├── models and forms
│   │   ├── CRUD and dashboard views
│   │   ├── availability and settings
│   │   └── seed_data management command
│   ├── scheduler/
│   │   ├── calendar_rules.py
│   │   ├── engine.py
│   │   ├── validation.py
│   │   ├── explain.py
│   │   ├── services.py
│   │   ├── selectors.py
│   │   └── exports.py
│   └── reports/
├── templates/
├── static/
├── media/
└── tests/
```

The employee, brand, process, pod, assignment and availability models
are kept in the `core` app because they are closely related and share
common management functionality. Scheduling logic is separated into the
`scheduler` app so that roster generation and validation are not mixed
into the presentation layer. Django's built-in authentication is used
for login.

------------------------------------------------------------------------

## How the system works

At a high level, the application follows this flow:

1.  The manager maintains employees, brands, processes, pods and
    assignments.
2.  The manager configures operating calendars and minimum staffing
    rules.
3.  Employee availability, weekly offs, leave, comp-offs and holidays
    are recorded.
4.  The roster generator creates the monthly schedule using the
    available information and configured rules.
5.  The validator checks the saved roster against coverage and employee
    constraints.
6.  Conflicts are displayed with explanations and possible actions.
7.  The manager reviews exceptions, makes manual decisions where needed
    and validates again.
8.  Publishing is restricted when open critical conflicts remain.

------------------------------------------------------------------------

## Data model

The main entities and relationships are:

-   **Employee:** Represents a staff member and is associated with a
    pod.
-   **Pod:** Represents an operational team. A pod can have a designated
    employee as its lead.
-   **Brand:** Represents a client or business brand supported by the
    operations team.
-   **Process:** Represents a type of work, such as CS, ACC, Retention
    or NDR.
-   **Assignment:** Connects an employee to a brand and process. It also
    records whether the assignment is a backup.
-   **OperatingRule:** Stores the operating calendar and minimum
    staffing requirement for a brand-process combination.
-   **Leave:** Stores employee leave requests and their approval status.
-   **CompOff:** Stores compensatory-off information.
-   **Holiday:** Stores public holidays, which may apply globally or to
    selected brands.
-   **RosterPeriod:** Represents a monthly roster and its publication
    state.
-   **RosterEntry:** Represents an employee's roster status for a
    particular date.
-   **RosterAssignment:** Records the brand and process work covered by
    an employee on a roster entry.
-   **Conflict:** Stores validation findings and their review status.

An employee can have multiple assignments, and a brand can have multiple
processes. This supports both single-resource and multi-resource brand
arrangements.

------------------------------------------------------------------------

## Scheduling algorithm

The scheduler is deterministic: the same input data and rules produce
the same scheduling decisions. It does not use random allocation or an
external large language model.

The generator broadly follows these steps:

1.  **Load the month context:** Load active employees, assignments,
    operating rules, holidays, approved leave and compensatory offs.
2.  **Allocate rotating weekly offs:** Choose weekly-off days while
    prioritising coverage, rest-day rules and then employee preferences
    and fairness.
3.  **Allocate daily work:** For each date, consider employees who are
    available and assign their active brand-process workloads when those
    workloads operate that day.
4.  **Check workload and shift compatibility:** Avoid allocations that
    violate configured workload limits or incompatible shift rules.
5.  **Fill coverage gaps:** Where possible, use eligible designated
    backup employees to cover remaining minimum-staffing gaps.
6.  **Validate and save:** Save the roster and run validation. Any
    remaining uncovered requirement is recorded as a conflict rather
    than hidden.

The validator independently checks the saved roster. It checks required
coverage for each operating brand-process-day and verifies that rostered
employees are eligible based on their assignments, availability, leave,
comp-offs, weekly offs, holidays, workload and shift rules.

------------------------------------------------------------------------

## Conflict handling

The application distinguishes between mandatory scheduling requirements
and preferences.

Examples include:

-   **No coverage:** A required brand-process workload has no rostered
    employee.
-   **Insufficient staffing:** The number of valid rostered employees is
    below the configured minimum.
-   **Preference not honoured:** An employee's preferred weekly-off day
    could not be used because coverage or other rules took priority.
-   **Assignment or availability issue:** A roster entry conflicts with
    the employee's assignment or availability.
-   **Shift or workload issue:** An allocation conflicts with configured
    shift compatibility or workload limits.

Critical and high-severity conflicts require manager attention.
Lower-severity preference warnings can be reviewed and acknowledged with
a reason when the manager accepts the trade-off. Acknowledging a warning
records a management decision; it does not itself change the roster.

The application aims to produce the best feasible roster it can and
clearly reports constraints that it cannot satisfy. It does not silently
treat an uncovered requirement as successful coverage.

------------------------------------------------------------------------

## Sample data

The seed command creates fictional demonstration data. The documented
sample includes:

-   30 employees.
-   4 pods (Alpha, Beta, Gamma and Delta), with pod leads.
-   4 processes: CS, ACC, RET and NDR.
-   24 brands.
-   19 single-resource brands (approximately 79%) and 5 multi-resource
    brands with 2--3 employees.
-   Primary and backup assignments.
-   Different weekly-off patterns, including fixed and rotating
    patterns.
-   Global and brand-specific holiday examples.
-   Leave and comp-off examples.
-   Monthly roster data for the demonstration period.

All sample people, brands and business data are fictional. Some sample
holiday dates, including Holi and Diwali, are assumptions for
demonstration purposes and should be updated for a real operating
calendar.

To seed the database:

``` bash
python manage.py seed_data --create-admin
```

The command is designed to be safe to rerun using the sample business
identifiers. Check the command's help before using optional reset flags
on a database containing data you want to keep.

------------------------------------------------------------------------

## Run locally on Windows

These instructions use **Command Prompt (CMD)**.

### Prerequisites

-   Python 3.12
-   Git (if cloning from GitHub)
-   MySQL Server for the MySQL setup, or SQLite for a quick local run

### 1. Open the project folder

``` cmd
cd /d path\to\smart_roster
```

### 2. Create and activate a virtual environment

``` cmd
python -m venv .venv
.venv\Scripts\activate.bat
```

### 3. Install dependencies

``` cmd
python -m pip install -r requirements.txt
```

### 4. Create the environment file

``` cmd
copy .env.example .env
```

Open `.env` in a text editor and set a strong, private Django secret
key. Keep `.env` out of GitHub.

For a basic SQLite setup, use:

``` env
DB_ENGINE=sqlite
```

### 5. Create database tables and sample data

``` cmd
python manage.py migrate
python manage.py seed_data --create-admin
```

### 6. Run the development server

``` cmd
python manage.py runserver
```

Open:

``` text
http://127.0.0.1:8000/
```

Sign in with the development demo credentials documented by the project
owner. Change the default password before using any shared or deployed
environment.

------------------------------------------------------------------------

## MySQL configuration

The project supports MySQL through Django's MySQL database backend. The
local development setup was tested by creating a database, connecting
Django, running migrations and loading the sample data.

Create a database and a dedicated database user in MySQL. Use a strong
password and do not commit it to source control.

Example SQL (replace the password with your own private value):

``` sql
CREATE DATABASE smart_roster_db
CHARACTER SET utf8mb4
COLLATE utf8mb4_unicode_ci;

CREATE USER 'roster_user'@'localhost'
IDENTIFIED BY 'choose-a-strong-private-password';

GRANT ALL PRIVILEGES ON smart_roster_db.*
TO 'roster_user'@'localhost';
```

Install the MySQL driver configured for the project. The MySQL driver
setup used in the local Windows environment included PyMySQL because the
native `mysqlclient` extension was blocked by the machine's
application-control policy:

``` cmd
python -m pip install PyMySQL
```

Set the database variables in `.env`:

``` env
DB_ENGINE=mysql
DB_NAME=smart_roster_db
DB_USER=roster_user
DB_PASSWORD=your-private-database-password
DB_HOST=127.0.0.1
DB_PORT=3306
```

Then run:

``` cmd
python manage.py check
python manage.py migrate
python manage.py seed_data --create-admin
python manage.py runserver
```

The seed command creates sample data in the database selected by `.env`.
Running migrations against a new MySQL database creates its tables; it
does not automatically copy records from an existing SQLite database.

------------------------------------------------------------------------

## Demo scenario

The application includes a demonstration scenario for showing how the
system responds when an employee becomes unavailable.

A typical walkthrough is:

1.  Start with a generated roster.
2.  Apply approved leave to the sole assigned employee for a
    single-resource brand on a required working day.
3.  Revalidate the roster and inspect the resulting no-coverage
    conflicts for the affected processes.
4.  Review the suggested replacement candidates.
5.  Use the manager actions to assign a replacement, apply the leave to
    the roster or acknowledge the risk.
6.  Revalidate and inspect the updated coverage.
7.  Repeat the scenario for a multi-resource brand, where another team
    member may maintain coverage while one employee is unavailable.

This demonstrates the difference between a single-resource brand and a
brand with multiple employees, and shows why the manager still needs to
review important scheduling decisions.

------------------------------------------------------------------------

## Testing

Run the automated test suite with:

``` cmd
python manage.py test tests
```

The project README's original development test run recorded **40 passing
tests**. The tests cover areas including:

-   Master data and assignment creation.
-   Single-resource and multi-resource scheduling.
-   Operating calendars, Sundays and holidays.
-   Approved leave and compensatory offs.
-   Minimum staffing and weekday overrides.
-   Rotating weekly offs.
-   Backup coverage and shift clashes.
-   Conflict detection after changes.
-   Manual roster editing and validation.
-   Publishing restrictions.
-   Exports, sample-data seeding and demo scenario behaviour.

The recorded 40-test result refers to the development test run. Run the
test suite in the environment you intend to submit or deploy, and report
the result from that run.

------------------------------------------------------------------------

## Assessment questions: how I think

### 1. How did you model employees, brands, processes, pods, and their relationships?

I modelled employees, brands, processes and pods as separate entities
because each represents a different part of the company's operations. An
employee belongs to a pod, and a pod can have an employee as its lead. I
used an Assignment entity to connect an employee with a particular brand
and process. This creates a many-to-many relationship, because one
employee can work on multiple brand-process combinations and one
brand-process can have multiple employees. The assignment also records
whether an employee is a primary resource or a backup. I kept operating
rules separate from employee availability so that a brand's working
calendar is not confused with an employee's personal schedule.

### 2. How does your system generate a roster and determine whether it satisfies the required constraints?

The system first loads the month's employees, assignments, brand-process
operating rules, holidays, approved leaves and compensatory offs. It
then allocates rotating weekly offs, assigns available employees to the
brand-process workloads that operate on each date, and tries to fill
remaining coverage gaps using eligible backup employees. After
generation, a separate validator checks the saved roster against minimum
staffing, assignments, leave, weekly offs, holidays, workload and shift
rules. The system reports any requirement it cannot satisfy as a
conflict instead of hiding the gap. I consider the roster ready for
publishing only when there are no open critical conflicts.

### 3. How did you handle the difference between single-resource brands and brands with multiple employees?

I used the same data model and scheduling rules for both types, rather
than creating separate scheduling systems. The difference is represented
through the number of employee assignments for each brand and process.
In a single-resource brand, the assigned employee's absence can create a
coverage gap, unless an eligible backup is available. In a
multi-resource brand, the remaining employees may continue to meet the
minimum staffing requirement. The validator checks the actual required
and available coverage for each day, so it can identify whether the team
is still covered or whether a gap exists.

### 4. What happens when an employee manages all processes for a brand but is unavailable on a required working day?

The generator does not count an employee who is on approved leave, a
compensatory off or a weekly off as available for that day. If that
employee is the only eligible resource for the brand's required
processes, the validator raises a critical no-coverage conflict for each
affected process. The conflict explains the reason for the gap and the
manager can review suggested replacement candidates. The manager can
assign an eligible replacement, change the roster or acknowledge the
risk. The system does not silently mark the work as covered when nobody
is available.

### 5. How does your system handle conflicting requirements or insufficient staffing?

I prioritised mandatory business requirements, such as minimum staffing
and employee availability, over soft preferences such as a preferred
weekly-off day. The generator tries to create the best feasible roster
using the configured rules. If there are not enough eligible employees,
it records the remaining issue as a conflict with an appropriate
severity and explanation. This makes the trade-off visible to the
operations manager. A preference warning can be reviewed and
acknowledged, but acknowledging a warning does not create coverage or
fix a critical gap.

### 6. Which parts of the process did you automate, and which did you leave under human control? Why?

I automated monthly roster generation, rotating weekly-off allocation,
backup coverage attempts, validation, leave-impact analysis, replacement
suggestions and conflict explanations. I kept leave approval, final
replacement decisions, conflict acknowledgement, manual roster changes
and publishing under manager control. I made this choice because
scheduling affects both customer commitments and employees' time off.
The system can calculate options and explain the impact, but a manager
should remain responsible for decisions that affect people and service
delivery.

### 7. If the company doubled its team and brand count, what would you change about your solution?

If the company doubled in size, I would first move roster generation
into background jobs so that users do not have to wait for a
long-running scheduling task. I would evaluate a constraint-optimisation
approach, such as CP-SAT or integer linear programming, for more complex
scheduling cases while keeping the independent validator. I would also
introduce role-based access for managers and pod leads, bulk import
tools, audit history, database performance improvements and caching for
frequently viewed roster data. These changes would help the system scale
while keeping scheduling decisions traceable.

### 8. If you had one additional week to work on this, what would you improve first?

I would first improve the leave-change workflow by adding minimal
re-planning. Instead of only showing replacement candidates, the system
could propose the smallest set of roster changes needed to restore
coverage and let the manager compare the alternatives. I would then add
stronger end-to-end browser tests and improve the deployed demo so that
the full workflow can be tested reliably. This would improve both the
practical usefulness of the system and confidence in the final roster.

------------------------------------------------------------------------

## Deployment

The application must be deployed to a public host before a public
application URL can be submitted. The repository includes a `Procfile`
and `build.sh` intended to support a platform that can run a Django
application with Gunicorn.

A typical deployment process is:

1.  Push the project source code to GitHub.

2.  Create a web service on a supported hosting platform.

3.  Configure the build command using the included build script, if
    supported by the platform.

4.  Configure the start command:

    ``` bash
    gunicorn config.wsgi
    ```

5.  Set environment variables in the hosting platform's dashboard:

    -   `DJANGO_DEBUG=False`
    -   `DJANGO_SECRET_KEY` with a long, unique secret value
    -   `DJANGO_ALLOWED_HOSTS` with the deployed hostname
    -   `DJANGO_CSRF_TRUSTED_ORIGINS` with the HTTPS origin
    -   `DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST` and
        `DB_PORT` for the production database
    -   `DEMO_ADMIN_PASSWORD` if the seed command needs to create the
        demo admin while debug mode is disabled

6.  Run migrations and seed the demonstration data once.

7.  Open the public URL and test login, roster generation, conflict
    handling, static files and exports.

Do not put production credentials in this README or commit a real `.env`
file. Use the hosting provider's environment-variable settings.

**Deployment status:** Add the actual public URL here after deployment
is complete.

``` text
Application URL: Not deployed yet
```

------------------------------------------------------------------------

## Known limitations and future improvements

-   The scheduling engine uses a deterministic heuristic. It is not a
    global optimiser and may report a gap that a more advanced
    optimisation algorithm could potentially avoid.
-   Weekly-off logic is based on day-level scheduling; the application
    does not implement a complete hour-by-hour shift model.
-   The current application is designed around an operations-manager
    workflow. More granular roles for pod leads and other users could be
    added.
-   Notifications such as email or Slack alerts are not currently part
    of the core workflow.
-   Conflict explanations are rule-based rather than generated by an
    external AI model.
-   Sample holiday dates are demonstration assumptions and should be
    maintained for a real organisation.
-   A production deployment and production database configuration should
    be tested separately from local development.

### Improvements I would prioritise

1.  Minimal re-planning after leave or availability changes.
2.  Side-by-side comparison of alternative roster plans.
3.  Role-based access and pod-lead approval.
4.  Notifications when a published roster changes.
5.  A richer shift model with start times, end times and working hours.
6.  More end-to-end browser tests and deployment monitoring.

------------------------------------------------------------------------

## Project submission

For the DataStraw assessment, provide:

-   **Application URL:** Add the public deployed URL after deployment.
-   **GitHub repository:** Add the public repository URL.
-   **Demo video:** Add the link to the 3--5 minute walkthrough.
-   **Approach note:** Include a short summary of the architecture and
    scheduling decisions.
-   **LinkedIn profile:** Add your accessible LinkedIn profile URL.
