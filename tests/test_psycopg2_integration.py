# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------

"""Integration tests for psycopg2 instrumentation.

Validates that psycopg2 is registered in the distro's supported library lists
and that the Psycopg2Instrumentor can be loaded.  Full span-generation tests
are skipped when the ``psycopg2`` package is not installed.
"""

import unittest

import pytest

psycopg2 = pytest.importorskip("psycopg2")

# pylint: disable=wrong-import-position
from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor  # noqa: E402

from microsoft.opentelemetry._constants import (  # noqa: E402
    _SUPPORTED_INSTRUMENTED_LIBRARIES,
)

# pylint: enable=wrong-import-position


class TestPsycopg2InstrumentationConfig(unittest.TestCase):
    """Verify psycopg2 is registered in the distro's supported library lists."""

    def test_psycopg2_in_supported_libraries(self):
        self.assertIn("psycopg2", _SUPPORTED_INSTRUMENTED_LIBRARIES)


class TestPsycopg2InstrumentorLifecycle(unittest.TestCase):
    """Verify the Psycopg2Instrumentor can be loaded and reports dependencies."""

    def test_instrumentation_dependencies(self):
        inst = Psycopg2Instrumentor()
        deps = inst.instrumentation_dependencies()
        dep_str = " ".join(deps)
        self.assertIn("psycopg2", dep_str)


if __name__ == "__main__":
    unittest.main()
