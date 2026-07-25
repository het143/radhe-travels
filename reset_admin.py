import MySQLdb
from werkzeug.security import generate_password_hash
import config  # Imports your existing database credentials


def reset_admin_password():
    print("=== System Root Password Reset ===")
    email = input("Enter the Admin Email address: ")
    new_password = input("Enter the NEW Master Key (Password): ")

    # Securely hash the new password
    hashed_password = generate_password_hash(new_password)

    # Initialize variables as None before the try block
    db = None
    cur = None

    try:
        # Connect to the database using your existing config
        db = MySQLdb.connect(
            host=config.MYSQL_HOST,
            user=config.MYSQL_USER,
            passwd=config.MYSQL_PASSWORD,
            db=config.MYSQL_DB
        )
        cur = db.cursor()

        # Check if admin exists
        cur.execute("SELECT admin_id FROM admin WHERE email=%s", (email,))
        if not cur.fetchone():
            print("\n❌ Error: No admin found with that email address.")
            return

        # Update the password
        cur.execute("UPDATE admin SET password=%s WHERE email=%s", (hashed_password, email))
        db.commit()

        print("\n✅ Success! The Admin Master Key has been securely updated.")
        print("You can now log in to the web portal with your new password.")

    except Exception as e:
        print(f"\n❌ Database Error: {e}")
    finally:
        # Safely close the connections only if they were successfully created
        if cur is not None:
            cur.close()
        if db is not None:
            db.close()


if __name__ == "__main__":
    reset_admin_password()