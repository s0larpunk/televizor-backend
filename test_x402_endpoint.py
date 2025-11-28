import httpx
import asyncio

async def test():
    url = "https://x402.org/facilitator/verify"
    print(f"Testing {url}...")
    keys = ["proof", "payment", "x-payment", "header", "payload", "token"]
    async with httpx.AsyncClient() as client:
        for key in keys:
            print(f"Testing key: {key}")
            try:
                response = await client.post(url, json={key: "dummy_proof"}, follow_redirects=True)
                print(f"Key {key}: Status {response.status_code}, Response: {response.text}")
            except Exception as e:
                print(f"Error with key {key}: {e}")

if __name__ == "__main__":
    asyncio.run(test())
