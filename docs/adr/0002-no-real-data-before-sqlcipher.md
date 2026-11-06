# No real bank data until SQLCipher is in

Early milestones run on synthetic data with FileVault and file permissions as the only protection at rest. SQLCipher, with its key in the macOS Keychain, must be in place before the first real Statement Import. Database access goes through one connection factory so that switching is a small change. We defer SQLCipher only to avoid its native-build friction while the Ledger design is still moving; real data on an unencrypted database is not an acceptable trade.
