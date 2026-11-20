# Employee Registration & Event Check-In

## Team briefing

This guide explains what the application does, how its parts work together, and what its technical terms mean. It is intended for a project walkthrough or team presentation.

### One-minute overview

The application supports the employee-registration and event-entry journey for Kyndryl Year End Celebrations 2026:

1. Employees register with an eligible `@kyndryl.com` address and provide event and emergency-contact details.
2. The application validates the information, prevents duplicate email registrations, and displays a unique entry code.
3. On event day, an administrator verifies the code and records attendance. If the code is unavailable, the administrator can verify the registered name and email instead.

It is a small, single-project web application: the browser provides the screens, a Python server handles requests and rules, and SQLite stores registrations.

## How the parts fit together

```text
Employee or administrator
          |
          | Opens pages, submits forms, requests data
          v
Browser interface
index.html + styles.css + app.js
          |
          | Same-origin HTTP requests; JSON for API data
          v
Python application server
server.py
          |
          | SQL reads and writes
          v
Local SQLite database
registrations.sqlite3
```

When EmailJS settings are supplied in `.env`, the Python server sends a confirmation through EmailJS to the registrant's validated Kyndryl email address. It includes the attendee's name, work email, mobile, meal and beverage choices, travel mode, event details, and entry code; emergency-contact details are excluded. If delivery fails, the on-screen entry code remains available and an administrator can retry from the registration list.

For automatic delivery, `server.py` makes an HTTPS `POST` to EmailJS's REST API. The EmailJS private key stays in the server's `.env`; neither the private key nor the EmailJS request is sent from browser JavaScript.

The server uses Python's `ThreadingHTTPServer`, which lets it handle more than one browser request at a time by using threads. It also serves the HTML, CSS, and JavaScript files from the project directory.

## Main project files

| File | Responsibility |
| --- | --- |
| [`index.html`](./index.html) | Page structure, navigation, registration form, event information, and admin dashboard. |
| [`styles.css`](./styles.css) | Colors, spacing, typography, and layouts for desktop and mobile screens. |
| [`app.js`](./app.js) | Browser behavior: form submission, API calls, page switching, and dashboard updates. |
| [`server.py`](./server.py) | Local web server, API routes, input validation, administrator sessions, email handling, CSV export, and database operations. |
| [`emailjs-template.html`](./emailjs-template.html) | Ready-to-paste HTML body for the EmailJS confirmation template. |
| [`tests/test_server.py`](./tests/test_server.py) | Automated tests for registration rules, duplicate prevention, check-in, and email configuration behavior. |
| [`.env.example`](./.env.example) | Names of optional local settings. Copy it to `.env` and add local values; never commit `.env`. |
| [`README.md`](./README.md) | Quick-start instructions and configuration notes. |

At first start, the server creates `registrations.sqlite3`. This runtime database contains employee and emergency-contact information and should be kept private.

## Important workflows

### Employee registration

1. The employee fills in the form in `index.html`.
2. The browser checks basic required fields and sends the form as JSON to `POST /api/register`.
3. `server.py` validates the data again. Server-side validation is essential because browser checks alone can be bypassed.
4. SQLite enforces case-insensitive uniqueness for the official email address. A duplicate registration is rejected.
5. The server creates a random event entry code and tries to send the confirmation using EmailJS if configured.
6. `app.js` displays the entry code and EmailJS delivery result.

The registration form collects a travel mode for the confirmation summary. The drinking/travel rule is enforced on the server: someone indicating they will drink must choose a travel mode, and drinking with self-drive is rejected.

### Requirement-to-implementation map

| Challenge requirement | Implementation |
| --- | --- |
| Only Kyndryl employees can register | Server validates the `@kyndryl.com` email domain. |
| Required employee and emergency-contact details | Registration form collects and server validates the values before storage. |
| Confirm attendance and accept event terms | Both confirmations are required and recorded. |
| Prevent drinking with self-drive | Server rejects that combination even if browser checks are bypassed. |
| Prevent duplicate registration | SQLite has a case-insensitive unique constraint on official email. |
| Confirmation and entry eligibility | Successful registration returns a random entry code and configured EmailJS sends it to the registrant's Kyndryl email address. |
| Restricted administration | Admin password from local configuration creates an expiring session cookie. |
| Search, filtering, statistics, and CSV export | Protected admin API routes query the registrations table and serialize CSV. |
| Verify and record event attendance | Admin can check in by entry code or name-plus-email fallback; server stores a UTC timestamp and blocks repeat check-ins. |
| Public event guidance | Event page reads public event details from the server; unsupplied details remain placeholders. |
| Mobile and desktop browser support | CSS media queries adapt the page at tablet and phone widths. |

### Administrator and event-day check-in

1. An administrator signs in with the password configured as `APP_ADMIN_PASSWORD`.
2. The server creates a temporary administrator session and sends it in an `HttpOnly`, `SameSite=Strict` cookie.
3. The dashboard requests counts and registration rows from the protected admin API.
4. Staff can check in an attendee by entry code or use the fallback form with both registered name and official email.
5. The server records the attendance time in UTC. Unknown codes and repeat check-ins are rejected.
6. Administrators can search, filter, export the registration list to CSV, and retry a failed or missing EmailJS confirmation.

Administrator sessions are held in memory and expire after eight hours. Restarting the server clears active sessions. This demo has one shared administrator password, not separate administrator accounts.

### Public event information

The Event information page is public and does not require sign-in. Venue, schedule, official event rules, and emergency contacts are placeholders because the challenge document says organizers will provide those details separately. Update the response for `GET /api/public/event` in `server.py` when approved event content is available.

## API route summary

| Route | Access | Purpose |
| --- | --- | --- |
| `GET /api/public/event` | Public | Returns event information and safety guidance. |
| `POST /api/register` | Public | Validates and stores a registration, then returns its entry code and email status. |
| `POST /api/admin/login` | Public | Checks the configured admin password and starts a session. |
| `GET /api/admin/session` | Public | Tells the browser whether its admin session is active. |
| `POST /api/admin/logout` | Public | Ends the current admin session. |
| `GET /api/admin/registrations` | Admin | Returns registrations; accepts `search`, `diet`, `alcohol`, and `attendance` filters. |
| `GET /api/admin/stats` | Admin | Returns registration and check-in totals. |
| `POST /api/admin/email/resend` | Admin | Retries the confirmation through EmailJS for an existing registration. |
| `POST /api/admin/attendance` | Admin | Verifies an entry code and records check-in. |
| `POST /api/admin/attendance/manual` | Admin | Performs fallback verification using name and official email. |
| `GET /api/admin/export.csv` | Admin | Downloads the registration list as a CSV file. |

The browser and server use JSON for API requests and responses. CSV is used for spreadsheet export.
Typical error responses are `400` for invalid form data, `401` for a missing or incorrect admin session, `404` for an unknown credential, and `409` for duplicate registration or check-in.

## Data captured

The `registrations` table stores:

- Employee name, mobile number, and official email.
- Emergency-contact name and mobile number.
- Dietary and alcohol preferences, plus travel mode when supplied.
- Attendance and terms confirmations.
- A unique entry code, registration time, attendance time, and email-delivery status.

The official email and entry code have database uniqueness constraints. Attendance time is empty until a successful check-in.

## Technical terms in plain language

| Term | Meaning in this project |
| --- | --- |
| **Browser / client** | The employee’s or administrator’s web browser. It displays the interface and sends requests to the server. |
| **Server** | The Python program that receives browser requests, applies rules, and reads or writes data. |
| **Thread / concurrent request** | A thread is a unit of work in the server process. The threaded server can work on multiple browser requests without making each one wait for the previous request to finish. |
| **HTTP** | The request-and-response protocol used between the browser and server. |
| **API** | A set of named routes the browser can call to perform actions or get data. For example, `/api/register` creates a registration. |
| **Endpoint / route** | A particular API address and method, such as `POST /api/register`. |
| **JSON** | A text format for structured data sent between the browser and server, such as a registration form. |
| **HTML** | The markup that defines page content and controls, such as headings, labels, and forms. |
| **CSS** | Styling rules that control appearance and responsive layout. |
| **JavaScript** | Browser code that handles form submission, navigation, API calls, and dashboard updates. |
| **Python** | The language used for the application server and its validation and database logic. |
| **SQLite** | A database stored in a local file instead of a separate database server. |
| **Database table** | A structured collection of related records. Here, `registrations` holds one row per employee registration. |
| **Schema** | The definition of a table’s columns, data types, and rules. |
| **Unique constraint** | A database rule that prevents duplicate values. The official email and entry code must be unique. |
| **Parameterized SQL** | A way to pass values separately from a database query, so user input is treated as data rather than executable SQL. |
| **Validation** | Checks that information is present and meets the rules before it is accepted. Checks run in both the browser and the server. |
| **Session** | Temporary state that remembers an administrator is signed in. |
| **Cookie** | A small value the browser sends with later requests so the server can recognize the admin session. |
| **`HttpOnly` / `SameSite`** | Cookie settings that limit access from browser scripts and reduce cross-site request risks. They do not replace HTTPS or production authentication. |
| **Environment variable / `.env` file** | A local setting read when the server starts, used for items such as the admin password and EmailJS keys. `.env` is ignored by Git and should not be shared. |
| **EmailJS** | A hosted email service. This app calls its REST API from the Python server; EmailJS receives the confirmation fields listed above. |
| **REST API** | An API called over HTTP using methods such as `POST`. The server uses it to send a message through EmailJS. |
| **Status code** | A numeric HTTP result describing a request, such as `200` for success or `409` for a conflict. |
| **CSV** | A text-based table format that can be opened in spreadsheet programs. |
| **UTC** | A standard time zone used for stored check-in timestamps so they do not depend on the administrator’s local clock. |
| **Responsive design** | Layout rules that adapt to different screen widths. This app stacks content on small screens and keeps the admin table scrollable inside its own area. |
| **Unit test** | An automated check of a small piece of application behavior, such as rejecting drinking with self-drive. |
| **Thread-safe / concurrent database access** | The SQLite connection is created per operation, and each server request can work independently. This is sufficient for a local demonstration; production traffic needs load and concurrency testing. |

## Running and demonstrating the app

From PowerShell in the project directory:

```powershell
Copy-Item .env.example .env
# Edit .env and set APP_ADMIN_PASSWORD
python server.py
```

Open `http://127.0.0.1:8000`. The default address is local to the computer; it is not automatically a shareable internet link.

For a team demonstration, use synthetic names, phone numbers, and `@kyndryl.com` test addresses:

1. Register an employee and show the confirmation code.
2. Show the on-screen confirmation and email-delivery status.
3. Sign in to Admin and show counts, filters, and CSV export.
4. Verify the test entry code and show the recorded attendance time.
5. Try an invalid or previously used code to demonstrate rejection.

Run the automated server tests with:

```powershell
python -m unittest discover -s tests -v
```

The EmailJS tests use mocked HTTP responses; they verify the request payload and HTML escaping but do not prove that EmailJS will accept or deliver a real email.

## Limitations and production considerations

This implementation is a local demonstration, not a production deployment template:

- SQLite data is stored locally and is not encrypted by the application.
- Administrator access uses one shared password; it has no organization identity provider, roles, or rate limiting.
- Local development uses HTTP. A hosted deployment needs HTTPS and controlled network access.
- Automatic email is enabled only when EmailJS keys and service/template IDs are configured. EmailJS processes the attendee data listed above. If email is not configured, the app shows the entry code and an administrator-contact message.
- Before using real employee data, have the organization approve hosting, access control, data retention, backups, and email handling.

These are operational and security decisions for a production rollout, not features that should be inferred from the local demo.
