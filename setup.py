from setuptools import setup, find_packages

import os
import re
import fnmatch
import urllib.request, urllib.error, urllib.parse


UI_VERSION = "0.0.22"
UI_LOCATION = "parlay/ui/dist"
DOCS_LOCATION = "parlay/docs/_build/html"

here = os.path.abspath(os.path.dirname(__file__))
UI_LOCATION = os.path.join(here, UI_LOCATION)
DOCS_LOCATION = os.path.join(here, DOCS_LOCATION)

def get_version():
    VERSIONFILE = os.path.join('parlay', '__init__.py')
    initfile_lines = open(VERSIONFILE, 'rt').readlines()
    VSRE = r"^__version__ = ['\"]([^'\"]*)['\"]"
    for line in initfile_lines:
        mo = re.search(VSRE, line, re.M)
        if mo:
            return mo.group(1)
    raise RuntimeError('Unable to find version string in %s.' % (VERSIONFILE,))


def find_files(directory, pattern):

    for root, dirs, files in os.walk(directory):
        for basename in files:
            if fnmatch.fnmatch(basename, pattern):
                _filename = os.path.join(root, basename)
                _modulename = os.path.splitext(_filename)[0].replace(os.path.sep, ".")
                print("Found: " + _modulename)
                yield _modulename, _filename

# If we're doing a build, or index has gotten deleted somehow, grab the correct version
if not os.path.exists(UI_LOCATION + "/index.html"):
    # wget the dist file and put it in /ui/dist
    response = urllib.request.urlopen('https://github.com/PromenadeSoftware/ParlayUI/releases/download/'+UI_VERSION+'/index.html')
    html = response.read()
    if not os.path.exists(UI_LOCATION):
        os.makedirs(UI_LOCATION)

    with open(os.path.join(UI_LOCATION, "index.html"), 'w+') as index_file:
        index_file.write(html)


package_data_files = [os.path.relpath(filename, "parlay") for _, filename in find_files(UI_LOCATION, "*")]

if os.path.exists(DOCS_LOCATION):
    package_data_files.extend([os.path.relpath(filename, "parlay") for _, filename in find_files(DOCS_LOCATION, "*")])
else:
    try:
        import sphinx
        sphinx.build_main(['-b html', 'parlay/docs', DOCS_LOCATION])
        package_data_files.extend([os.path.relpath(filename, "parlay") for _, filename in find_files(DOCS_LOCATION, "*")])
    except ImportError as _:
        print("Warning: Documentation not built. Please run `pip install sphinx sphinx-rtd-theme` to build documentation.")


# Get README to use as long description
readme = ""
with open(os.path.join(here, 'README.md')) as f:
    readme = f.read()


setup(
    name="parlay",
    version=get_version(),
    description="A framework for developing and testing software for embedded devices",
    long_description=readme,
    author="Promenade Software, Inc.",
    author_email="info@promenadesoftware.com",
    url="https://github.com/PromenadeSoftware/Parlay",
    license="GPLv3",
    packages=find_packages(),
    package_data={"parlay": package_data_files},
    install_requires=["Twisted >=15.0.0",
                      "autobahn >=0.9.0",
                      "pyserial < 3.0.0"],
    extras_require={
        "secure": ["cryptography>=1.2.1",
                   "pyOpenSSL>=0.15.1",
                   "cffi>=1.5.0",
                   "service-identity >=14.0.0",
                   "requests",
                   "ipaddress>=1.0.16"]
    },
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: Embedded Systems',
        'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
        'Programming Language :: Python :: 2.7',
    ],
    keywords='embedded device broker medical',
    zip_safe=False,
    test_suite='parlay/tests',
    entry_points={
        'console_scripts': [
              'parlay = parlay.__main__:main',
              'findparlay = parlay.server.advertiser:main'
          ]
    }
)
