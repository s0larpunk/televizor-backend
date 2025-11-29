import asyncio
import logging
from unittest.mock import MagicMock, patch, AsyncMock
from tbank_payment import tbank_service

# Configure logging to capture output
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tbank_payment")

async def test_logging():
    print("Testing T-Bank logging...")
    
    # Mock response
    mock_response = MagicMock()
    mock_response.text = '{"Success": true, "PaymentId": "12345", "PaymentURL": "https://example.com"}'
    mock_response.json.return_value = {"Success": True, "PaymentId": "12345", "PaymentURL": "https://example.com"}
    mock_response.status_code = 200

    # Patch httpx.AsyncClient
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_response

        # Call create_payment
        try:
            # We need to patch the logger module where it's defined in tbank_payment
            with patch("tbank_payment.logger") as mock_logger:
                await tbank_service.create_payment(
                    order_id="test_order",
                    amount=10000,
                    description="Test Payment",
                    success_url="http://success",
                    fail_url="http://fail"
                )
                
                # Verify logger was called with the response text
                found = False
                for call in mock_logger.info.call_args_list:
                    args, _ = call
                    if "T-Bank Init response:" in args[0] and mock_response.text in args[0]:
                        found = True
                        print("✅ Logging verification successful: Found response text in logs.")
                        break
                
                if not found:
                    print("❌ Logging verification failed: Response text not found in logs.")
                    print("Calls were:", mock_logger.info.call_args_list)
                    
        except Exception as e:
            print(f"❌ Test failed with exception: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_logging())
