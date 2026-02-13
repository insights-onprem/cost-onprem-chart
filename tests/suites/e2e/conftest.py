"""
E2E suite fixtures.

Most fixtures are inherited from the root conftest.py.
This file contains E2E-specific fixtures only.
"""

# E2E-specific fixtures are defined in the test classes themselves
# to ensure proper scoping (class-level for the complete flow tests).
#
# See test_complete_flow.py for:
# - e2e_cluster_id: Unique cluster ID for test run
# - e2e_test_data: Generated test CSV data
# - registered_source: Source registration with cleanup
