"""Explicitly authorized per-user Calendar read-only smoke check; never prints busy times.

Usage: python scripts/check_calendar.py
Calendar calls are free/busy only. Normal secure token refresh/status maintenance
may occur locally. Do not run without approval for the specified test accounts.
"""
from datetime import datetime,timedelta,timezone
import logging
from contextlib import closing
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from services.google_oauth import GoogleOAuthService, GoogleProvider
from services.token_cipher import WindowsTokenCipher


class AccountBoundCipher:
    """Check runtime ownership without inspecting decrypted credential values."""
    def __init__(self):
        self.owner=None
        self.decrypt_verified=False
        self.delegate=WindowsTokenCipher()

    def decrypt(self,user_id,value):
        if user_id!=self.owner: raise RuntimeError('Credential owner mismatch')
        result=self.delegate.decrypt(user_id,value)
        self.decrypt_verified=True
        return result

    def encrypt(self,user_id,value):
        if user_id!=self.owner: raise RuntimeError('Credential owner mismatch')
        return self.delegate.encrypt(user_id,value)


def discover_test_accounts():
    """Only the specifically approved identifier lookup; never return to output."""
    path=Path(__file__).resolve().parents[1]/'database'/'study_agent.db'
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as database:
        rows=database.execute(
            """SELECT c.telegram_user_id FROM google_connections c
               JOIN users u ON u.telegram_user_id=c.telegram_user_id
               WHERE c.status IN ('connected','expired')
                 AND c.encrypted_credentials IS NOT NULL
               ORDER BY c.telegram_user_id LIMIT 3"""
        ).fetchall()
    if len(rows)!=2: raise RuntimeError('Exactly two eligible connected test accounts are required')
    return [row[0] for row in rows]


def main():
    if len(sys.argv)!=1: print('No account identifiers or credentials should be supplied.');return 2
    try:
        identities=discover_test_accounts()
    except Exception as error:
        print('Account discovery stopped ('+type(error).__name__+'); no identifiers displayed.');return 2
    logging.disable(logging.CRITICAL)
    cipher=AccountBoundCipher()
    service=GoogleOAuthService(provider=GoogleProvider(),cipher=cipher)
    start=datetime.now(timezone.utc)
    success=True
    for index,identity in enumerate(identities,1):
        label='A' if index==1 else 'B'
        try:
            cipher.owner=identity
            cipher.decrypt_verified=False
            value=service.availability(identity,start,start+timedelta(hours=1))
            if not cipher.decrypt_verified: raise RuntimeError('Credential ownership not verified')
            if any(set(period)!={'start','end'} for period in value['busy']): raise ValueError()
            print(f'Account {label}: free/busy retrieval passed using its user-bound encrypted connection; no event details displayed.')
        except Exception as error:
            print(f'Account {label}: check failed ({type(error).__name__}); no fallback used.')
            success=False
    return 0 if success else 1


if __name__=='__main__':raise SystemExit(main())
