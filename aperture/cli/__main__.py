# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright © 2026 lucya.systems — https://lucya.systems
"""`python -m aperture.cli` -- the documented way in."""

import sys

from .entry import main   # not `from . import main`: that reads as the old main.py

sys.exit(main())
