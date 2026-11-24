"""Decoupled visualization layer (Streamlit dashboard).

Only this package imports GUI libraries. ``replay`` and ``render`` are pure Python so they can be tested
headlessly; ``charts`` uses Altair; ``app`` is the Streamlit entry point.
"""
