"""
External API tests via gateway route.

These tests validate the API contract by making direct HTTP calls through
the external gateway route with JWT authentication. This tests the full
stack: gateway routing, JWT validation, header injection, and backend services.

Run with: pytest -m api
"""
