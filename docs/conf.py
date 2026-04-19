import os
import sys

# Make server/ and server/agents/ importable so autodoc can introspect them
sys.path.insert(0, os.path.abspath('../server'))
sys.path.insert(0, os.path.abspath('../server/agents'))
sys.path.insert(0, os.path.abspath('../server/api'))

# -- Project information -----------------------------------------------------
project = 'Flight Radar Simulator'
copyright = '2026, Flight Radar Team'
author = 'Flight Radar Team'
release = '1.0.0'

# -- General configuration ---------------------------------------------------
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.viewcode',
    'sphinx_autodoc_typehints',
]

# autodoc settings
autodoc_default_options = {
    'members': True,
    'undoc-members': True,
    'show-inheritance': True,
    'private-members': False,
}

# Napoleon settings (Google / NumPy docstring support)
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = True

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

# -- Options for HTML output -------------------------------------------------
html_theme = 'alabaster'
html_static_path = ['_static']
