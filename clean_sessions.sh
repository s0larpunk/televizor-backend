#!/bin/bash
# Clean all sessions and start fresh

echo "🧹 Cleaning sessions and data..."

# Stop servers (if running)
echo "Stopping servers..."
pkill -f "uvicorn main:app"
pkill -f "npm run dev"

# Remove Telegram sessions
echo "Removing Telegram sessions..."
rm -rf sessions/

# Remove database (optional - uncomment if you want to clear user data)
# echo "Removing database..."
# rm -f telegram_feed.db

echo "✅ Cleanup complete!"
echo ""
echo "To start fresh:"
echo "1. Clear browser cookies for localhost:3000"
echo "2. Start backend: uvicorn main:app --reload --host 127.0.0.1 --port 8000"
echo "3. Start frontend: cd ../frontend && npm run dev"
