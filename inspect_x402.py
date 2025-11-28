import x402
import inspect
from x402.fastapi import middleware

print("x402 package contents:")
print(dir(x402))

print("\nx402.fastapi.middleware contents:")
print(dir(middleware))

print("\nrequire_payment signature:")
try:
    print(inspect.signature(middleware.require_payment))
except Exception as e:
    print(e)
