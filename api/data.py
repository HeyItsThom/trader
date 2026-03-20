"""
Vercel serverless entry point for /api/data.
Re-exports the Flask app from the root api.py so Vercel runs it as a WSGI function.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import app  # noqa: F401  — Vercel picks up this WSGI app
