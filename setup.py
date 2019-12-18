from setuptools import setup
from champ.constants import VERSION
from distutils.extension import Extension
import numpy as np


if __name__ == '__main__':
    setup(
        name='champ',
        packages=['champ', 'champ.controller'],
        ext_modules=[Extension("champ.adapters_cython", ["champ/adapters_cython.c"], include_dirs=[np.get_include()])],
        version=VERSION,
        entry_points={
          'console_scripts': [
              'champ = champ.main:main'
          ]
        },
        include_package_data=True,
        zip_safe=False,
        data_files=[('notebooks', ['notebooks/all-lda-kd-fitting.ipynb',
                                   'notebooks/data-analysis.ipynb',
                                   'notebooks/genomic-kd-fitting.ipynb',
                                   'notebooks/lda-intensity-estimation.ipynb',
                                   'notebooks/thermodynamics.ipynb'])],
        description='Processes CHAMP image data',
        url='http://www.finkelsteinlab.org',
        keywords=['DNA', 'protein', 'illumina', 'bioinformatics', 'crispr'],
        classifiers=['Development Status :: 3 - Alpha',
                     'Natural Language :: English',
                     'Intended Audience :: Science/Research',
                     'License :: Freely Distributable',
                     'Operating System :: POSIX :: Linux',
                     'Programming Language :: Python :: 3.7',
                     'Topic :: Scientific/Engineering :: Bio-Informatics',
                     ]
    )
