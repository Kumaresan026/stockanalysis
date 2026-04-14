from setuptools import setup, find_packages

setup(
    name="stock-event-engine",
    version="0.1.0",
    author="Kumaresan",
    author_email="kumaresan2126@gmail.com",
    description="Event-driven stock analytics and alert engine",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    packages=find_packages(exclude=["tests*", "build*", "dist*"]),
    python_requires=">=3.8",
    install_requires=[
        "numpy>=1.24.0",
        "pandas>=2.0.0",
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Intended Audience :: Developers",
        "Topic :: Office/Business :: Financial :: Investment",
    ],
)