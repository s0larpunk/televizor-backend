# Telegram Stars Payment Configuration Guide

## Setup Steps

### 1. Bot Configuration

#### Create/Configure Your Telegram Bot
```bash
# Talk to @BotFather on Telegram
/newbot  # If creating new bot
# OR
/mybots  # To manage existing bot

# Get your bot token
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ
```

### 2. Environment Variables

Add to your `.env` file:

```bash
# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_API_ID=your_api_id_here
TELEGRAM_API_HASH=your_api_hash_here

# Payment Configuration
TELEGRAM_WEBHOOK_SECRET=your_random_secret_token_here
PREMIUM_PRICE_STARS=100  # Price in Telegram Stars (minimum 1)

# Webhook URL (your production domain)
TELEGRAM_WEBHOOK_URL=https://yourdomain.com/api/payment/webhook
```

### 3. Set Webhook URL

Use this Python script or curl command:

```python
import requests

BOT_TOKEN = "your_bot_token"
WEBHOOK_URL = "https://yourdomain.com/api/payment/webhook"
SECRET_TOKEN = "your_random_secret"

url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
data = {
    "url": WEBHOOK_URL,
    "secret_token": SECRET_TOKEN,
    "allowed_updates": ["pre_checkout_query", "message"]
}

response = requests.post(url, json=data)
print(response.json())
```

OR using curl:

```bash
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://yourdomain.com/api/payment/webhook",
    "secret_token": "your_random_secret",
    "allowed_updates": ["pre_checkout_query", "message"]
  }'
```

### 4. Telegram Stars Payment Provider

**Important**: For Telegram Stars (XTR currency):
- NO payment provider token needed
- Payments are processed through Telegram's internal system
- Set `provider_token` to empty string `""` in sendInvoice

### 5. Testing

#### For Development (using ngrok):
```bash
# Start ngrok to expose local server
ngrok http 8000

# Use the ngrok URL for webhook
# Example: https://abc123.ngrok.io/api/payment/webhook
```

#### Test Payment Flow:
1.  Start your bot conversation
2. Call `/api/payment/create-invoice` endpoint
3. Check your Telegram for invoice
4. Click "Pay" button
5. Complete payment with Telegram Stars
6. Verify webhook receives updates
7. Check subscription status updated

### 6. Production Deployment

1. **SSL Required**: Webhook URL must use HTTPS
2. **Domain**: Use your production domain
3. **Port**: Standard HTTPS port (443) or custom (80, 88, 443, 8443)
4. **Security**: Validate secret_token on all webhook requests

### 7. Verify Webhook Status

```bash
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getWebhookInfo"
```

Expected response:
```json
{
  "ok": true,
  "result": {
    "url": "https://yourdomain.com/api/payment/webhook",
    "has_custom_certificate": false,
    "pending_update_count": 0,
    "allowed_updates": ["pre_checkout_query", "message"]
  }
}
```

## Telegram Stars Pricing

- Minimum: 1 Star
- Recommended: 50-500 Stars for monthly subscriptions
- 1 Star ≈ $0.01-0.02 USD (varies by region)

## References

- [Telegram Bot Payments API](https://core.telegram.org/bots/payments)
- [Telegram Stars](https://telegram.org/blog/telegram-stars)
- [sendInvoice Method](https://core.telegram.org/bots/api#sendinvoice)
- [Webhooks](https://core.telegram.org/bots/api#setwebhook)

## Troubleshooting

### Invoice not sending?
- Check bot token is correct
- Verify user has started conversation with bot
- Check bot has permission to send messages

### Webhook not receiving updates?
- Verify webhook URL is HTTPS
- Check secret_token matches
- Ensure allowed_updates includes payment events
- Check server logs for errors

### Payment not completing?
- Verify pre_checkout_query is answered quickly (<10 seconds)
- Check answerPreCheckoutQuery returns ok=true
- Review Telegram API error messages
