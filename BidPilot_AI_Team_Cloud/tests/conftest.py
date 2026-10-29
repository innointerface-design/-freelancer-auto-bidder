import os
import tempfile
from cryptography.fernet import Fernet

# Isolated test-only credentials/database; never load or call a real account.
os.environ['APP_ENV'] = 'test'
os.environ['DATABASE_URL'] = 'sqlite:///' + tempfile.mkdtemp(prefix='bidpilot-tests-') + '/test.db'
os.environ['SESSION_SECRET'] = 'test-only-session-secret-' * 3
os.environ['FERNET_KEY'] = Fernet.generate_key().decode()
os.environ['ADMIN_EMAIL'] = 'owner@example.test'
os.environ['ADMIN_PASSWORD'] = 'Test-only-owner-password-123!'
os.environ['SECURE_COOKIES'] = 'false'
os.environ['PLATFORM_AUTOBID_APPROVED'] = 'true'
os.environ['ALLOW_PRODUCTION_CONNECTION'] = 'true'
os.environ['PUBLIC_URL'] = 'http://testserver'
for name in ('GEMINI_API_KEY', 'OPENAI_API_KEY', 'SECOND_FOUNDER_EMAIL', 'SECOND_FOUNDER_PASSWORD'):
    os.environ.pop(name, None)
