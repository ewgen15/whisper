import os

# Modules read these at import time; real calls are always mocked in tests.
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_KEY", "test-key")
