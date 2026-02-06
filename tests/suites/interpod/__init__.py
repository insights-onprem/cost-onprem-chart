"""
Internal cluster tests via test-runner pod.

These tests execute commands inside the cluster using a dedicated test-runner
pod. This allows testing internal service-to-service communication without
going through the external gateway.

Run with: pytest -m internal
"""
