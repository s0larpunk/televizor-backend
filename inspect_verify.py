import x402.facilitator
import x402.fastapi.middleware
import inspect

print("FacilitatorClient.verify signature:")
try:
    print(inspect.signature(x402.facilitator.FacilitatorClient.verify))
except Exception as e:
    print(e)

print("\nrequire_payment source:")
try:
    print(inspect.getsource(x402.fastapi.middleware.require_payment))
except Exception as e:
    print(e)
