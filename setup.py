"""
setup.py for stock_event_engine — PyPI packaging configuration.
"""

from setuptools import setup, find_packages

setup(
    name="stock_event_engine",
    version="1.0.0",
    author="Stock Platform Team",
    author_email="team@stockplatform.example.com",
    description="A reusable Python library for stock market analytics, "
                "signal detection, alerting, and portfolio analysis.",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/stockplatform/stock-event-engine",
    packages=find_packages(include=["stock_event_engine", "stock_event_engine.*"]),
    python_requires=">=3.9",
    install_requires=[
        "numpy>=1.24.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.4.0",
            "pylint>=3.0.0",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Intended Audience :: Financial and Insurance Industry",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Office/Business :: Financial :: Investment",
    ],
    keywords="stock market analytics trading indicators signals",
)
