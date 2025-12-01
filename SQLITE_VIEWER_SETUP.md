# SQLite Web Viewer Setup - Railway

## Quick Setup Guide

### 1. Set Environment Variable in Railway

Go to your Railway project dashboard and add:

```
ADMIN_PASSWORD=your-secure-password-here
```

**Important:** Choose a strong password! This protects your database viewer.

### 2. Deploy the Changes

The changes are already committed to your repository. Railway will automatically:
- Install `sqlite-web` from `requirements.txt`
- Deploy the new admin endpoints

### 3. Start the Database Viewer

Once deployed, start the viewer with:

```bash
curl -X GET https://your-app.railway.app/admin/db-viewer/start \
  -H "X-Admin-Password: your-secure-password-here"
```

**Response:**
```json
{
  "status": "started",
  "message": "Database viewer started successfully",
  "url": "/db",
  "pid": 12345,
  "note": "The viewer is running in read-only mode for safety"
}
```

### 4. Access the Viewer

Open your browser and go to:
```
https://your-app.railway.app/db
```

You'll see a beautiful web interface where you can:
- Browse all tables (users, web_sessions, feed_configs, etc.)
- Run SQL queries
- Export data
- View table schemas

**Note:** The viewer runs in **read-only mode** for safety - you can view but not modify data.

### 5. Stop the Viewer (When Done)

```bash
curl -X GET https://your-app.railway.app/admin/db-viewer/stop \
  -H "X-Admin-Password: your-secure-password-here"
```

### 6. Check Status

```bash
curl -X GET https://your-app.railway.app/admin/db-viewer/status \
  -H "X-Admin-Password: your-secure-password-here"
```

## Available Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/admin/db-viewer/start` | GET | Start the SQLite web viewer |
| `/admin/db-viewer/stop` | GET | Stop the SQLite web viewer |
| `/admin/db-viewer/status` | GET | Check if viewer is running |

All endpoints require the `X-Admin-Password` header.

## Security Features

✅ Password-protected endpoints  
✅ Read-only mode (cannot modify data)  
✅ Only accessible when explicitly started  
✅ Can be stopped when not needed  

## Troubleshooting

**"Admin password not configured"**
- Make sure you set `ADMIN_PASSWORD` in Railway environment variables

**"Database not found"**
- Check that your database path is correct (`data/telegram_feed.db`)
- Make sure Railway volume is mounted at `/data`

**"sqlite_web not found"**
- Railway should automatically install it from `requirements.txt`
- Check deployment logs for installation errors

## Alternative: Railway CLI

You can also use Railway CLI for quicker access:

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login and link project
railway login
railway link

# Access your production database locally
railway run cat data/telegram_feed.db > production.db

# Then open with any local SQLite browser
```

---

**Next Step:** Set the `ADMIN_PASSWORD` environment variable in your Railway dashboard, then start the viewer!
