# noinspection SqlNoDataSourceInspection,SqlDialectInspection,DuplicatedCode
from datetime import datetime, date, timedelta
import MySQLdb
import os
from werkzeug.utils import secure_filename
from flask import render_template, request, redirect, url_for, flash, session
from app import app
from database.db_connection import mysql


@app.context_processor
def inject_operator_notifications():
    if "operator" in session:
        try:
            operator_id = session["operator"]
            cur = mysql.connection.cursor()
            counts = {}

            cur.execute(
                "SELECT COUNT(*) FROM operator WHERE operator_id = %s AND admin_remarks IS NOT NULL AND admin_remarks != ''",
                (operator_id,))
            counts['admin_messages'] = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM route WHERE operator_id = %s AND status = 2", (operator_id,))
            counts['rejected_routes'] = cur.fetchone()[0]

            cur.execute(
                "SELECT COUNT(*) FROM schedule s JOIN bus b ON s.bus_id = b.bus_id WHERE b.operator_id = %s AND s.status = 2",
                (operator_id,))
            counts['rejected_schedules'] = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM bus WHERE operator_id = %s AND layout_status = 3", (operator_id,))
            counts['rejected_layouts'] = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM offer WHERE operator_id = %s AND status = 2", (operator_id,))
            counts['rejected_offers'] = cur.fetchone()[0]

            cur.close()
            return dict(op_counts=counts)
        except MySQLdb.Error as e:
            print(f"Operator Notification Engine Error: {e}")
            return dict(op_counts={})
        except Exception as e:
            print(f"Unexpected Operator Notification Engine Error: {e}")
            return dict(op_counts={})
    return dict(op_counts={})


def get_operator_name():
    if "operator" in session:
        if "operator_name" not in session or "operator_profile_image" not in session:
            try:
                cur = mysql.connection.cursor()
                cur.execute("SELECT operator_name, profile_image FROM operator WHERE operator_id = %s",
                            (session["operator"],))
                data = cur.fetchone()
                cur.close()
                if data:
                    session["operator_name"] = data[0]
                    session["operator_profile_image"] = data[1]
            except MySQLdb.Error as e:
                print(f"Error fetching operator name: {e}")

        return session.get("operator_name", "Operator")
    return "Operator"


@app.route("/operator_dashboard")
def operator_dashboard():
    if "operator" not in session: return redirect(url_for("login_page"))
    operator_id = session["operator"]

    try:
        cur = mysql.connection.cursor()

        cur.execute("SELECT COUNT(*) FROM bus WHERE operator_id = %s", (operator_id,))
        total_buses = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(DISTINCT s.route_id) FROM schedule s JOIN bus b ON s.bus_id = b.bus_id WHERE b.operator_id = %s",
            (operator_id,))
        active_routes = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(bk.booking_id), COALESCE(SUM(bk.total_amount), 0)
            FROM booking bk JOIN schedule s ON bk.schedule_id = s.schedule_id JOIN bus b ON s.bus_id = b.bus_id
            WHERE b.operator_id = %s AND DATE(bk.booking_date) = CURDATE() AND bk.booking_status = 1
        """, (operator_id,))
        today_data = cur.fetchone()

        stats = {
            "total_buses": total_buses, "todays_bookings": today_data[0],
            "active_routes": active_routes, "today_earnings": f"{float(today_data[1]):,.2f}"
        }

        today_date = date.today()
        last_7_dates = [today_date - timedelta(days=i) for i in range(6, -1, -1)]
        revenue_labels = [d.strftime("%b %d") for d in last_7_dates]
        revenue_values = [0.0] * 7

        cur.execute("""
            SELECT DATE(bk.booking_date), SUM(bk.total_amount) 
            FROM booking bk JOIN schedule s ON bk.schedule_id = s.schedule_id JOIN bus b ON s.bus_id = b.bus_id
            WHERE b.operator_id = %s AND bk.booking_status = 1 AND bk.booking_date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
            GROUP BY DATE(bk.booking_date)
        """, (operator_id,))
        rev_dict = {row[0]: float(row[1]) for row in cur.fetchall()}
        for i, d in enumerate(last_7_dates):
            if d in rev_dict: revenue_values[i] = rev_dict[d]

        cur.execute("""
            SELECT CONCAT(r.source_city, ' ⇄ ', r.destination_city), COUNT(bk.booking_id)
            FROM booking bk JOIN schedule s ON bk.schedule_id = s.schedule_id JOIN route r ON s.route_id = r.route_id JOIN bus b ON s.bus_id = b.bus_id
            WHERE b.operator_id = %s AND bk.booking_status = 1
            GROUP BY r.route_id, r.source_city, r.destination_city ORDER BY COUNT(bk.booking_id) DESC LIMIT 3
        """, (operator_id,))
        route_data = cur.fetchall()
        route_share = {row[0]: row[1] for row in route_data} if route_data else {"No Active Bookings": 1}

        cur.execute("SELECT bus_number FROM bus WHERE operator_id = %s AND layout_status = 2", (operator_id,))
        active_buses = [b[0] for b in cur.fetchall()]

        cur.execute("""
            SELECT st.ticket_id, st.name, st.pnr, st.message, u.mobile_no 
            FROM support_ticket st
            JOIN user u ON st.user_id = u.user_id
            JOIN booking bk ON st.pnr = CONCAT('RBR-', LPAD(bk.booking_id, 6, '0'))
            JOIN schedule s ON bk.schedule_id = s.schedule_id
            JOIN bus bs ON s.bus_id = bs.bus_id
            WHERE bs.operator_id = %s AND st.message LIKE '%%[EMERGENCY SOS%%' AND st.status = 0
        """, (operator_id,))
        passenger_sos = [{"ticket_id": r[0], "passenger": r[1], "pnr": r[2], "message": r[3], "mobile": r[4]} for r in
                         cur.fetchall()]

        cur.close()

    except MySQLdb.Error as e:
        print(f"Dashboard Load Error: {e}")
        stats = {"total_buses": 0, "todays_bookings": 0, "active_routes": 0, "today_earnings": "0.00"}
        revenue_labels, revenue_values, route_share, active_buses, passenger_sos = [], [], {"No Data": 1}, [], []
    except Exception as e:
        print(f"Unexpected Dashboard Error: {e}")
        stats = {"total_buses": 0, "todays_bookings": 0, "active_routes": 0, "today_earnings": "0.00"}
        revenue_labels, revenue_values, route_share, active_buses, passenger_sos = [], [], {"No Data": 1}, [], []

    return render_template("operator/dashboard.html", operator_name=get_operator_name(), stats=stats,
                           revenue_labels=revenue_labels, revenue_values=revenue_values, route_share=route_share,
                           active_buses=active_buses, passenger_sos=passenger_sos, now=str(datetime.now().time()))


@app.route("/operator_resolve_sos/<int:ticket_id>", methods=["POST"])
def operator_resolve_sos(ticket_id):
    if "operator" not in session: return redirect(url_for("login_page"))

    try:
        cur = mysql.connection.cursor()
        cur.execute("""
            SELECT st.ticket_id FROM support_ticket st
            JOIN booking bk ON st.pnr = CONCAT('RBR-', LPAD(bk.booking_id, 6, '0'))
            JOIN schedule s ON bk.schedule_id = s.schedule_id
            JOIN bus bs ON s.bus_id = bs.bus_id
            WHERE st.ticket_id = %s AND bs.operator_id = %s
        """, (ticket_id, session["operator"]))

        if cur.fetchone():
            cur.execute(
                "UPDATE support_ticket SET status = 1, admin_reply = 'Emergency SOS resolved by the Operator on the ground.' WHERE ticket_id = %s",
                (ticket_id,))
            mysql.connection.commit()
            flash("Passenger SOS marked as resolved. The situation has been cleared.", "success")
        else:
            flash("Security Alert: Unauthorized attempt to clear an SOS ticket.", "danger")
        cur.close()
    except MySQLdb.Error as e:
        print(f"Resolve SOS Error: {e}")
        flash("Database Error resolving SOS.", "danger")

    return redirect(url_for("operator_dashboard"))


@app.route("/operator_send_alert", methods=["POST"])
def operator_send_alert():
    if "operator" not in session: return redirect(url_for("login_page"))
    operator_id = session["operator"]
    alert_type = request.form.get("alert_type")
    bus_reg = request.form.get("bus_reg", "Unknown Bus")
    message = request.form.get("message", "")

    full_alert = f"[EMERGENCY] {alert_type} - Bus {bus_reg}: {message}"

    try:
        cur = mysql.connection.cursor()
        cur.execute("UPDATE operator SET operator_message = %s WHERE operator_id = %s", (full_alert, operator_id))
        mysql.connection.commit()
        cur.close()

        # --- NEW WEBSOCKET BROADCAST ---
        from app import socketio
        socketio.emit('emergency_trigger', {
            'title': '⚠️ Operator Alert',
            'message': f'Bus {bus_reg} reported: {alert_type}.'
        })

        flash("Emergency alert broadcasted to Admin Console instantly.", "success")
    except MySQLdb.Error as e:
        print(f"Emergency Alert Error: {e}")
        flash("Error sending alert.", "danger")

    return redirect(url_for("operator_dashboard"))

@app.route("/operator_view_profile")
def operator_view_profile():
    if "operator" not in session: return redirect(url_for("login_page"))
    operator_id = session["operator"]

    try:
        cur = mysql.connection.cursor()
        cur.execute(
            "SELECT operator_name, email, mobile_no, profile_image, license_document, bus_registration_document, gender, age, upi_id, bank_name, account_number, ifsc_code FROM operator WHERE operator_id = %s",
            (operator_id,))
        op_data = cur.fetchone()
        cur.close()

        if op_data:
            return render_template("operator/view_profile.html",
                                   operator_name=get_operator_name(),
                                   operator_email=op_data[1],
                                   operator_mobile=op_data[2],
                                   operator_profile_image=op_data[3],
                                   license_doc=op_data[4],
                                   bus_doc=op_data[5],
                                   operator_gender=op_data[6],
                                   operator_age=op_data[7],
                                   upi_id=op_data[8],
                                   bank_name=op_data[9],
                                   account_number=op_data[10],
                                   ifsc_code=op_data[11])
    except MySQLdb.Error as e:
        print(f"View Profile Error: {e}")

    session.clear()
    return redirect(url_for("login_page"))


@app.route("/operator_update_profile", methods=["POST"])
def operator_update_profile():
    if "operator" not in session: return redirect(url_for("login_page"))
    operator_id = session["operator"]

    operator_name = request.form.get("operator_name")
    mobile_no = request.form.get("mobile_no")
    gender = request.form.get("gender")
    age = request.form.get("age")

    upi_id = request.form.get("upi_id")
    bank_name = request.form.get("bank_name")
    account_number = request.form.get("account_number")
    ifsc_code = request.form.get("ifsc_code")

    if not age: age = None

    profile_img = request.files.get("profile_image")
    license_doc = request.files.get("license_document")
    bus_doc = request.files.get("bus_registration_document")

    try:
        cur = mysql.connection.cursor()

        update_fields = [
            "operator_name = %s", "mobile_no = %s", "gender = %s", "age = %s",
            "upi_id = %s", "bank_name = %s", "account_number = %s", "ifsc_code = %s"
        ]
        update_values = [operator_name, mobile_no, gender, age, upi_id, bank_name, account_number, ifsc_code]

        if profile_img and profile_img.filename != '':
            filename = secure_filename(profile_img.filename)
            unique_filename = f"op_{operator_id}_{filename}"
            upload_folder = os.path.join(app.root_path, 'static', 'uploads', 'operators')
            os.makedirs(upload_folder, exist_ok=True)
            profile_img.save(os.path.join(upload_folder, unique_filename))
            update_fields.append("profile_image = %s")
            update_values.append(unique_filename)
            session["operator_profile_image"] = unique_filename

        doc_upload_folder = os.path.join(app.root_path, 'static', 'uploads', 'documents')
        os.makedirs(doc_upload_folder, exist_ok=True)

        if license_doc and license_doc.filename != '':
            l_filename = secure_filename(f"lic_{mobile_no}_{license_doc.filename}")
            license_doc.save(os.path.join(doc_upload_folder, l_filename))
            update_fields.append("license_document = %s")
            update_values.append(l_filename)

        if bus_doc and bus_doc.filename != '':
            b_filename = secure_filename(f"bus_{mobile_no}_{bus_doc.filename}")
            bus_doc.save(os.path.join(doc_upload_folder, b_filename))
            update_fields.append("bus_registration_document = %s")
            update_values.append(b_filename)

        update_values.append(operator_id)
        cur.execute(f"UPDATE operator SET {', '.join(update_fields)} WHERE operator_id = %s", tuple(update_values))
        mysql.connection.commit()
        cur.close()

        session["operator_name"] = operator_name
        flash("Business Profile and financial details updated successfully!", "success")
    except MySQLdb.Error as e:
        print(f"Operator Profile Update Error: {e}")
        flash("Database error updating profile.", "danger")
    except Exception as e:
        print(f"Unexpected Profile Update Error: {e}")
        flash("Error updating profile.", "danger")

    return redirect(url_for("operator_view_profile"))


# =========================================================================
# ALL-IN-ONE OPERATIONS HUB (ROUTES, FLEET, SCHEDULES)
# =========================================================================
@app.route("/operator_operations_hub")
def operator_operations_hub():
    if "operator" not in session: return redirect(url_for("login_page"))
    operator_id = session["operator"]

    cur = mysql.connection.cursor()
    try:
        cur.execute("SELECT account_status, profile_image FROM operator WHERE operator_id = %s", (operator_id,))
        op_data = cur.fetchone()
    except MySQLdb.Error as e:
        print(f"Status check error: {e}")
        op_data = (1, "")

    if not op_data or op_data[0] != 1:
        flash("Your account is not fully approved. You cannot manage operations yet.", "warning")
        return redirect(url_for("operator_dashboard"))

    search_query = request.args.get("search", "").strip()
    gujarat_cities = [
        "Ahmedabad", "Amreli", "Anand", "Bharuch", "Bhavnagar", "Bhuj", "Dwarka", "Gandhidham",
        "Gandhinagar", "Godhra", "Gondal", "Jamnagar", "Junagadh", "Mehsana", "Morbi", "Nadiad",
        "Navsari", "Palanpur", "Patan", "Porbandar", "Rajkot", "Somnath", "Surat", "Surendranagar",
        "Vadodara", "Valsad", "Vapi", "Viramgam"
    ]

    cur.execute(
        "SELECT s.bus_id, s.seat_type, MAX(s.seat_price) FROM seat s JOIN bus b ON s.bus_id = b.bus_id WHERE s.seat_price > 0 AND b.operator_id = %s GROUP BY s.bus_id, s.seat_type",
        (operator_id,))
    seat_prices_dict = {}
    for b_id, s_type, s_price in cur.fetchall():
        if b_id not in seat_prices_dict:
            seat_prices_dict[b_id] = {}
        seat_prices_dict[b_id][s_type] = float(s_price)

    try:
        if search_query:
            like_query = f"%{search_query}%"
            cur.execute(
                "SELECT route_id, source_city, destination_city, distance_km, status, admin_feedback, operator_remarks FROM route WHERE operator_id = %s AND (source_city LIKE %s OR destination_city LIKE %s) ORDER BY route_id DESC",
                (operator_id, like_query, like_query))
        else:
            cur.execute(
                "SELECT route_id, source_city, destination_city, distance_km, status, admin_feedback, operator_remarks FROM route WHERE operator_id = %s ORDER BY route_id DESC",
                (operator_id,))
        routes = [{"id": r[0], "source": r[1], "destination": r[2], "distance": r[3], "status": r[4], "feedback": r[5],
                   "operator_remarks": r[6]} for r in cur.fetchall()]
    except MySQLdb.Error as e:
        print(f"Hub Route Fetch Error: {e}")
        routes = []

    try:
        cur.execute(
            "SELECT bus_id, bus_number, bus_type, total_seats, amenities, layout_status, admin_feedback, operator_remarks FROM bus WHERE operator_id = %s ORDER BY bus_id DESC",
            (operator_id,))
        buses = [{"id": b[0], "number": b[1], "type": b[2], "seats": b[3], "amenities": b[4], "status": b[5],
                  "feedback": b[6], "operator_remarks": b[7]} for b in cur.fetchall()]
    except MySQLdb.Error as e:
        print(f"Hub Bus Fetch Error: {e}")
        buses = []

    try:
        cur.execute(
            "SELECT route_id, source_city, destination_city, distance_km FROM route WHERE status = 1 ORDER BY source_city ASC"
        )
        approved_routes = [{"id": r[0], "source": r[1], "destination": r[2], "distance": r[3]} for r in cur.fetchall()]

        cur.execute("SELECT bus_id, bus_number FROM bus WHERE operator_id = %s AND layout_status = 2", (operator_id,))
        approved_buses = [{"id": b[0], "number": b[1]} for b in cur.fetchall()]

        if search_query:
            cur.execute("""
                SELECT s.schedule_id, b.bus_number, r.source_city, r.destination_city, s.travel_date, s.departure_time, s.arrival_time, s.ticket_price, s.status, s.schedule_pattern, s.admin_feedback, s.operator_remarks, b.bus_type, b.total_seats, r.distance_km, b.bus_id
                FROM schedule s JOIN bus b ON s.bus_id = b.bus_id JOIN route r ON s.route_id = r.route_id WHERE b.operator_id = %s AND (b.bus_number LIKE %s OR r.source_city LIKE %s OR r.destination_city LIKE %s OR s.schedule_pattern LIKE %s) ORDER BY s.travel_date DESC, s.departure_time DESC
            """, (operator_id, f"%{search_query}%", f"%{search_query}%", f"%{search_query}%", f"%{search_query}%"))
        else:
            cur.execute("""
                SELECT s.schedule_id, b.bus_number, r.source_city, r.destination_city, s.travel_date, s.departure_time, s.arrival_time, s.ticket_price, s.status, s.schedule_pattern, s.admin_feedback, s.operator_remarks, b.bus_type, b.total_seats, r.distance_km, b.bus_id
                FROM schedule s JOIN bus b ON s.bus_id = b.bus_id JOIN route r ON s.route_id = r.route_id WHERE b.operator_id = %s ORDER BY s.travel_date DESC, s.departure_time DESC
            """, (operator_id,))
        raw_schedules = cur.fetchall()

        grouped_schedules = {}
        for r in raw_schedules:
            s_id, bus_num, src, dest, t_date, d_time, a_time = r[0:7]
            price = float(r[7]) if len(r) > 7 and r[7] else 0.0
            status, pattern, feedback, op_remarks = r[8:12]

            pattern = pattern or "Single Trip"
            bus_type = r[12] if len(r) > 12 and r[12] else ""
            total_seats = r[13] if len(r) > 13 and r[13] else 0
            distance = float(r[14]) if len(r) > 14 and r[14] else 0.0
            bus_id = int(r[15]) if len(r) > 15 and r[15] else 0

            b_prices = seat_prices_dict.get(bus_id, {})
            seater_p = b_prices.get('seater', 0.0)
            sleeper_p = b_prices.get('sleeper', 0.0)
            ladies_p = b_prices.get('ladies', 0.0)

            dep_str = (datetime.min + d_time).strftime("%I:%M %p") if d_time else ""
            arr_str = (datetime.min + a_time).strftime("%I:%M %p") if a_time else ""

            is_round = "Round Trip" in pattern
            is_recur = "Recurring" in pattern

            day_part = None
            if is_recur:
                day_part = pattern.split("Recurring (")[-1].replace(")", "").strip()

            date_key = None if is_recur else t_date

            batch_key = (bus_num, src, dest, price, status, is_round, is_recur, date_key, bus_id)

            if batch_key not in grouped_schedules:
                grouped_schedules[batch_key] = {
                    "ids": [], "display_id": s_id, "bus": bus_num,
                    "source": src, "destination": dest, "price": price, "status": status,
                    "start_date": t_date, "end_date": t_date,
                    "outbound_dep": "", "outbound_arr": "",
                    "return_dep": "", "return_arr": "",
                    "single_dep": "", "single_arr": "",
                    "days": set(), "feedback": feedback, "operator_remarks": op_remarks,
                    "is_batch": is_recur or is_round, "bus_type": bus_type, "total_seats": total_seats,
                    "distance": distance,
                    "seater_price": seater_p, "sleeper_price": sleeper_p, "ladies_price": ladies_p
                }

            group = grouped_schedules[batch_key]
            group["ids"].append(str(s_id))

            if isinstance(t_date, date):
                if t_date < group["start_date"]: group["start_date"] = t_date
                if t_date > group["end_date"]: group["end_date"] = t_date

            if day_part:
                group["days"].add(day_part)

            if "Outbound" in pattern:
                group["outbound_dep"] = dep_str
                group["outbound_arr"] = arr_str
            elif "Return" in pattern:
                group["return_dep"] = dep_str
                group["return_arr"] = arr_str
            else:
                group["single_dep"] = dep_str
                group["single_arr"] = arr_str

        schedules_list = []
        day_order = {"Mon": 1, "Tue": 2, "Wed": 3, "Thu": 4, "Fri": 5, "Sat": 6, "Sun": 7}

        for key, batch in grouped_schedules.items():
            is_round, is_recur = key[6], key[7]

            if is_recur:
                sorted_days = sorted(list(batch["days"]), key=lambda d: day_order.get(d, 99))
                days_str = ", ".join(sorted_days)
                start_str = batch["start_date"].strftime('%d %b %Y') if isinstance(batch["start_date"], date) else str(
                    batch["start_date"])
                end_str = batch["end_date"].strftime('%d %b %Y') if isinstance(batch["end_date"], date) else str(
                    batch["end_date"])
                date_display = f"{start_str} to {end_str}" if start_str != end_str else start_str
                pattern_display = f"Recurring ({days_str})"
                if is_round:
                    pattern_display = f"Round Trip - {pattern_display}"
            else:
                date_display = batch["start_date"].strftime('%d %b %Y') if isinstance(batch["start_date"],
                                                                                      date) else str(
                    batch["start_date"])
                pattern_display = "Round Trip" if is_round else "Single Trip"

            if is_round:
                timings = f"Outbound: {batch['outbound_dep']} ➝ {batch['outbound_arr']} | Return: {batch['return_dep']} ➝ {batch['return_arr']}"
            else:
                timings = f"{batch['single_dep']} ➝ {batch['single_arr']}"

            schedules_list.append({
                "ids": ",".join(batch["ids"]),
                "display_id": batch["display_id"],
                "bus": batch["bus"],
                "source": batch["source"],
                "destination": batch["destination"],
                "date": date_display,
                "timings": timings,
                "price": batch["price"],
                "status": batch["status"],
                "pattern": pattern_display,
                "feedback": batch["feedback"],
                "operator_remarks": batch["operator_remarks"],
                "is_batch": batch["is_batch"],
                "bus_type": batch["bus_type"],
                "total_seats": batch["total_seats"],
                "distance": batch["distance"],
                "seater_price": batch["seater_price"],
                "sleeper_price": batch["sleeper_price"],
                "ladies_price": batch["ladies_price"]
            })

        schedules_list.sort(key=lambda x: x['display_id'], reverse=True)
    except MySQLdb.Error as e:
        print(f"Hub Schedule Fetch Error: {e}")
        schedules_list, approved_routes, approved_buses = [], [], []

    cur.close()

    return render_template("operator/operations_hub.html", operator_name=get_operator_name(), routes=routes,
                           gujarat_cities=gujarat_cities, buses=buses, schedules=schedules_list,
                           approved_routes=approved_routes, approved_buses=approved_buses, search_query=search_query)


@app.route("/operator_bulk_submit", methods=["POST"])
def operator_bulk_submit():
    if "operator" not in session: return redirect(url_for("login_page"))
    operator_id = session["operator"]
    cur = mysql.connection.cursor()

    messages = []
    actions_taken = 0

    # 1. PROCESS ROUTE CREATION
    source_city = request.form.get("source_city")
    destination_city = request.form.get("destination_city")
    distance_km = request.form.get("distance_km")

    if source_city and destination_city and distance_km:
        actions_taken += 1
        if source_city == destination_city:
            messages.append(("danger", "Route: Source and Destination cannot be the same city."))
        else:
            try:
                cur.execute(
                    "SELECT status FROM route WHERE source_city = %s AND destination_city = %s AND operator_id = %s",
                    (source_city, destination_city, operator_id))
                existing = cur.fetchone()
                if existing:
                    messages.append(
                        ("warning", f"Route: '{source_city} to {destination_city}' is already requested or active."))
                else:
                    cur.execute(
                        "INSERT INTO route (source_city, destination_city, distance_km, operator_id, status) VALUES (%s, %s, %s, %s, 0)",
                        (source_city, destination_city, distance_km, operator_id))
                    messages.append(
                        ("success", f"Route: '{source_city} to {destination_city}' requested successfully!"))
            except MySQLdb.Error as e:
                print(f"Bulk Route Error: {e}")
                messages.append(("danger", "Route: Database error occurred."))

    # 2. PROCESS FLEET REGISTRATION
    bus_number = request.form.get("bus_number")
    if bus_number:
        actions_taken += 1
        bus_number = bus_number.strip().upper()
        bus_type = request.form.get("bus_type")
        total_seats = request.form.get("total_seats")
        amenities = request.form.get("amenities")

        if not bus_type or not total_seats:
            messages.append(("danger", "Fleet: Category and Capacity are required to register a vehicle."))
        else:
            try:
                cur.execute("SELECT COUNT(*) FROM bus WHERE operator_id = %s AND layout_status != 3", (operator_id,))
                if cur.fetchone()[0] >= 3:
                    messages.append(("danger", "Fleet: Maximum 3 active vehicles reached."))
                else:
                    cur.execute(
                        "SELECT bus_id FROM bus WHERE operator_id = %s AND bus_type = %s AND layout_status != 3",
                        (operator_id, bus_type))
                    if cur.fetchone():
                        messages.append(
                            ("warning", f"Fleet: You already own an active {bus_type} bus. Need unique categories."))
                    else:
                        cur.execute("SELECT operator_id FROM bus WHERE bus_number = %s", (bus_number,))
                        if cur.fetchone():
                            messages.append(("danger",
                                             f"Fleet: Vehicle '{bus_number}' is already registered in the global network."))
                        else:
                            cur.execute(
                                "INSERT INTO bus (operator_id, bus_number, bus_type, total_seats, amenities, layout_status) VALUES (%s, %s, %s, %s, %s, 0)",
                                (operator_id, bus_number, bus_type, int(total_seats), amenities))
                            messages.append(("success", f"Fleet: Vehicle '{bus_number}' successfully registered!"))
            except MySQLdb.Error as e:
                print(f"Bulk Fleet Error: {e}")
                messages.append(("danger", "Fleet: Database error occurred."))

    # 3. PROCESS FARE CONFIGURATION & SCHEDULE DEPLOYMENT
    sched_bus_id = request.form.get("sched_bus_id")
    sched_route_id = request.form.get("sched_route_id")
    departure_time = request.form.get("departure_time")

    # Capture New Fare Configs
    seater_price = request.form.get("seater_price")
    sleeper_price = request.form.get("sleeper_price")
    ladies_price = request.form.get("ladies_price")

    has_schedule_input = bool(departure_time)
    has_price_input = bool(seater_price or sleeper_price or ladies_price)

    if has_schedule_input or has_price_input:
        if not sched_bus_id:
            actions_taken += 1
            messages.append(("danger",
                             "Operations: You must select an Approved Fleet vehicle to update fares or deploy schedules."))
        else:
            try:
                # Security Check
                cur.execute("SELECT operator_id FROM bus WHERE bus_id = %s", (sched_bus_id,))
                bus_owner = cur.fetchone()

                if not bus_owner or bus_owner[0] != operator_id:
                    actions_taken += 1
                    messages.append(("danger", "Security Alert - You are not authorized to manage this vehicle."))
                else:
                    # --- 3A. Process Fare Config ---
                    if has_price_input:
                        actions_taken += 1
                        price_updated = False
                        if seater_price and float(seater_price) > 0:
                            cur.execute("UPDATE seat SET seat_price = %s WHERE bus_id = %s AND seat_type = 'seater'",
                                        (seater_price, sched_bus_id))
                            price_updated = True
                        if sleeper_price and float(sleeper_price) > 0:
                            cur.execute("UPDATE seat SET seat_price = %s WHERE bus_id = %s AND seat_type = 'sleeper'",
                                        (sleeper_price, sched_bus_id))
                            price_updated = True
                        if ladies_price and float(ladies_price) > 0:
                            cur.execute("UPDATE seat SET seat_price = %s WHERE bus_id = %s AND seat_type = 'ladies'",
                                        (ladies_price, sched_bus_id))
                            price_updated = True

                        if price_updated:
                            messages.append(("success", "Fare Config: Seat prices synced directly to the Architect!"))

                    # --- 3B. Process Timetable Deployment ---
                    if has_schedule_input:
                        actions_taken += 1
                        if not sched_route_id:
                            messages.append(
                                ("danger", "Schedule: You must select an Approved Route to deploy a timetable."))
                        else:
                            # Verify prices exist before allowing a schedule
                            cur.execute("SELECT MIN(seat_price) FROM seat WHERE bus_id = %s AND seat_price > 0",
                                        (sched_bus_id,))
                            min_price_data = cur.fetchone()
                            ticket_price = min_price_data[0] if min_price_data and min_price_data[0] else 0

                            if ticket_price == 0:
                                messages.append(("danger",
                                                 "Schedule: Please set base prices in the Fare Config before scheduling this bus."))
                            else:
                                schedule_type = request.form.get("schedule_type", "single")
                                is_recurring = request.form.get("is_recurring")
                                arrival_time = request.form.get("arrival_time")

                                if not arrival_time:
                                    messages.append(("danger", "Schedule: Departure and Arrival times are required."))
                                else:
                                    if is_recurring:
                                        start_date_str = request.form.get("start_date")
                                        end_date_str = request.form.get("end_date")
                                        selected_days = request.form.getlist("days")

                                        if not start_date_str or not end_date_str or not selected_days:
                                            messages.append(("danger",
                                                             "Schedule: Dates and Operating Days are required for recurring schedules."))
                                        else:
                                            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
                                            end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
                                            day_map = {'0': 'Mon', '1': 'Tue', '2': 'Wed', '3': 'Thu', '4': 'Fri',
                                                       '5': 'Sat', '6': 'Sun'}
                                            trips_created = 0
                                            curr_d = start_date

                                            while curr_d <= end_date:
                                                if str(curr_d.weekday()) in selected_days:
                                                    day_name = day_map[str(curr_d.weekday())]
                                                    if schedule_type == "single":
                                                        cur.execute(
                                                            "INSERT INTO schedule (bus_id, route_id, travel_date, departure_time, arrival_time, ticket_price, status, schedule_pattern) VALUES (%s, %s, %s, %s, %s, %s, 0, %s)",
                                                            (sched_bus_id, sched_route_id, curr_d.strftime('%Y-%m-%d'),
                                                             departure_time, arrival_time, ticket_price,
                                                             f"Recurring ({day_name})"))
                                                        trips_created += 1
                                                    elif schedule_type == "round":
                                                        return_dep, return_arr = request.form.get(
                                                            "return_departure_time"), request.form.get(
                                                            "return_arrival_time")
                                                        if return_dep and return_arr:
                                                            cur.execute(
                                                                "INSERT INTO schedule (bus_id, route_id, travel_date, departure_time, arrival_time, ticket_price, status, schedule_pattern) VALUES (%s, %s, %s, %s, %s, %s, 0, %s)",
                                                                (sched_bus_id, sched_route_id,
                                                                 curr_d.strftime('%Y-%m-%d'), departure_time,
                                                                 arrival_time, ticket_price,
                                                                 f"Round Trip (Outbound) - Recurring ({day_name})"))
                                                            cur.execute(
                                                                "INSERT INTO schedule (bus_id, route_id, travel_date, departure_time, arrival_time, ticket_price, status, schedule_pattern) VALUES (%s, %s, %s, %s, %s, %s, 0, %s)",
                                                                (sched_bus_id, sched_route_id,
                                                                 curr_d.strftime('%Y-%m-%d'), return_dep, return_arr,
                                                                 ticket_price,
                                                                 f"Round Trip (Return) - Recurring ({day_name})"))
                                                            trips_created += 2
                                                curr_d += timedelta(days=1)
                                            if trips_created > 0:
                                                messages.append(("success",
                                                                 f"Schedule: Successfully deployed {trips_created} recurring trips!"))
                                    else:
                                        trip_date = request.form.get("trip_date")
                                        if not trip_date:
                                            messages.append(
                                                ("danger", "Schedule: Trip Date is required for a single schedule."))
                                        else:
                                            if schedule_type == "single":
                                                cur.execute(
                                                    "INSERT INTO schedule (bus_id, route_id, travel_date, departure_time, arrival_time, ticket_price, status, schedule_pattern) VALUES (%s, %s, %s, %s, %s, %s, 0, %s)",
                                                    (sched_bus_id, sched_route_id, trip_date, departure_time,
                                                     arrival_time, ticket_price, "Single Trip"))
                                                messages.append(
                                                    ("success", "Schedule: Single trip successfully deployed!"))
                                            elif schedule_type == "round":
                                                return_dep, return_arr = request.form.get(
                                                    "return_departure_time"), request.form.get("return_arrival_time")
                                                if return_dep and return_arr:
                                                    cur.execute(
                                                        "INSERT INTO schedule (bus_id, route_id, travel_date, departure_time, arrival_time, ticket_price, status, schedule_pattern) VALUES (%s, %s, %s, %s, %s, %s, 0, %s)",
                                                        (sched_bus_id, sched_route_id, trip_date, departure_time,
                                                         arrival_time, ticket_price, "Round Trip (Outbound)"))
                                                    cur.execute(
                                                        "INSERT INTO schedule (bus_id, route_id, travel_date, departure_time, arrival_time, ticket_price, status, schedule_pattern) VALUES (%s, %s, %s, %s, %s, %s, 0, %s)",
                                                        (sched_bus_id, sched_route_id, trip_date, return_dep,
                                                         return_arr, ticket_price, "Round Trip (Return)"))
                                                    messages.append(
                                                        ("success", "Schedule: Round trip schedules generated!"))
                                                else:
                                                    messages.append(("danger",
                                                                     "Schedule: Return times are required for round trips."))
            except MySQLdb.Error as e:
                print(f"Bulk Schedule Error: {e}")
                messages.append(("danger", "Database error occurred while processing operations."))

    mysql.connection.commit()
    cur.close()

    if actions_taken == 0:
        flash("No data was provided. Please fill out Route, Fleet, Fares, or Timetable before deploying.", "warning")
    else:
        for cat, msg in messages:
            flash(msg, cat)

    return redirect(url_for('operator_operations_hub') + '?tab=status')


@app.route("/operator_route_reply/<int:route_id>", methods=["POST"])
def operator_route_reply(route_id):
    if "operator" not in session: return redirect(url_for("login_page"))
    remarks = request.form.get("operator_remarks", "").strip()
    try:
        cur = mysql.connection.cursor()
        cur.execute("UPDATE route SET operator_remarks = %s WHERE route_id = %s", (remarks, route_id))
        mysql.connection.commit()
        cur.close()
        flash("Message sent to Admin.", "success")
    except MySQLdb.Error as e:
        print(f"Route Reply Error: {e}")
        flash("Error saving message.", "danger")
    return redirect(url_for('operator_operations_hub') + '?tab=status')


@app.route("/operator_layout_reply/<int:bus_id>", methods=["POST"])
def operator_layout_reply(bus_id):
    if "operator" not in session: return redirect(url_for("login_page"))
    remarks = request.form.get("operator_remarks", "").strip()
    try:
        cur = mysql.connection.cursor()
        cur.execute("UPDATE bus SET operator_remarks = %s WHERE bus_id = %s", (remarks, bus_id))
        mysql.connection.commit()
        cur.close()
        flash("Message sent to Admin.", "success")
    except MySQLdb.Error as e:
        print(f"Layout Reply Error: {e}")
        flash("Error saving message.", "danger")
    return redirect(url_for('operator_operations_hub') + '?tab=status')


@app.route("/operator_schedule_reply/<int:schedule_id>", methods=["POST"])
def operator_schedule_reply(schedule_id):
    if "operator" not in session: return redirect(url_for("login_page"))
    remarks = request.form.get("operator_remarks", "").strip()
    try:
        cur = mysql.connection.cursor()
        cur.execute("UPDATE schedule SET operator_remarks = %s WHERE schedule_id = %s", (remarks, schedule_id))
        mysql.connection.commit()
        cur.close()
        flash("Message sent to Admin.", "success")
    except MySQLdb.Error as e:
        print(f"Schedule Reply Error: {e}")
        flash("Error saving message.", "danger")
    return redirect(url_for('operator_operations_hub') + '?tab=status')


@app.route("/operator_build_layout/<int:bus_id>")
def operator_build_layout(bus_id):
    if "operator" not in session: return redirect(url_for("login_page"))
    cur = mysql.connection.cursor()

    cur.execute("SELECT bus_id, bus_number, total_seats FROM bus WHERE bus_id = %s AND operator_id = %s",
                (bus_id, session["operator"]))
    bus_data = cur.fetchone()
    if not bus_data:
        cur.close()
        return redirect(url_for("operator_operations_hub", tab="command"))

    bus_info = {"bus_id": bus_data[0], "bus_number": bus_data[1], "total_seats": bus_data[2]}
    cur.execute("SELECT row_num, col_num, seat_type, seat_number, seat_price FROM seat WHERE bus_id = %s", (bus_id,))
    seats = cur.fetchall()

    layout_data = []
    price_seater, price_sleeper, price_ladies = 0, 0, 0

    for s in seats:
        price = float(s[4]) if s[4] else 0
        seat_type = s[2]
        layout_data.append({"row": s[0], "col": s[1], "type": seat_type, "id": s[3] or "", "price": price})
        if price > 0:
            if seat_type == 'seater':
                price_seater = int(price)
            elif seat_type == 'sleeper':
                price_sleeper = int(price)
            elif seat_type == 'ladies':
                price_ladies = int(price)

    cur.close()
    return render_template("operator/seat_layout_architect.html", operator_name=get_operator_name(), bus=bus_info,
                           layout_data=layout_data, price_seater=price_seater, price_sleeper=price_sleeper,
                           price_ladies=price_ladies)


@app.route("/operator_save_layout/<int:bus_id>", methods=["POST"])
def operator_save_layout(bus_id):
    if "operator" not in session: return {"success": False, "error": "Unauthorized"}
    data = request.json
    layout_grid = data.get("layout", [])

    try:
        cur = mysql.connection.cursor()
        cur.execute("SELECT COUNT(*) FROM booked_seat bs JOIN seat s ON bs.seat_id = s.seat_id WHERE s.bus_id = %s",
                    (bus_id,))
        booked_count = cur.fetchone()[0]

        if booked_count > 0:
            for r_idx, row in enumerate(layout_grid):
                for c_idx, cell in enumerate(row):
                    seat_type = cell.get("type")
                    price = cell.get("price", 0)
                    if seat_type and seat_type not in ["empty", "erase"]:
                        cur.execute(
                            "UPDATE seat SET seat_price = %s WHERE bus_id = %s AND row_num = %s AND col_num = %s AND seat_type = %s",
                            (price, bus_id, r_idx, c_idx, seat_type))

            mysql.connection.commit()
            cur.close()
            flash("Layout locked (Tickets Sold). Prices updated!", "success")
            return {"success": True}
        else:
            cur.execute("DELETE FROM seat WHERE bus_id = %s", (bus_id,))
            for r_idx, row in enumerate(layout_grid):
                for c_idx, cell in enumerate(row):
                    seat_type = cell.get("type")
                    seat_number = cell.get("id", "").strip() or None
                    price = cell.get("price", 0)
                    if seat_type and seat_type not in ["empty", "erase"]:
                        cur.execute(
                            "INSERT INTO seat (bus_id, seat_number, seat_type, seat_status, row_num, col_num, seat_price) VALUES (%s, %s, %s, 1, %s, %s, %s)",
                            (bus_id, seat_number, seat_type, r_idx, c_idx, price))

            cur.execute("UPDATE bus SET layout_status = 1 WHERE bus_id = %s", (bus_id,))
            mysql.connection.commit()
            cur.close()
            flash("Layout and pricing deployed!", "success")
            return {"success": True}

    except MySQLdb.IntegrityError as e:
        if e.args[0] == 1451: return {"success": False, "error": "Passengers have booked tickets!"}
        return {"success": False, "error": str(e)}
    except MySQLdb.Error as e:
        print(f"Error saving layout: {e}")
        return {"success": False, "error": str(e)}


@app.route("/operator_quick_price_update", methods=["POST"])
def operator_quick_price_update():
    if "operator" not in session: return redirect(url_for("login_page"))
    operator_id = session["operator"]

    bus_id = request.form.get("price_bus_id")
    seater_price = request.form.get("seater_price", 0)
    sleeper_price = request.form.get("sleeper_price", 0)
    ladies_price = request.form.get("ladies_price", 0)

    if not bus_id:
        flash("Please select an approved bus to update prices.", "warning")
        return redirect(url_for("operator_operations_hub") + '?tab=command')

    try:
        cur = mysql.connection.cursor()

        # Security Check: Verify this operator actually owns this bus
        cur.execute("SELECT operator_id FROM bus WHERE bus_id = %s", (bus_id,))
        owner = cur.fetchone()
        if not owner or owner[0] != operator_id:
            flash("Security Alert - Unauthorized access to vehicle.", "danger")
            return redirect(url_for("operator_operations_hub") + '?tab=command')

        # Run bulk updates across all seats mapped to this bus
        updates_made = False

        if seater_price and float(seater_price) > 0:
            cur.execute("UPDATE seat SET seat_price = %s WHERE bus_id = %s AND seat_type = 'seater'",
                        (seater_price, bus_id))
            updates_made = True

        if sleeper_price and float(sleeper_price) > 0:
            cur.execute("UPDATE seat SET seat_price = %s WHERE bus_id = %s AND seat_type = 'sleeper'",
                        (sleeper_price, bus_id))
            updates_made = True

        if ladies_price and float(ladies_price) > 0:
            cur.execute("UPDATE seat SET seat_price = %s WHERE bus_id = %s AND seat_type = 'ladies'",
                        (ladies_price, bus_id))
            updates_made = True

        if updates_made:
            mysql.connection.commit()
            flash("Bus seat prices updated successfully across the entire layout!", "success")
        else:
            flash("No valid prices entered to update.", "warning")

        cur.close()
    except MySQLdb.Error as e:
        print(f"Quick Price Update Error: {e}")
        flash("Database error while updating prices.", "danger")

    return redirect(url_for("operator_operations_hub") + '?tab=command')


@app.route("/operator_view_commission")
def operator_view_commission():
    if "operator" not in session: return redirect(url_for("login_page"))
    operator_id = session["operator"]
    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    try:
        cur.execute("SELECT commission_rate FROM operator WHERE operator_id = %s", (operator_id,))
        op_data = cur.fetchone()
        commission_rate = float(op_data['commission_rate']) if op_data and op_data[
            'commission_rate'] is not None else 10.0

        cur.execute("""
            SELECT settlement_id, total_booking_amount as gross, commission_amount as platform_fee, 
                   net_payable_amount as payout, settlement_period, settlement_date, settlement_status
            FROM settlement
            WHERE operator_id = %s
            ORDER BY settlement_date DESC
        """, (operator_id,))
        settlements = cur.fetchall()

    except MySQLdb.Error as e:
        print(f"Operator Commission Error: {e}")
        commission_rate = 10.0
        settlements = []
    finally:
        cur.close()

    return render_template("operator/view_commission.html",
                           operator_name=get_operator_name(),
                           commission_rate=commission_rate,
                           settlements=settlements)


@app.route("/operator_payout_receipt/<int:settlement_id>")
def operator_payout_receipt(settlement_id):
    if "operator" not in session: return redirect(url_for("login_page"))
    operator_id = session["operator"]

    try:
        cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

        cur.execute("""
            SELECT s.settlement_id, s.total_booking_amount, s.commission_amount, s.net_payable_amount, s.settlement_date, s.settlement_period,
                   o.operator_name, o.upi_id, o.bank_name, o.account_number, o.ifsc_code
            FROM settlement s
            JOIN operator o ON s.operator_id = o.operator_id
            WHERE s.settlement_id = %s AND s.operator_id = %s
        """, (settlement_id, operator_id))

        receipt = cur.fetchone()
        cur.close()

        if not receipt:
            flash("Receipt not found or you do not have permission to view it.", "danger")
            return redirect(url_for("operator_view_commission"))

        return render_template("operator/payout_receipt.html", operator_name=get_operator_name(), receipt=receipt)
    except MySQLdb.Error as e:
        print(f"Operator Receipt Fetch Error: {e}")
        return redirect(url_for("operator_view_commission"))


@app.route("/operator_offer_reply/<int:offer_id>", methods=["POST"])
def operator_offer_reply(offer_id):
    if "operator" not in session: return redirect(url_for("login_page"))
    remarks = request.form.get("operator_remarks", "").strip()

    try:
        cur = mysql.connection.cursor()
        cur.execute("UPDATE offer SET operator_remarks = %s WHERE offer_id = %s AND operator_id = %s",
                    (remarks, offer_id, session["operator"]))
        mysql.connection.commit()
        cur.close()
        flash("Your message was sent to the Admin.", "success")
    except MySQLdb.Error as e:
        print(f"Offer Reply Error: {e}")
        flash(f"Error sending message.", "danger")

    return redirect(url_for("operator_create_offer"))


@app.route("/operator_create_offer", methods=["GET", "POST"])
def operator_create_offer():
    if "operator" not in session: return redirect(url_for("login_page"))
    operator_id = session["operator"]
    cur = mysql.connection.cursor()

    if request.method == "POST":
        try:
            cur.execute(
                "INSERT INTO offer (operator_id, offer_title, offer_code, discount_percentage, valid_until, status) VALUES (%s, %s, %s, %s, %s, 0)",
                (operator_id, request.form.get("offer_title"), request.form.get("offer_code").strip().upper(),
                 request.form.get("discount_percentage"), request.form.get("valid_until")))
            mysql.connection.commit()
            flash("Promo Code submitted for Admin approval!", "success")
        except MySQLdb.Error as e:
            print(f"Create Offer Error: {e}")
            flash(f"Database Error processing offer.", "danger")
        return redirect(url_for("operator_create_offer"))

    search_query = request.args.get("search", "").strip()

    if search_query:
        cur.execute("""
            SELECT offer_id, offer_title, offer_code, discount_percentage, valid_until, status, admin_feedback, operator_remarks 
            FROM offer 
            WHERE operator_id = %s AND (offer_title LIKE %s OR offer_code LIKE %s)
            ORDER BY offer_id DESC
        """, (operator_id, f"%{search_query}%", f"%{search_query}%"))
    else:
        cur.execute("""
            SELECT offer_id, offer_title, offer_code, discount_percentage, valid_until, status, admin_feedback, operator_remarks 
            FROM offer 
            WHERE operator_id = %s 
            ORDER BY offer_id DESC
        """, (operator_id,))

    offers_list = [
        {"id": r[0], "title": r[1], "code": r[2], "discount": float(r[3]), "valid_until": r[4].strftime('%d %b %Y'),
         "status": r[5], "feedback": r[6], "operator_remarks": r[7]} for r in cur.fetchall()]
    cur.close()

    return render_template("operator/create_offer.html", operator_name=get_operator_name(), offers=offers_list,
                           today=datetime.now().strftime('%Y-%m-%d'), search_query=search_query)


@app.route('/operator_view_users')
def operator_view_users():
    if 'operator' not in session: return redirect(url_for('login_page'))
    operator_id = session['operator']
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    cursor.execute("""
        SELECT DISTINCT u.user_id as id, u.full_name as name, u.email, u.mobile_no as mobile, u.gender, u.age,
               GROUP_CONCAT(DISTINCT bk.seat_numbers SEPARATOR ', ') as all_seats,
               COUNT(bk.booking_id) as total_trips
        FROM user u 
        JOIN booking bk ON u.user_id = bk.user_id 
        JOIN schedule s ON bk.schedule_id = s.schedule_id 
        JOIN bus b ON s.bus_id = b.bus_id 
        WHERE b.operator_id = %s AND bk.booking_status = 1
        GROUP BY u.user_id, u.full_name, u.email, u.mobile_no, u.gender, u.age
        ORDER BY u.full_name ASC
    """, (operator_id,))

    users = cursor.fetchall()

    for u in users:
        u['gender'] = u['gender'] if u['gender'] else ""
        u['age'] = u['age'] if u['age'] else ""

        if u['all_seats']:
            seat_list = list(set([s.strip() for s in u['all_seats'].replace(',,', ',').split(',') if s.strip()]))
            u['all_seats'] = ", ".join(seat_list)
        else:
            u['all_seats'] = ""

    cursor.close()

    return render_template('operator/view_users.html', operator_name=get_operator_name(), users=users)


@app.route('/operator_view_cancellations')
def operator_view_cancellations():
    if 'operator' not in session:
        return redirect(url_for('login_page'))

    operator_id = session['operator']
    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    try:
        cur.execute("""
            SELECT c.cancellation_id, c.cancel_date, c.refund_amount, c.refund_status, c.cancel_reason,
                   b.booking_id, b.seat_numbers, u.full_name, u.mobile_no, 
                   r.source_city, r.destination_city, s.travel_date, bs.bus_number, s.schedule_pattern
            FROM cancellation c
            JOIN booking b ON c.booking_id = b.booking_id
            JOIN user u ON b.user_id = u.user_id
            JOIN schedule s ON b.schedule_id = s.schedule_id
            JOIN route r ON s.route_id = r.route_id
            JOIN bus bs ON s.bus_id = bs.bus_id
            WHERE bs.operator_id = %s
            ORDER BY c.cancel_date DESC
        """, (operator_id,))
        cancellations = cur.fetchall()
        for c in cancellations:
            pattern = c.get('schedule_pattern')
            if pattern and "Return" in pattern:
                c['source_city'], c['destination_city'] = c['destination_city'], c['source_city']
    except MySQLdb.Error as e:
        print(f"Cancellations Load Error: {e}")
        cancellations = []
    finally:
        cur.close()

    return render_template('operator/view_cancellations.html', operator_name=get_operator_name(),
                           cancellations=cancellations)


@app.route("/operator_view_feedback")
def operator_view_feedback():
    if "operator" not in session: return redirect(url_for('login_page'))
    operator_id = session["operator"]
    cur = mysql.connection.cursor()
    try:
        cur.execute("""
                    SELECT f.feedback_id,
                           f.rating,
                           f.comments,
                           f.feedback_date,
                           u.full_name,
                           u.mobile_no,
                           r.source_city,
                           r.destination_city,
                           bk.booking_id,
                           bk.journey_date,
                           b.bus_number,
                           s.schedule_pattern
                    FROM feedback f
                             JOIN user u ON f.user_id = u.user_id
                             JOIN booking bk ON f.booking_id = bk.booking_id
                             JOIN schedule s ON bk.schedule_id = s.schedule_id
                             JOIN route r ON s.route_id = r.route_id
                             JOIN bus b ON s.bus_id = b.bus_id
                    WHERE b.operator_id = %s
                    ORDER BY f.feedback_date DESC
                    """, (operator_id,))

        raw_feedbacks = cur.fetchall()
        feedbacks = []
        for r in raw_feedbacks:
            pattern = r[11]
            if pattern and "Return" in pattern:
                src, dest = r[7], r[6]
            else:
                src, dest = r[6], r[7]

            feedbacks.append({
                "id": r[0], "rating": int(r[1]), "comments": r[2],
                "date": r[3].strftime("%d %b %Y") if r[3] else "",
                "passenger": r[4], "mobile": r[5], "route": f"{src} ⇄ {dest}", "pnr": f"RBR-{r[8]:06d}",
                "journey_date": r[9].strftime("%d %b %Y") if r[9] else "", "bus_number": r[10]
            })

        avg_rating = round(sum(f['rating'] for f in feedbacks) / len(feedbacks), 1) if feedbacks else 0.0

    except MySQLdb.Error as e:
        print(f"Feedback Load Error: {e}")
        feedbacks, avg_rating = [], 0.0
    finally:
        cur.close()

    return render_template("operator/view_feedback.html", operator_name=get_operator_name(), feedbacks=feedbacks,
                           avg_rating=avg_rating)


@app.route('/operator_view_bookings')
def operator_view_bookings():
    if 'operator' not in session: return redirect(url_for('login_page'))
    operator_id = session['operator']
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    try:
        cursor.execute("""
                       SELECT bk.booking_id      as id,
                              bk.booking_id      as pnr,
                              u.full_name        as passenger,
                              u.mobile_no        as mobile,
                              r.source_city      as source,
                              r.destination_city as destination,
                              s.travel_date      as journey_date,
                              b.bus_number       as bus,
                              bk.seat_numbers    as seats,
                              bk.total_amount    as amount,
                              bk.booking_status  as status,
                              bk.booking_date    as booking_date,
                              s.departure_time   as dep_time,
                              s.schedule_pattern as pattern
                       FROM booking bk
                                JOIN user u ON bk.user_id = u.user_id
                                JOIN schedule s ON bk.schedule_id = s.schedule_id
                                JOIN route r ON s.route_id = r.route_id
                                JOIN bus b ON s.bus_id = b.bus_id
                       WHERE b.operator_id = %s
                       ORDER BY bk.booking_date DESC
                       """, (operator_id,))
        bookings = cursor.fetchall()
        for bk in bookings:
            pattern = bk.get('pattern')
            if pattern and "Return" in pattern:
                bk['source'], bk['destination'] = bk['destination'], bk['source']

            bk['pnr'] = f"RBR-{bk['pnr']:06d}"
            bk['status'] = "Confirmed" if bk['status'] == 1 else "Failed"
            bk['time'] = (datetime.min + bk['dep_time']).strftime("%I:%M %p") if bk['dep_time'] else ""

            bk['booking_date'] = bk['booking_date'].strftime('%d %b %Y, %I:%M %p') if bk['booking_date'] else ""
            bk['journey_date'] = bk['journey_date'].strftime('%d %b %Y') if bk['journey_date'] else ""

    except MySQLdb.Error as e:
        print(f"Bookings Load Error: {e}")
        bookings = []

    cursor.close()

    return render_template('operator/view_bookings.html', operator_name=get_operator_name(), bookings=bookings)


@app.route("/operator_booking_seat_map/<int:booking_id>")
def operator_booking_seat_map(booking_id):
    if "operator" not in session: return redirect(url_for("login_page"))
    operator_id = session["operator"]

    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    try:
        # 1. Get the specific booking and schedule details
        cur.execute("""
            SELECT bk.booking_id, bk.schedule_id, bk.seat_numbers, u.full_name,
                   s.travel_date, s.departure_time, b.bus_id, b.bus_number, 
                   r.source_city, r.destination_city, s.schedule_pattern
            FROM booking bk
            JOIN user u ON bk.user_id = u.user_id
            JOIN schedule s ON bk.schedule_id = s.schedule_id
            JOIN bus b ON s.bus_id = b.bus_id
            JOIN route r ON s.route_id = r.route_id
            WHERE bk.booking_id = %s AND b.operator_id = %s
        """, (booking_id, operator_id))
        booking = cur.fetchone()

        if not booking:
            flash("Booking not found or unauthorized.", "danger")
            return redirect(url_for("operator_view_bookings"))

        # Handle pattern reversing for display
        pattern = booking.get('schedule_pattern')
        if pattern and "Return" in pattern:
            booking['source_city'], booking['destination_city'] = booking['destination_city'], booking['source_city']

        # 2. Get the entire seat architecture for this specific bus
        cur.execute("SELECT row_num, col_num, seat_type, seat_number FROM seat WHERE bus_id = %s", (booking['bus_id'],))
        all_seats = cur.fetchall()

        # 3. Get ALL currently booked seats on this specific schedule to grey them out
        cur.execute("SELECT seat_numbers FROM booking WHERE schedule_id = %s AND booking_status = 1",
                    (booking['schedule_id'],))
        all_booked = []
        for b in cur.fetchall():
            if b['seat_numbers']:
                all_booked.extend([sn.strip() for sn in b['seat_numbers'].split(',')])

        # 4. Get THIS specific passenger's seats to highlight them
        user_seats = [sn.strip() for sn in booking['seat_numbers'].split(',')] if booking['seat_numbers'] else []

        layout_data = []
        for s in all_seats:
            seat_num = s['seat_number']

            if seat_num in user_seats:
                status = 'user_booked'
            elif seat_num in all_booked:
                status = 'other_booked'
            else:
                status = 'available'

            layout_data.append({
                "row": s['row_num'], "col": s['col_num'], "type": s['seat_type'],
                "id": seat_num, "status": status
            })

    except MySQLdb.Error as e:
        print(f"Seat Map Error: {e}")
        flash("Database Error loading seat map.", "danger")
        return redirect(url_for("operator_view_bookings"))
    finally:
        cur.close()

    booking['pnr'] = f"RBR-{booking['booking_id']:06d}"
    dep_time = (datetime.min + booking['departure_time']).strftime("%I:%M %p") if booking['departure_time'] else ""
    booking['time'] = dep_time
    booking['travel_date'] = booking['travel_date'].strftime("%d %b %Y")

    return render_template("operator/booking_seat_map.html", operator_name=get_operator_name(), booking=booking,
                           layout_data=layout_data)


@app.route("/operator_view_ticket/<int:booking_id>")
def operator_view_ticket(booking_id):
    if "operator" not in session: return redirect(url_for("login_page"))
    operator_id = session["operator"]

    cur = mysql.connection.cursor()
    try:
        query = """
                SELECT bk.booking_id,
                       bk.booking_date,
                       bk.journey_date,
                       bk.total_amount,
                       bk.seat_numbers,
                       bk.booking_status,
                       u.full_name,
                       u.email,
                       u.mobile_no,
                       s.departure_time,
                       s.arrival_time,
                       r.source_city,
                       r.destination_city,
                       b.bus_number,
                       b.bus_type,
                       o.operator_name,
                       off.offer_code,
                       bk.discount_amount,
                       p.payment_method,
                       s.schedule_pattern
                FROM booking bk
                         JOIN user u ON bk.user_id = u.user_id
                         JOIN schedule s ON bk.schedule_id = s.schedule_id
                         JOIN route r ON s.route_id = r.route_id
                         JOIN bus b ON s.bus_id = b.bus_id
                         JOIN operator o ON b.operator_id = o.operator_id
                         LEFT JOIN offer off ON bk.offer_id = off.offer_id
                         LEFT JOIN payment p ON bk.booking_id = p.booking_id
                WHERE bk.booking_id = %s AND b.operator_id = %s
                """
        cur.execute(query, (booking_id, operator_id))
        result = cur.fetchone()

        if not result:
            flash("Ticket not found or unauthorized access.", "danger")
            return redirect(url_for("operator_view_bookings"))

        dep_time = (datetime.min + result[9]).strftime("%I:%M %p") if result[9] else ""
        arr_time = (datetime.min + result[10]).strftime("%I:%M %p") if result[10] else ""

        pattern = result[19]
        if pattern and "Return" in pattern:
            src = result[12]
            dest = result[11]
        else:
            src = result[11]
            dest = result[12]

        ticket_data = {
            "pnr": f"RBR-{result[0]:06d}", "booking_date": result[1].strftime("%d %b %Y, %I:%M %p"),
            "journey_date": result[2].strftime("%d %b %Y"), "total_amount": result[3],
            "seats": result[4], "status": "Confirmed" if result[5] == 1 else "Failed/Cancelled",
            "passenger_name": result[6], "passenger_email": result[7], "passenger_mobile": result[8],
            "departure_time": dep_time, "arrival_time": arr_time, "source": src,
            "destination": dest, "bus_number": result[13], "bus_type": result[14], "operator_name": result[15],
            "promo_code": result[16] if result[16] else "",
            "discount": float(result[17]) if result[17] else 0.0,
            "payment_method": result[18] if len(result) > 18 and result[18] else ""
        }
    except MySQLdb.Error as e:
        print(f"Operator Ticket Fetch Error: {e}")
        return redirect(url_for("operator_view_bookings"))
    finally:
        cur.close()

    return render_template("passenger/ticket.html", ticket=ticket_data)


@app.route('/operator_offer_usage')
def operator_offer_usage():
    if 'operator' not in session: return redirect(url_for('login_page'))
    operator_id = session['operator']
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    try:
        cursor.execute("""
            SELECT bk.booking_id as pnr,
                   u.full_name as passenger,
                   u.mobile_no as mobile,
                   o.offer_code as code,
                   o.offer_title as title,
                   bk.discount_amount as discount,
                   bk.total_amount as final_amount,
                   bk.booking_date,
                   r.source_city as source,
                   r.destination_city as destination,
                   s.schedule_pattern as pattern
            FROM booking bk
            JOIN user u ON bk.user_id = u.user_id
            JOIN offer o ON bk.offer_id = o.offer_id
            JOIN schedule s ON bk.schedule_id = s.schedule_id
            JOIN bus b ON s.bus_id = b.bus_id
            JOIN route r ON s.route_id = r.route_id
            WHERE b.operator_id = %s AND bk.booking_status = 1
            ORDER BY bk.booking_date DESC
        """, (operator_id,))
        usages = cursor.fetchall()
        for u in usages:
            pattern = u.get('pattern')
            if pattern and "Return" in pattern:
                u['source'], u['destination'] = u['destination'], u['source']
            u['pnr'] = f"RBR-{u['pnr']:06d}"
    except MySQLdb.Error as e:
        print(f"Offer Usage Load Error: {e}")
        usages = []

    cursor.close()
    return render_template('operator/offer_usage.html', operator_name=get_operator_name(), usages=usages)


@app.route('/operator_admin_communication', methods=['GET', 'POST'])
def operator_admin_communication():
    if 'operator' not in session: return redirect(url_for('login_page'))
    operator_id = session['operator']

    cur = mysql.connection.cursor()

    if request.method == 'POST':
        op_message = request.form.get("operator_message", "").strip()
        cur.execute("UPDATE operator SET operator_message = %s WHERE operator_id = %s", (op_message, operator_id))
        mysql.connection.commit()
        flash("Message sent to System Administration.", "success")
        return redirect(url_for('operator_admin_communication'))

    cur.execute("SELECT admin_remarks, operator_message FROM operator WHERE operator_id = %s", (operator_id,))
    op_data = cur.fetchone()
    remarks = op_data[0] if op_data and op_data[0] else ""
    my_message = op_data[1] if op_data and op_data[1] else ""

    cur.execute("SELECT admin_name FROM admin LIMIT 1")
    admin_data = cur.fetchone()
    admin_name = admin_data[0] if admin_data else "System Administrator"

    cur.close()

    return render_template('operator/admin_communication.html',
                           operator_name=get_operator_name(),
                           admin_name=admin_name,
                           remarks=remarks,
                           my_message=my_message)