"""MockPay: a simulated payment provider.

Conceptually an external system that happens to share our process and database. Our `payments`
app reaches it only through the PaymentGateway port (outbound) and signed webhooks (inbound):
delete this package, add a Razorpay adapter, and nothing in bookings or payments changes.
"""
