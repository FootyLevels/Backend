"""
Setup script for FootyLevels package.
"""

from setuptools import setup, find_packages
from pathlib import Path

# Read README for long description
readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text() if readme_file.exists() else ""

setup(
    name="footylevels",
    version="0.1.0",
    author="FootyLevels Team",
    description="AI-powered soccer skill analysis using pose estimation",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/footylevels",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    python_requires=">=3.8",
    install_requires=[
        "mediapipe>=0.10.0",
        "opencv-python>=4.8.0",
        "numpy>=1.24.0",
        "ffmpeg-python>=0.2.0",
        "dtaidistance>=2.3.0",
        "fastdtw>=0.3.4",
        "fastapi>=0.100.0",
        "uvicorn>=0.23.0",
        "python-multipart>=0.0.6",
        "pyyaml>=6.0",
        "pydantic>=2.0.0",
        "matplotlib>=3.7.0",
        "plotly>=5.15.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.4.0",
            "pytest-asyncio>=0.21.0",
            "ipython>=8.14.0",
            "jupyter>=1.0.0",
        ],
        "gpu": [
            "tensorflow>=2.13.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "footylevels-demo=scripts.demo:main",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Intended Audience :: Education",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Topic :: Multimedia :: Video",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
    keywords="soccer football pose-estimation computer-vision skill-analysis",
)
