import os

# --- SECURITY ---
# Pulls from Railway when live, defaults to local string when on your laptop
SECRET_KEY = os.environ.get("SECRET_KEY", "secret123")

# --- MYSQL CONFIGURATION ---
# Railway automatically generates these exact variable names (MYSQLHOST, MYSQLUSER, etc.)
MYSQL_HOST = os.environ.get("MYSQLHOST", "localhost")
MYSQL_USER = os.environ.get("MYSQLUSER", "root")
MYSQL_PASSWORD = os.environ.get("MYSQLPASSWORD", "Het123!@#")
MYSQL_DB = os.environ.get("MYSQLDATABASE", "bus_booking_system")
MYSQL_PORT = int(os.environ.get("MYSQLPORT", 3306))

# --- EMAIL CONFIGURATION ---
EMAIL_HOST = "smtp.gmail.com"
EMAIL_PORT = 587

# Pulls from Railway, defaults to your local email for testing
EMAIL_USERNAME = os.environ.get("EMAIL_USERNAME", "solankihet272gmail@gmail.com")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "YOUR_NEW_APP_PASSWORD_HERE")
EMAIL_FROM     = os.environ.get("EMAIL_FROM", "RedBus Royal <solankihet272gmail@gmail.com>")