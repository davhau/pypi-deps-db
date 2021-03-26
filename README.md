## A dependency database for python packages on pypi

This data is updated twice per day by crawling pypi. (see [pypi-crawlers](https://github.com/DavHau/mach-nix/tree/master/pypi-crawlers))

It allows deterministic dependency resolution which is required by [mach-nix](https://github.com/DavHau/mach-nix) to generate highly reproducible python environments.

The data set contains dependencies for sdist and wheel packages, though the sdist dependencies are not fully complete, since there is no fixed standard for declaring dependencies in sdist packages.
Most sdist packages which use setuptools/setup.py are contained in the data.
