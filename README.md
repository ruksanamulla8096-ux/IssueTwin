# IssueTwin – Multi-sector complaint intelligence platform

IssueTwin is a Flask + SQLite prototype for colleges, apartments, municipalities,
offices, large companies and factories. It groups similar reports into shared
issues, tracks affected people, supports admin workflows, detects possible
recurrences and exports issue data.

## Features in this MVP

- Organization workspaces with sector selection.
- Sectors: College / University, Apartment / Housing Society,
  Municipality / Public Services, Organization / Office, Large Company,
  Factory / Industrial Site.
- Sector-specific complaint categories.
- Reporter registration and login.
- Admin and reporter roles.
- Complaint submission with site, location, category and severity.
- TF-IDF text similarity and domain synonym normalization for duplicate detection.
- Shared issue records and complaint-to-issue mapping.
- Priority recommendations using severity, safety keywords and report count.
- Community confirmation that an issue affects the user.
- Admin assignment, status updates and resolution history.
- Possible recurrence detection after a previous issue is resolved.
- Dashboard statistics and CSV export.

## Run on Windows

1. Install Python 3.10 or newer.
2. Extract the ZIP file.
3. Open Command Prompt / VS Code terminal inside the `issuetwin_project` folder.
4. Create a virtual environment:

   ```bash
   python -m venv venv
   venv\Scripts\activate
   ```

5. Install packages:

   ```bash
   pip install -r requirements.txt
   ```

6. Start the app:

   ```bash
   python app.py
   ```

7. Open `http://127.0.0.1:5000`.

## Demo administrator

- Email: `admin@issuetwin.demo`
- Password: `Admin123!`
- Workspace: `IssueTwin Demo Campus`

Change the demo password and Flask `SECRET_KEY` before any real deployment.
Do not expose the demo credentials on a public server.

## Add reporters

Use Register to create accounts. Register with the same organization name and
sector to join the same workspace. New accounts are reporters by default.
The demo administrator can open **Manage users** and promote trusted users to
admin.

## Test the duplicate detection

1. Log in as the demo admin or create a reporter in the same workspace.
2. Submit: `Water cooler is not working near Block A`.
3. Submit another complaint in the same site with similar wording:
   `Drinking water dispenser is broken near Block A`.
4. Check whether the new report is linked to the existing issue.
5. Try a different issue such as `Projector is not working in Room 205`.

For best matching, keep the same site/building name for reports about the same
physical problem. The prototype intentionally scopes matching to one
organization and site to reduce accidental cross-site grouping.

## Important limitations

- This is a hackathon MVP, not a production complaint platform.
- TF-IDF is a lightweight text similarity technique, not a human-level semantic
  understanding system. It may miss paraphrases or group related but distinct
  reports. Tune `MATCH_THRESHOLD` after testing with real examples.
- The matching threshold defaults to 0.34. Set an environment variable
  `MATCH_THRESHOLD` to experiment with another value.
- Similarity matching uses category/site constraints and should always be
  reviewed by an administrator before action is taken.
- The prototype does not connect to real municipal emergency dispatch,
  factory safety controls, hospital systems, or company identity providers.
- Before real deployment, add HTTPS, CSRF protection, rate limiting, audit
  controls, backups, stronger access policies, data retention rules, privacy
  notices and security testing. Never use this prototype as the only channel
  for emergencies or safety-critical reporting.

## Project structure

```text
issuetwin_project/
├── app.py
├── requirements.txt
├── README.md
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── login.html
│   ├── register.html
│   ├── dashboard.html
│   ├── new_complaint.html
│   ├── issue_detail.html
│   ├── users.html
│   └── 404.html
└── static/
    └── style.css
```
