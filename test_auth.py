from sheet_manager import SheetManager
import time

print("Testing Auth Logic...")

try:
    sm = SheetManager()
    if not sm.client:
        print("Connection failed, cannot test.")
        exit(1)

    test_user = f"TestUser_{int(time.time())}"
    test_pass = "password123"

    print(f"1. Registering {test_user}...")
    if sm.register_user(test_user, test_pass):
        print("   -> Success")
    else:
        print("   -> Failed (Might already exist)")

    print("2. Login with correct password...")
    if sm.login_user(test_user, test_pass):
        print("   -> Success")
    else:
        print("   -> Failed")

    print("3. Login with WRONG password...")
    if not sm.login_user(test_user, "wrongpass"):
        print("   -> Success (Access Denied)")
    else:
        print("   -> Failed (Access Granted unexpectedly)")

    print("4. Get All Users...")
    users = sm.get_all_users()
    if test_user in users:
        print(f"   -> Success (Found {test_user} in {len(users)} users)")
    else:
        print(f"   -> Failed (User not found)")

except Exception as e:
    print(f"Error: {e}")
