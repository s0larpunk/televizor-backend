import x402.facilitator
import inspect

print("FacilitatorClient members:")
print(dir(x402.facilitator.FacilitatorClient))

print("\nFacilitatorClient.verify_payment signature:")
try:
    print(inspect.signature(x402.facilitator.FacilitatorClient.verify_payment))
except Exception as e:
    print(e)
