# Canvas Course Merger

Canvas Course Merger is a FastAPI application for safely processing approved Canvas course-merger requests. A Canvas administrator validates SIS-backed sections, creates a blank destination course, and cross-lists the source sections through a guided, auditable workflow.

The project demonstrates OAuth 2.0 integration with Canvas, live API validation, background job processing, encrypted credential storage, database-backed workflow state, and defensive handling of partial failures.

## Workflow

1. Sign in with a Canvas administrator account.
2. Record the faculty identity and external request or ticket reference.
3. Add at least two source SIS section IDs to a destination group, manually or by CSV/XLSX upload.
4. Validate the sections, term, subaccounts, permissions, and current cross-list state against Canvas.
5. Review the generated destination course details and explicitly confirm the request.
6. Track course creation and section-level progress while a separate worker processes the job.

The application creates a blank, unpublished destination course with no SIS course ID. It does not publish courses, copy content, de-cross-list sections, or automatically undo successful moves.

## Safety and architecture

```text
Browser
  -> FastAPI web application
  -> validation and workflow services
  -> database-backed queue and audit history
  -> background worker
  -> Canvas REST API
```

Key safeguards include:

- Canvas OAuth 2.0 with scoped API access and encrypted token storage
- live validation before confirmation and again immediately before execution
- administrator permission and manageable-account checks
- explicit confirmation that is invalidated when Canvas data changes
- durable request, attempt, progress, and audit records
- retry support for failed section moves without recreating the destination
- no automatic retry when course creation returns an ambiguous result

When all source sections share an approved subaccount, it is selected automatically. Mixed-subaccount requests require an explicit destination choice from accounts the administrator can manage. `CANVAS_ALLOWED_ACCOUNT_IDS` can optionally restrict eligible destination accounts.

## Technology

- Python 3.12+
- FastAPI, Jinja, and lightweight JavaScript
- SQLAlchemy and Alembic
- SQLite for local evaluation
- MySQL 8.0+ for shared deployment
- A separate database-backed worker
- Pytest and Ruff

## Local setup

Create a scoped Canvas API Developer Key with this redirect URI:

```text
http://localhost:8000/auth/callback
```

Enable enforced scopes, expiring tokens, and the endpoints listed in [`.env.example`](.env.example). The signed-in Canvas role must also be able to read SIS data and manage courses and sections in the relevant accounts.

Then install and configure the application:

```bash
git clone https://github.com/tavernbiscuit/canvas_course_merger.git
cd canvas_course_merger
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --constraint constraints.txt -e '.[dev]'
cp .env.example .env
```

Update `.env` with the Canvas Test URL, Developer Key credentials, root account ID, and two unique secrets:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Use those values for `APP_SESSION_SECRET` and `APP_TOKEN_ENCRYPTION_KEY`. Initialize the database and start the web application:

```bash
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open [http://localhost:8000](http://localhost:8000). Validation is read-only. To execute confirmed requests in Canvas Test, start the worker in a second terminal:

```bash
source .venv/bin/activate
canvas-merger-worker
```

Use disposable test sections. Stopping the application does not reverse changes already made in Canvas.

## Deployment

Shared deployments require:

- HTTPS behind a reverse proxy
- MySQL 8.0 or newer; MariaDB is not supported
- production Canvas credentials and a matching OAuth redirect URI
- unique session and token-encryption secrets
- separate supervised web and worker processes
- database backups and preservation of the token-encryption key

Run migrations before starting the services:

```bash
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers
.venv/bin/canvas-merger-worker
```

The included `Dockerfile` builds an OCI-compatible image. Health endpoints are available at `/health/live` and `/health/ready`.

## Development and testing

The automated test suite uses isolated fakes and does not require Canvas or MySQL:

```bash
pytest
ruff check app tests migrations
alembic check
```

An optional MySQL integration test is available for an empty, disposable database:

```bash
TEST_MYSQL_DATABASE_URL='mysql+pymysql://user:password@127.0.0.1/test_canvas_merger?charset=utf8mb4' \
  pytest tests/test_mysql.py
```
