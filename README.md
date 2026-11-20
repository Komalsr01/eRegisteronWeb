# Kyndryl Year End Celebrations 2026

A responsive employee-registration and event check-in app built with Python's standard library, SQLite, and browser-native HTML/CSS/JavaScript. No package install is required.

For a team-ready architecture walkthrough, workflow explanation, and technical glossary, see [PROJECT_GUIDE.md](./PROJECT_GUIDE.md).

## Run locally

Create a local `.env` file from the example, fill in the administrator password, then start the app:

```powershell
Copy-Item .env.example .env
# Edit .env and set APP_ADMIN_PASSWORD
python server.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The admin dashboard is available from the **Admin** navigation link. Administrator access is disabled until `APP_ADMIN_PASSWORD` is set.

Registrations are stored in `registrations.sqlite3` in this directory. Keep that file private; it contains employee and emergency-contact information.

## Public free demo on Render

The repository includes [`render.yaml`](./render.yaml) for a public, synthetic-data-only demo. `DEMO_MODE=true` makes demo registrations non-persistent and disables email sending. A warning is shown on the page. Do not add `.env`, EmailJS keys, or real employee data to the public deployment.

1. Create a GitHub repository and push the project files. Confirm `.env` and `registrations.sqlite3` are excluded by `.gitignore`.
2. Create a Render account, choose **New > Blueprint**, and connect the GitHub repository.
3. Review the `eregisteronweb-demo` service and choose the **Free** plan, then approve creation.
4. Wait for the deploy to finish. Render will provide a public `https://...onrender.com` URL; open it and confirm the demo-only warning appears.
5. Test using synthetic data only. Render Free services can sleep after inactivity and have an ephemeral filesystem; this demo intentionally stores no registrations. Free services are for previews, not production.

## Email confirmation

The app sends confirmation email through EmailJS when it is configured. The destination is the Kyndryl email address entered by the registrant.

To enable EmailJS:

1. In EmailJS, create an email service connected to your Gmail account and create an email template. Set **From Name** to `Komal`, **To Email** to exactly `{{to_email}}`, and **To Name** to `{{to_name}}`. Do not put your Gmail address in **To Email**; the server supplies the registrant's validated `@kyndryl.com` address as `to_email`.
2. Set the template **Subject** to:

   ```text
   ✓ Registration Confirmed — Kyndryl Year End Celebrations 2026
   ```

3. Open [emailjs-template.html](./emailjs-template.html), copy its contents, and paste them into the template's **Content** editor in HTML mode. The body uses HTML-escaped attendee fields. The pass ID is the app's `KY-...` entry code; the app does not generate QR images.

4. In EmailJS, open **Account > Security** and enable **API access from non-browser environments**. The app sends via the server, so this account setting must allow server-side requests.
5. Add `EMAILJS_SERVICE_ID`, `EMAILJS_TEMPLATE_ID`, and `EMAILJS_PUBLIC_KEY` to the local `.env` file. If you enable EmailJS strict mode / private-key security, also set `EMAILJS_PRIVATE_KEY` in `.env`; never put the private key in `.env.example`, browser code, or chat. The Gmail account is configured in the EmailJS Gmail service; these are EmailJS values, not your Gmail password.
6. Restart the server. New registrations will send email automatically; admins can also retry delivery from the registration list. If email delivery fails, the entry code remains visible and the admin can retry.

EmailJS receives the recipient address, name, mobile number, meal and beverage choices, travel mode, event details, and entry code to render the requested confirmation. Emergency-contact details are not sent. The server calls EmailJS; its private key is not sent to the browser. Do not commit `.env` or paste its private key into chat. EmailJS rate-limits sends, so the server spaces requests to at least one second apart. The sender account and display name are controlled by the Gmail service configured in EmailJS; the greeting uses the attendee's first name (for example, Komal when Komal registers).

## Event content

The date, venue, gates, and event guidance currently use the details supplied for the confirmation email. Confirm these details with the event organizers before sending real invitations.

## Included workflows

- Employee registration with required attendance and terms confirmations, `@kyndryl.com` validation, dietary and alcohol preferences, emergency contact, and safe-travel validation.
- Case-insensitive duplicate-email prevention and a unique entry code.
- On-screen registration confirmation, automatic EmailJS delivery when configured, and admin resend for existing registrations.
- Restricted admin login, registration/attendance statistics, search and filters, and CSV export.
- Event entrance verification using the entry code, with name-plus-email fallback, recorded check-in time, and duplicate-check-in prevention.
- Public event guidance and mobile/desktop layouts.

## Tests

```powershell
python -m unittest discover -s tests -v
```

This is a local demonstration app, not a production deployment template. Before sharing it publicly, deploy behind HTTPS, configure managed administrator authentication and secret storage, and confirm the event's privacy, retention, and security requirements.
