# FileAway

**Temporary file sharing, without the clutter.**

FileAway is a lightweight web application for sharing files through temporary download links. Upload a file, generate a shareable URL, and let the recipient download it without needing an account. Files are designed to expire automatically after 24 hours.

## Features

- **Simple file uploads** — Upload files through a clean, responsive web interface.
- **Shareable download links** — Generate a unique URL for each uploaded file.
- **24-hour link expiry** — Links expire automatically after their validity period.
- **Automatic cleanup** — A scheduled background task removes expired files and their associated database records.
- **100 MB upload limit** — Supports file uploads up to 100 MB.
- **Direct downloads** — Recipients can download files directly through their share links.
- **Responsive interface** — Built with React and Tailwind CSS for a clean user experience.

## Tech Stack

| Layer | Technologies |
|---|---|
| Frontend | React, JavaScript, Vite |
| Styling | Tailwind CSS |
| Backend | Python, FastAPI |
| ORM | SQLAlchemy |
| Database | SQLite |
| Database migrations | Alembic |
| Background tasks | APScheduler |

## How It Works

1. **Upload:** The sender selects a file and uploads it to the application.
2. **Generate:** The backend stores the file and generates a unique share token.
3. **Share:** The application returns a URL that can be shared with the recipient.
4. **Download:** The recipient opens the link to download the file.
5. **Expire:** After 24 hours, the link expires and the scheduled cleanup process removes the file and its database record.

## Project Structure

```text
fileaway/
├── app/
│   ├── main.py
│   ├── models.py
│   ├── schemas.py
│   ├── database.py
│   └── create_tables.py
├── frontend/
│   ├── src/
│   ├── public/
│   └── package.json
├── migrations/
├── tests/
├── uploads/
├── alembic.ini
├── .env
├── .gitignore
└── README.md
```

*The `uploads/` directory and local database are runtime data, not source files. The `.env` file contains local configuration and should not be committed.*

## Getting Started

### Prerequisites

- Python 3.10 or newer
- Node.js and npm
- Git

### 1. Clone the repository

```bash
git clone <your-repository-url>
cd fileaway
```

Replace `<your-repository-url>` with the URL of this GitHub repository.

### 2. Set up the backend

Create and activate a virtual environment from the project root.

**Windows PowerShell**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install the backend dependencies:

```bash
python -m pip install fastapi "uvicorn[standard]" sqlalchemy alembic python-dotenv python-multipart apscheduler
```

Create a `.env` file in the project root with your local database configuration:

```dotenv
DATABASE_URL=sqlite:///./fileaway.db
```

Initialize the database tables using the project's table creation script:

```bash
python -m app.create_tables
```

Start the FastAPI development server:

```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The backend should now be available at:

- API: http://127.0.0.1:8000
- Interactive API documentation: http://127.0.0.1:8000/docs

### 3. Set up the frontend

Open a second terminal and navigate to the frontend directory:

```bash
cd frontend
npm install
npm run dev
```

Vite will display the local frontend URL, typically:

http://localhost:5173

Keep the backend running while using the frontend. The frontend's API configuration must point to the local FastAPI server for local development.

## API Overview

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Backend health check |
| `POST` | `/upload` | Upload a file and generate a share link |
| `GET` | `/files/share/{share_token}` | Download a file using its share token |

The upload endpoint expects a multipart form field named `uploaded_file`.

### Example Upload Request

```bash
curl -X POST http://127.0.0.1:8000/upload \
  -F "uploaded_file=@example.pdf"
```

The backend returns a response containing the uploaded file's metadata and share URL.

## Configuration

The backend uses environment-based configuration for its database connection.

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Database connection string |

The upload directory and public share URL should be configured appropriately for the deployment environment. Never commit credentials, secrets, or private environment files to version control.

## Testing

Run the backend tests from the project root:

```bash
python -m pytest
```

Ensure the required testing dependencies are installed before running the test suite. The frontend can be checked with:

```bash
cd frontend
npm run build
```

## Security and Limitations

- Anyone who possesses a valid share link can access the corresponding file until the link expires.
- Uploaded files consume server storage and network bandwidth.
- The 100 MB limit applies to the application's upload logic; the hosting infrastructure must support the same limit.
- Public deployment requires appropriate CORS configuration, persistent storage, secure environment variables, and protection against unauthorized file enumeration.
- A production deployment must use a publicly accessible API URL rather than a local development address.

## Deployment

The project is being prepared for deployment with a hosted frontend and a persistent backend environment. Production deployment requires additional configuration for public share URLs, HTTPS, persistent storage, database initialization, and background cleanup.

Deployment instructions will be added once the production configuration has been tested.

## Future Improvements

- Optional password-protected sharing
- Configurable expiration periods
- File-size and storage usage indicators
- Improved upload progress and error handling
- Rate limiting and additional abuse prevention
- Automated deployment and monitoring

## License

No license has been specified yet. Add a `LICENSE` file if you intend to distribute the project under an open-source license.
