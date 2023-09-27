import setuptools

with open('README.md', 'r', encoding='utf-8') as fh:
    long_description = fh.read()

setuptools.setup(
    name='eci',
    version='1.0.0',
    author='Joshua B. Teves',
    author_email='joshua.teves@nih.gov',
    description='A library for using EGI EEG network interface',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/nimh-sfim/PsychoPy3_EGI_NTP',
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
    ],
    packages=setuptools.find_packages(),
    extras_require={
        'dev': [
            'sphinx',
            'sphinx_rtd_theme',
        ]
    },
    python_requires='>=3.7',
)
