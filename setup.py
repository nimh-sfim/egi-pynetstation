"""Setuptools fallback packaging metadata.

pyproject.toml declares poetry-core as the build backend, so pip does not
use this file when building or installing the project. It is kept only as a
fallback for tooling that expects a setup.py, and every field here must
mirror pyproject.toml. See tests/test_version.py, which enforces that.
"""

import setuptools

with open('README.md', 'r', encoding='utf-8') as fh:
    long_description = fh.read()

setuptools.setup(
    name='egi_pynetstation',
    version='2.0.0',
    author='Joshua B. Teves',
    author_email='jbtevespro@gmail.com',
    description='Magstim-EGI EEG amplifier NetStation API',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/nimh-sfim/egi-pynetstation',
    license='MIT',
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
    ],
    packages=setuptools.find_packages(),
    install_requires=[
        'ntplib>=0.4.0',
    ],
    extras_require={
        'dev': [
            'sphinx',
            'sphinx_rtd_theme',
        ]
    },
    python_requires='>=3.9',
)
