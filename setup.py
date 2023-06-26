from setuptools import setup, find_packages

with open("requirements.txt") as f:
	install_requires = f.read().strip().split("\n")

# get version from __version__ variable in erpnext_biotime/__init__.py
from erpnext_biotime import __version__ as version

setup(
	name="erpnext_biotime",
	version=version,
	description="ERPNext integration with BioTime",
	author="Axentor",
	author_email="hello@axentor.co",
	packages=find_packages(),
	zip_safe=False,
	include_package_data=True,
	install_requires=install_requires
)
