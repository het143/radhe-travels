from flask import Flask, render_template, request, redirect, url_for, flash, session
from database.db_connection import mysql
import config
from flask_socketio import SocketIO, emit
import os
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

from authlib.integrations.flask_client import OAuth
import secrets
import string

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

# ---------------------------------------------------------
# SOCKETIO CONFIGURATION & LIVE TRACKING MEMORY
# ---------------------------------------------------------
socketio = SocketIO(app, cors_allowed_origins="*")

active_clients = {}


@socketio.on("connect")
def handle_connect():
    pass


@socketio.on("disconnect")
def handle_disconnect():
    user_sid = request.sid
    if user_sid in active_clients:
        del active_clients[user_sid]
        emit("user_disconnected", user_sid, broadcast=True)


@socketio.on("send_location")
def handle_location(data):
    user_sid = request.sid
    active_clients[user_sid] = {
        "id": user_sid,
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "user_info": data.get("user_info", "Unknown User"),
        "type": data.get("type", "passenger")
    }
    emit("receive_location", active_clients[user_sid], broadcast=True)


# ---------------------------------------------------------
# MYSQL CONFIGURATION
# ---------------------------------------------------------
app.config['MYSQL_HOST'] = config.MYSQL_HOST
app.config['MYSQL_USER'] = config.MYSQL_USER
app.config['MYSQL_PASSWORD'] = config.MYSQL_PASSWORD
app.config['MYSQL_DB'] = config.MYSQL_DB
mysql.init_app(app)

# ---------------------------------------------------------
# GOOGLE OAUTH CONFIGURATION
# ---------------------------------------------------------
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id='YOUR_GOOGLE_CLIENT_ID_HERE',
    client_secret='YOUR_GOOGLE_CLIENT_SECRET_HERE',
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)


# =========================================================
# AUTHENTICATION SYSTEM & ROUTING
# =========================================================
@app.route("/")
def root_redirect():
    return redirect(url_for("passenger_home"))


@app.route("/login_page")
def login_page():
    if "user" in session: return redirect(url_for("passenger_home"))
    if "operator" in session: return redirect(url_for("operator_dashboard"))
    if "admin" in session: return redirect(url_for("admin_dashboard"))
    return render_template("login.html")


@app.route("/login", methods=["GET"])
def login(): return redirect(url_for("login_page"))


@app.route("/admin_login", methods=["GET"])
def admin_login(): return redirect(url_for("login_page"))


@app.route("/operator_login", methods=["GET"])
def operator_login(): return redirect(url_for("login_page"))


@app.route("/login", methods=["POST"])
def login_post():
    email = request.form.get("email")
    password = request.form.get("password")
    login_type = request.form.get("login_type")
    cur = mysql.connection.cursor()

    if login_type == "admin":
        cur.execute("SELECT admin_id, password FROM admin WHERE email=%s", (email,))
        admin = cur.fetchone()
        if admin and check_password_hash(admin[1], password):
            session["admin"] = admin[0]
            cur.close()
            return redirect(url_for("admin_dashboard"))
        else:
            flash("Invalid Admin credentials.", "danger")

    elif login_type == "operator":
        cur.execute("SELECT operator_id, account_status, password FROM operator WHERE email=%s", (email,))
        operator = cur.fetchone()
        if operator and check_password_hash(operator[2], password):
            if operator[1] == 1:
                session["operator"] = operator[0]
                cur.close()
                return redirect(url_for("operator_dashboard"))
            elif operator[1] == 2:
                flash("Your account application was rejected by the Admin.", "danger")
            else:
                flash("Your account is still pending Admin approval.", "warning")
        else:
            flash("Invalid Operator credentials.", "danger")

    elif login_type == "passenger":
        cur.execute("SELECT user_id, full_name, password FROM user WHERE email=%s", (email,))
        user = cur.fetchone()
        if user and check_password_hash(user[2], password):
            session["user"] = user[0]
            session["user_name"] = user[1]
            cur.close()
            return redirect(url_for("passenger_home"))
        else:
            flash("Invalid Passenger credentials.", "danger")
    else:
        flash("Invalid login attempt.", "danger")

    cur.close()
    return redirect(url_for("login_page"))


@app.route("/register", methods=["POST"])
def register_post():
    full_name = request.form.get("full_name")
    email = request.form.get("email")
    mobile_no = request.form.get("mobile_no")
    password = request.form.get("password")
    confirm_password = request.form.get("confirm_password")

    if password != confirm_password:
        flash("Registration Failed: Passwords do not match.", "danger")
        return redirect(url_for("login_page"))

    hashed_password = generate_password_hash(password)
    try:
        cur = mysql.connection.cursor()
        cur.execute("INSERT INTO user(full_name, email, mobile_no, password) VALUES (%s, %s, %s, %s)",
                    (full_name, email, mobile_no, hashed_password))
        mysql.connection.commit()
        cur.close()
        flash("Passenger Registration Successful! Please login.", "success")
    except Exception as e:
        flash(f"Database Error: {str(e)}", "danger")
    return redirect(url_for("login_page"))


@app.route("/forgot_password", methods=["POST"])
def forgot_password():
    email = request.form.get("email")
    mobile_no = request.form.get("mobile_no")
    new_password = request.form.get("new_password")
    confirm_password = request.form.get("confirm_password")
    user_type = request.form.get("user_type")

    if new_password != confirm_password:
        flash("Recovery Failed: Passwords do not match.", "danger")
        return redirect(url_for("login_page"))

    hashed_password = generate_password_hash(new_password)
    cur = mysql.connection.cursor()
    try:
        if user_type == "passenger":
            cur.execute("SELECT * FROM user WHERE email=%s AND mobile_no=%s", (email, mobile_no))
            if cur.fetchone():
                cur.execute("UPDATE user SET password=%s WHERE email=%s", (hashed_password, email))
                mysql.connection.commit()
                flash("Password reset successfully! Please login.", "success")
            else:
                flash("Identity verification failed. Invalid Email or Mobile Number.", "danger")
        elif user_type == "operator":
            cur.execute("SELECT * FROM operator WHERE email=%s AND mobile_no=%s", (email, mobile_no))
            if cur.fetchone():
                cur.execute("UPDATE operator SET password=%s WHERE email=%s", (hashed_password, email))
                mysql.connection.commit()
                flash("Access Token reset successfully! Please login.", "success")
            else:
                flash("Identity verification failed. Invalid Email or Mobile Number.", "danger")
    except Exception as e:
        flash(f"Database Error: {str(e)}", "danger")
    finally:
        cur.close()

    return redirect(url_for("login_page"))


@app.route("/operator_register", methods=["POST"])
def operator_register():
    operator_name = request.form.get("operator_name")
    mobile_no = request.form.get("mobile_no")
    email = request.form.get("email")
    password = request.form.get("password")
    confirm_password = request.form.get("confirm_password")
    terms_accepted = request.form.get("terms")

    if not terms_accepted:
        flash("Application Failed: You must accept the Operator Terms & Conditions.", "danger")
        return redirect(url_for("login_page"))

    if password != confirm_password:
        flash("Application Failed: Passwords do not match.", "danger")
        return redirect(url_for("login_page"))

    hashed_password = generate_password_hash(password)
    license_doc = request.files.get("license_document")
    bus_doc = request.files.get("bus_registration_document")

    try:
        cur = mysql.connection.cursor()
        license_filename = None
        if license_doc and license_doc.filename:
            license_filename = secure_filename(f"lic_{mobile_no}_{license_doc.filename}")
            os.makedirs(os.path.join(app.root_path, 'static/uploads/documents'), exist_ok=True)
            license_doc.save(os.path.join(app.root_path, 'static/uploads/documents', license_filename))

        bus_filename = None
        if bus_doc and bus_doc.filename:
            bus_filename = secure_filename(f"bus_{mobile_no}_{bus_doc.filename}")
            os.makedirs(os.path.join(app.root_path, 'static/uploads/documents'), exist_ok=True)
            bus_doc.save(os.path.join(app.root_path, 'static/uploads/documents', bus_filename))

        cur.execute("""
            INSERT INTO operator (operator_name, mobile_no, email, password, account_status, license_document, bus_registration_document) 
            VALUES (%s, %s, %s, %s, 0, %s, %s)
        """, (operator_name, mobile_no, email, hashed_password, license_filename, bus_filename))

        mysql.connection.commit()
        cur.close()
        flash("Application and documents submitted! Awaiting Admin approval.", "success")
    except Exception as e:
        flash(f"Error: {str(e)}", "danger")

    return redirect(url_for("login_page"))


@app.route('/google_login')
def google_login():
    redirect_uri = url_for('google_callback', _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route('/google_callback')
def google_callback():
    try:
        token = google.authorize_access_token()
        user_info = token.get('userinfo')
        email = user_info.get('email')
        name = user_info.get('name')
        picture = user_info.get('picture')

        cur = mysql.connection.cursor()
        cur.execute("SELECT user_id FROM user WHERE email = %s", (email,))
        existing_user = cur.fetchone()

        if existing_user:
            session['user'] = existing_user[0]
            session['user_name'] = name
            try:
                cur.execute("UPDATE user SET profile_image = %s WHERE email = %s", (picture, email))
                mysql.connection.commit()
            except Exception as e:
                print(f"Non-critical image update error: {e}")
            flash(f"Welcome back, {name}!", "success")
        else:
            alphabet = string.ascii_letters + string.digits
            dummy_password = ''.join(secrets.choice(alphabet) for i in range(16))
            hashed_password = generate_password_hash(dummy_password)
            cur.execute("""
                INSERT INTO user (full_name, email, mobile_no, password, profile_image) 
                VALUES (%s, %s, %s, %s, %s)
            """, (name, email, "Google Auth", hashed_password, picture))

            new_user_id = cur.lastrowid
            session['user'] = new_user_id
            session['user_name'] = name
            mysql.connection.commit()
            flash("Google Registration successful! Welcome to Radhe Travels.", "success")

        cur.close()
        return redirect(url_for('passenger_home'))
    except Exception as e:
        print(f"Google Auth Error: {e}")
        flash("Google Login failed. Please try again or use email/password.", "danger")
        return redirect(url_for('login_page'))


@app.route("/operator_terms")
def operator_terms():
    return render_template("operator_terms.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully", "success")
    return redirect(url_for("login_page"))


@app.route("/admin/network_map")
def admin_network_map():
    if "admin" not in session:
        flash("Unauthorized access.", "danger")
        return redirect(url_for("login_page"))

    live_passengers = []
    live_buses = []
    for sid, data in active_clients.items():
        if data.get("type") == "passenger":
            live_passengers.append({
                "id": sid[:6],
                "name": data.get("user_info"),
                "lat": data.get("latitude"),
                "lng": data.get("longitude")
            })
        elif data.get("type") == "bus":
            live_buses.append({
                "id": sid[:6],
                "operator": data.get("user_info"),
                "route": "Live Route",
                "lat": data.get("latitude"),
                "lng": data.get("longitude"),
                "status": "Moving"
            })
    return render_template("admin/map.html", buses=live_buses, passengers=live_passengers)


@app.errorhandler(404)
def page_not_found(_):
    return render_template('404.html'), 404


@app.errorhandler(500)
def internal_server_error(e):
    print(f"CRITICAL SERVER ERROR: {e}")
    return render_template('404.html'), 500


from controllers import admin_controller
from controllers import operator_controller
from controllers import passenger_controller

# NEW: Server Execution setup via SocketIO
if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000)