# Installation guide (current, synthetic)

## Prerequisites
- Python 3.10 or newer
- pip >= 23

## Steps
1. Clone the synthetic repository:
   `git clone https://example.local/synthetic/app.git`
2. Create a virtualenv:
   `python -m venv .venv`
3. Activate it (Windows PowerShell):
   `.venv\Scripts\Activate.ps1`
4. Install the package in editable mode:
   `pip install -e .`
5. Verify:
   `app --version`

You should see `app 2.4.0` printed.
