"""Shipped default policy files.

A package only so setuptools' `packages.find` discovers the directory and
the `package-data` glob in pyproject.toml applies to it. Without this, an
installed copy has no policy.toml and refuses to start.
"""
