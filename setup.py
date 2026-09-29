from pathlib import Path
import os
import runpy
import sys

from setuptools import Distribution, find_packages, setup
from setuptools.command.build_py import build_py

ROOT = Path(__file__).parent
VERSION = runpy.run_path(str(ROOT / "src/privpy/_version.py"))["__version__"]
if sys.platform == "darwin":
    os.environ.setdefault("MACOSX_DEPLOYMENT_TARGET", "14.0")


class Build(build_py):
    def run(self):
        super().run()
        build = runpy.run_path(str(Path(__file__).parent / "src/privpy/build.py"))
        destination = Path(self.build_lib) / "privpy" / build["library_name"]()
        build["build_native"](destination)


class NativeDistribution(Distribution):
    def has_ext_modules(self):
        return True


setup(
    name="privpy",
    version=VERSION,
    description="Experimental native private computation regions for Python",
    long_description=(ROOT / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    url="https://github.com/technoyoda/privpy",
    project_urls={
        "Source": "https://github.com/technoyoda/privpy",
        "Issues": "https://github.com/technoyoda/privpy/issues",
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: C++",
        "Operating System :: MacOS",
        "Operating System :: POSIX :: Linux",
    ],
    python_requires=">=3.9",
    package_dir={"": "src"},
    packages=find_packages("src"),
    package_data={"privpy": ["native/*.cpp"]},
    include_package_data=False,
    cmdclass={"build_py": Build},
    distclass=NativeDistribution,
    extras_require={"test": ["hypothesis>=6.100,<7", "coverage>=7,<8"]},
)
