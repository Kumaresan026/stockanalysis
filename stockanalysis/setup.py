"""
setup.py for the stock-analysis Django application.

Note: The stock_event_engine analytics library is an EXTERNAL dependency
managed by the stock-event-engine package (v0.1.0). It is NOT bundled here.
"""

from setuptools import setup, find_packages

setup(
    name="stockanalysis",
    version="1.0.0",
    author="Kumaresan",
    author_email="kumaresan2126@gmail.com",
    description="Cloud-based stock market analysis platform (Django).",
    packages=find_packages(exclude=["stock_event_engine*", "tests*"]),
    python_requires=">=3.9",
    install_requires=[
        # External analytics library — installed from the bundled
        # stock-event-engine/ directory via .ebextensions/02_packages.config.
        "stock-event-engine==0.1.0",
        "Django>=4.2,<5.0",
        "gunicorn>=21.2.0",
        "boto3>=1.28.0",
        "botocore>=1.31.0",
        "requests>=2.31.0",
        "pandas>=2.0.0",
        "numpy>=1.24.0",
        "python-dotenv>=1.0.0",
        "whitenoise>=6.5.0",
    ],
)
