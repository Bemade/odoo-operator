from setuptools import setup, find_packages

setup(
    name="odoo-operator",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "kopf",
        "kubernetes",
        "pyyaml",
    ],
    entry_points={
        "console_scripts": [
            "odoo-operator=odoo_operator.operator:main",
        ],
    },
)
