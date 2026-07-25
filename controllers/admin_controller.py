from flask import render_template, request, redirect, url_for, flash, session, make_response
from app import app
import MySQLdb
# noinspection PyUnresolvedReferences
from database.db_connection import mysql
import os
import io
import csv
from werkzeug.utils import secure_filename
from datetime import datetime, date, timedelta


@app.context_processor
def inject_admin_notifications():
    """Automatically injects pending notification counts into all admin templates."""
    if "admin" in session:
        try:
            cur = mysql.connection.cursor()
            counts = {}

            cur.execute("SELECT COUNT(*) FROM operator WHERE account_status = 0")
            counts['pending_operators'] = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM route WHERE status = 0")
            counts['pending_routes'] = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM schedule WHERE status = 0")
            counts['pending_schedules'] = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM bus WHERE layout_status = 1")
            counts['pending_layouts'] = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM offer WHERE status = 0")
            counts['pending_offers'] = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM support_ticket WHERE status = 0")
            counts['pending_support'] = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM cancellation WHERE refund_status = 0")
            counts['pending_refunds'] = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM operator WHERE operator_message IS NOT NULL AND operator_message != ''")
            counts['unread_operator_messages'] = cur.fetchone()[0]

            cur.close()
            return dict(admin_counts=counts)
        except MySQLdb.Error as e:
            print(f"Notification Engine Error: {e}")
            return dict(admin_counts={})
        except Exception as e:
            print(f"Unexpected Notification Error: {e}")
            return dict(admin_counts={})

    return dict(admin_counts={})


def get_admin_name():
    if "admin" in session:
        if "admin_name" not in session or "admin_profile_image" not in session:
            try:
                cur = mysql.connection.cursor()
                cur.execute("SELECT admin_name, profile_image FROM admin WHERE admin_id = %s", (session["admin"],))
                data = cur.fetchone()
                cur.close()

                if data:
                    session["admin_name"] = data[0]
                    session["admin_profile_image"] = data[1]
            except MySQLdb.Error as e:
                print(f"Error fetching admin name: {e}")

        return session.get("admin_name", "Super Admin")
    return "Super Admin"


@app.route("/admin_dashboard")
def admin_dashboard():
    if "admin" not in session: return redirect(url_for("login_page"))

    try:
        cur = mysql.connection.cursor()

        cur.execute("SELECT operator_id, operator_name, email FROM operator WHERE account_status = 0 LIMIT 2")
        pending_ops = [{"id": r[0], "name": r[1], "email": r[2]} for r in cur.fetchall()]

        cur.execute("SELECT route_id, source_city, destination_city FROM route WHERE status = 0 LIMIT 2")
        pending_routes = [{"id": r[0], "source": r[1], "destination": r[2]} for r in cur.fetchall()]

        cur.execute(
            "SELECT b.bus_id, b.bus_number, o.operator_name FROM bus b JOIN operator o ON b.operator_id = o.operator_id WHERE b.layout_status = 1 LIMIT 2")
        pending_layouts = [{"id": r[0], "bus_number": r[1], "operator": r[2]} for r in cur.fetchall()]

        cur.execute(
            "SELECT s.schedule_id, b.bus_number, r.source_city, r.destination_city, o.operator_name FROM schedule s JOIN bus b ON s.bus_id = b.bus_id JOIN operator o ON b.operator_id = o.operator_id JOIN route r ON s.route_id = r.route_id WHERE s.status = 0 LIMIT 2")
        pending_schedules = [{"id": r[0], "bus": r[1], "route": f"{r[2]} ⇄ {r[3]}", "operator": r[4]} for r in
                             cur.fetchall()]

        cur.execute("SELECT operator_name, operator_message FROM operator WHERE operator_message LIKE '%[EMERGENCY%'")
        emergency_alerts = [{"operator": r[0], "message": r[1]} for r in cur.fetchall()]

        cur.execute(
            "SELECT ticket_id, name, pnr, message, mobile_no, s.status FROM support_ticket s JOIN user u ON s.user_id = u.user_id WHERE message LIKE '%[EMERGENCY SOS]%' ORDER BY s.status ASC, s.ticket_id DESC")
        passenger_sos = [
            {"ticket_id": r[0], "passenger": r[1], "pnr": r[2], "message": r[3], "mobile": r[4], "status": r[5]} for r
            in cur.fetchall()]
        active_sos_count = sum(1 for sos in passenger_sos if sos["status"] == 0)

        cur.execute("SELECT COUNT(*) FROM user")
        total_users = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM operator")
        total_operators = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM bus")
        total_buses = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*), SUM(total_amount) FROM booking WHERE booking_status = 1")
        booking_data = cur.fetchone()
        total_bookings = booking_data[0] if booking_data and booking_data[0] else 0
        total_revenue = float(booking_data[1]) if booking_data and booking_data[1] else 0.0

        cur.execute("""
            SELECT SUM(bk.total_amount * (COALESCE(o.commission_rate, 10) / 100))
            FROM booking bk JOIN schedule s ON bk.schedule_id = s.schedule_id JOIN bus b ON s.bus_id = b.bus_id JOIN operator o ON b.operator_id = o.operator_id WHERE bk.booking_status = 1
        """)
        comm_data = cur.fetchone()
        total_commission = float(comm_data[0]) if comm_data and comm_data[0] else 0.0

        formatted_revenue = f"₹ {total_revenue:,.0f}"
        formatted_commission = f"₹ {total_commission:,.0f}"

        today = date.today()
        last_7_days = [today - timedelta(days=i) for i in range(6, -1, -1)]
        revenue_labels = [d.strftime("%b %d") for d in last_7_days]
        revenue_data = [0.0] * 7

        cur.execute("""
            SELECT DATE(booking_date), SUM(total_amount) FROM booking WHERE booking_status = 1 AND booking_date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) GROUP BY DATE(booking_date)
        """)
        rev_dict = {row[0]: float(row[1]) for row in cur.fetchall()}
        for i, d in enumerate(last_7_days):
            if d in rev_dict: revenue_data[i] = rev_dict[d]

        cur.execute("SELECT COUNT(*) FROM booking WHERE booking_status = 1")
        confirmed_count = cur.fetchone()[0] or 0

        cur.execute("SELECT COUNT(*) FROM booking WHERE booking_status = 0")
        failed_count = cur.fetchone()[0] or 0

        cur.execute("SELECT COUNT(*) FROM cancellation")
        cancelled_count = cur.fetchone()[0] or 0

        total_health = confirmed_count + failed_count + cancelled_count
        if total_health == 0:
            health_data = [0, 0, 0]
            health_percentages = {"confirmed": 0, "cancelled": 0, "failed": 0}
        else:
            health_data = [confirmed_count, cancelled_count, failed_count]
            health_percentages = {
                "confirmed": round((confirmed_count / total_health) * 100),
                "cancelled": round((cancelled_count / total_health) * 100),
                "failed": round((failed_count / total_health) * 100)
            }

        cur.execute("""
            SELECT o.operator_name, COUNT(b.bus_id) as bus_count FROM operator o JOIN bus b ON o.operator_id = b.operator_id GROUP BY o.operator_id, o.operator_name ORDER BY bus_count DESC LIMIT 5
        """)
        market_rows = cur.fetchall()
        market_labels = [row[0] for row in market_rows]
        market_data = [row[1] for row in market_rows]

        cur.close()

    except MySQLdb.Error as e:
        print(f"Dashboard Database Error: {e}")
        return redirect(url_for('login_page'))
    except Exception as e:
        print(f"Dashboard Generic Error: {e}")
        return redirect(url_for('login_page'))

    return render_template("admin/dashboard.html", admin_name=get_admin_name(), pending_ops=pending_ops,
                           pending_routes=pending_routes, pending_layouts=pending_layouts,
                           pending_schedules=pending_schedules, emergency_alerts=emergency_alerts,
                           passenger_sos=passenger_sos, active_sos_count=active_sos_count,
                           total_users=total_users, total_operators=total_operators, total_buses=total_buses,
                           total_bookings=total_bookings, total_revenue=formatted_revenue,
                           total_commission=formatted_commission, revenue_labels=revenue_labels,
                           revenue_data=revenue_data, health_data=health_data, health_percentages=health_percentages,
                           market_labels=market_labels, market_data=market_data)


@app.route("/admin_clear_alert/<string:op_name>", methods=["POST"])
def admin_clear_alert(op_name):
    if "admin" not in session: return redirect(url_for("login_page"))
    try:
        cur = mysql.connection.cursor()
        cur.execute("UPDATE operator SET operator_message = NULL WHERE operator_name = %s", (op_name,))
        mysql.connection.commit()
        cur.close()
        flash("Emergency alert acknowledged and cleared.", "success")
    except MySQLdb.Error as e:
        print(f"Clear Alert Error: {e}")
        flash("Error clearing alert.", "danger")

    return redirect(request.referrer or url_for("admin_dashboard"))


@app.route("/admin_view_profile")
def admin_view_profile():
    if "admin" not in session: return redirect(url_for("login_page"))

    try:
        cur = mysql.connection.cursor()
        cur.execute("SELECT admin_name, email, mobile_no, profile_image FROM admin WHERE admin_id = %s",
                    (session["admin"],))
        admin_data = cur.fetchone()
        cur.close()
        if admin_data:
            return render_template("admin/view_profile.html", admin_name=get_admin_name(), admin_email=admin_data[1],
                                   admin_mobile=admin_data[2], admin_profile_image=admin_data[3])
    except MySQLdb.Error as e:
        print(f"Admin Profile Load Error: {e}")

    return redirect(url_for("login_page"))


@app.route("/admin_update_profile", methods=["POST"])
def admin_update_profile():
    if "admin" not in session: return redirect(url_for("login_page"))
    admin_id = session["admin"]
    admin_name = request.form.get("admin_name")
    mobile_no = request.form.get("mobile_no")
    profile_img = request.files.get("profile_image")

    try:
        cur = mysql.connection.cursor()
        if profile_img and profile_img.filename != '':
            filename = secure_filename(profile_img.filename)
            unique_filename = f"admin_{admin_id}_{filename}"
            upload_folder = os.path.join(app.root_path, 'static', 'uploads', 'admins')
            os.makedirs(upload_folder, exist_ok=True)
            profile_img.save(os.path.join(upload_folder, unique_filename))

            cur.execute("UPDATE admin SET admin_name = %s, mobile_no = %s, profile_image = %s WHERE admin_id = %s",
                        (admin_name, mobile_no, unique_filename, admin_id))
            session["admin_profile_image"] = unique_filename
        else:
            cur.execute("UPDATE admin SET admin_name = %s, mobile_no = %s WHERE admin_id = %s",
                        (admin_name, mobile_no, admin_id))

        mysql.connection.commit()
        cur.close()
        session["admin_name"] = admin_name
        flash("Admin profile updated successfully!", "success")
    except MySQLdb.Error as e:
        print(f"Admin Profile Update Database Error: {e}")
        flash("Database Error updating profile.", "danger")
    except Exception as e:
        print(f"Admin Profile Update General Error: {e}")
        flash("Error updating profile.", "danger")

    return redirect(url_for("admin_view_profile"))


@app.route("/admin_approve_operator")
def admin_approve_operator():
    if "admin" not in session: return redirect(url_for("login_page"))

    try:
        cur = mysql.connection.cursor()
        search_query = request.args.get("search", "").strip()

        if search_query:
            cur.execute(
                "SELECT operator_id, operator_name, email, mobile_no, account_status FROM operator WHERE operator_name LIKE %s OR email LIKE %s OR mobile_no LIKE %s ORDER BY account_status ASC, operator_id DESC",
                (f"%{search_query}%", f"%{search_query}%", f"%{search_query}%"))
        else:
            cur.execute(
                "SELECT operator_id, operator_name, email, mobile_no, account_status,license_document, bus_registration_document FROM operator ORDER BY account_status ASC, operator_id DESC")

        operators = [{"id": op[0], "name": op[1], "email": op[2], "mobile": op[3], "status": op[4],
                      "license": op[5] if len(op) > 5 else "", "bus_doc": op[6] if len(op) > 6 else ""} for op in
                     cur.fetchall()]
        cur.close()
    except MySQLdb.Error as e:
        print(f"Fetch Operators Error: {e}")
        operators = []
        search_query = ""

    return render_template("admin/approve_operator.html", operators=operators, admin_name=get_admin_name(),
                           search_query=search_query)


@app.route("/admin_op_action/<int:op_id>/<action>")
def admin_op_action(op_id, action):
    if "admin" not in session: return redirect(url_for("login_page"))

    try:
        cur = mysql.connection.cursor()
        if action == "approve":
            cur.execute("UPDATE operator SET account_status = 1 WHERE operator_id = %s", (op_id,))
            flash("Operator approved successfully!", "success")
        elif action == "reject":
            cur.execute("UPDATE operator SET account_status = 2 WHERE operator_id = %s", (op_id,))
            flash("Operator application rejected.", "danger")
        mysql.connection.commit()
        cur.close()
    except MySQLdb.Error as e:
        print(f"Operator Action Error: {e}")
        flash("Database Error occurred.", "danger")

    return redirect(url_for("admin_approve_operator"))


@app.route("/admin_approval_hub")
def admin_approval_hub():
    if "admin" not in session: return redirect(url_for("login_page"))
    search_query = request.args.get("search", "").strip()
    status_filter = request.args.get("status_filter", "history")

    try:
        cur = mysql.connection.cursor()

        cur.execute(
            "SELECT bus_id, seat_type, MAX(seat_price) FROM seat WHERE seat_price > 0 GROUP BY bus_id, seat_type")
        seat_prices_dict = {}
        for b_id, s_type, s_price in cur.fetchall():
            if b_id not in seat_prices_dict:
                seat_prices_dict[b_id] = {}
            seat_prices_dict[b_id][s_type] = float(s_price)

        if search_query:
            cur.execute("""
                SELECT r.route_id, r.source_city, r.destination_city, r.distance_km, r.status, r.admin_feedback, r.operator_remarks, o.operator_name 
                FROM route r LEFT JOIN operator o ON r.operator_id = o.operator_id
                WHERE r.source_city LIKE %s OR r.destination_city LIKE %s OR o.operator_name LIKE %s
                ORDER BY r.status ASC, r.route_id DESC
            """, (f"%{search_query}%", f"%{search_query}%", f"%{search_query}%"))
        else:
            cur.execute("""
                SELECT r.route_id, r.source_city, r.destination_city, r.distance_km, r.status, r.admin_feedback, r.operator_remarks, o.operator_name 
                FROM route r LEFT JOIN operator o ON r.operator_id = o.operator_id
                ORDER BY r.status ASC, r.route_id DESC
            """)
        routes = [{"id": r[0], "source": r[1], "destination": r[2], "distance": r[3], "status": r[4], "feedback": r[5],
                   "operator_remarks": r[6], "operator_name": r[7]} for r in cur.fetchall()]

        if search_query:
            cur.execute("""
                SELECT b.bus_id, b.bus_number, b.bus_type, b.total_seats, o.operator_name, b.layout_status, b.admin_feedback, b.operator_remarks 
                FROM bus b JOIN operator o ON b.operator_id = o.operator_id 
                WHERE b.layout_status IN (1, 2, 3) AND (o.operator_name LIKE %s OR b.bus_number LIKE %s OR b.bus_type LIKE %s)
                ORDER BY b.layout_status ASC, b.bus_id DESC
            """, (f"%{search_query}%", f"%{search_query}%", f"%{search_query}%"))
        else:
            cur.execute("""
                SELECT b.bus_id, b.bus_number, b.bus_type, b.total_seats, o.operator_name, b.layout_status, b.admin_feedback, b.operator_remarks 
                FROM bus b JOIN operator o ON b.operator_id = o.operator_id 
                WHERE b.layout_status IN (1, 2, 3) ORDER BY b.layout_status ASC, b.bus_id DESC
            """)
        layouts = [
            {"bus_id": r[0], "bus_number": r[1], "bus_type": r[2], "seats": r[3], "operator": r[4], "status": r[5],
             "feedback": r[6], "operator_remarks": r[7]} for r in cur.fetchall()]

        if search_query:
            cur.execute("""
                SELECT s.schedule_id, o.operator_name, b.bus_number, r.source_city, r.destination_city, 
                       s.travel_date, s.departure_time, s.arrival_time, s.ticket_price, s.status, s.admin_feedback, s.operator_remarks, s.schedule_pattern, b.bus_type, b.total_seats, r.distance_km, b.bus_id
                FROM schedule s JOIN bus b ON s.bus_id = b.bus_id JOIN operator o ON b.operator_id = o.operator_id JOIN route r ON s.route_id = r.route_id 
                WHERE o.operator_name LIKE %s OR b.bus_number LIKE %s OR r.source_city LIKE %s OR r.destination_city LIKE %s OR s.travel_date LIKE %s
                ORDER BY s.status ASC, s.schedule_id DESC
            """, (f"%{search_query}%", f"%{search_query}%", f"%{search_query}%", f"%{search_query}%",
                  f"%{search_query}%"))
        else:
            cur.execute("""
                SELECT s.schedule_id, o.operator_name, b.bus_number, r.source_city, r.destination_city, 
                       s.travel_date, s.departure_time, s.arrival_time, s.ticket_price, s.status, s.admin_feedback, s.operator_remarks, s.schedule_pattern, b.bus_type, b.total_seats, r.distance_km, b.bus_id
                FROM schedule s JOIN bus b ON s.bus_id = b.bus_id JOIN operator o ON b.operator_id = o.operator_id JOIN route r ON s.route_id = r.route_id 
                ORDER BY s.status ASC, s.schedule_id DESC
            """)
        raw_schedules = cur.fetchall()

        grouped_schedules = {}
        for r in raw_schedules:
            s_id, op_name, bus_num, src, dest, t_date, d_time, a_time = r[0:8]
            price = float(r[8]) if len(r) > 8 and r[8] else 0.0
            status, feedback, op_remarks = r[9:12]

            pattern = r[12] if len(r) > 12 and r[12] else "Single Trip"
            bus_type = r[13] if len(r) > 13 and r[13] else ""
            total_seats = r[14] if len(r) > 14 and r[14] else 0
            distance = float(r[15]) if len(r) > 15 and r[15] else 0.0
            bus_id = int(r[16]) if len(r) > 16 and r[16] else 0

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

            batch_key = (op_name, bus_num, src, dest, price, status, is_round, is_recur, date_key, bus_id)

            if batch_key not in grouped_schedules:
                grouped_schedules[batch_key] = {
                    "ids": [], "display_id": s_id, "operator": op_name, "bus": bus_num,
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
                "operator": batch["operator"],
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
        cur.close()

    except MySQLdb.Error as e:
        print(f"Approval Hub Fetch Error: {e}")
        routes, layouts, schedules_list = [], [], []

    return render_template("admin/approval_hub.html", admin_name=get_admin_name(), routes=routes, layouts=layouts,
                           schedules=schedules_list, search_query=search_query, status_filter=status_filter)


@app.route("/admin_batch_action", methods=["POST"])
def admin_batch_action():
    if "admin" not in session: return redirect(url_for("login_page"))

    action = request.form.get("batch_action")
    global_feedback = request.form.get("global_feedback", "").strip()

    route_ids = request.form.getlist("route_ids")
    bus_ids = request.form.getlist("bus_ids")
    schedule_ids = request.form.getlist("schedule_ids")

    if not action:
        flash("No action selected.", "danger")
        return redirect(url_for('admin_approval_hub') + '?tab=engine')

    if not route_ids and not bus_ids and not schedule_ids:
        flash("No items selected. Please check at least one box.", "warning")
        return redirect(url_for('admin_approval_hub') + '?tab=engine')

    try:
        cur = mysql.connection.cursor()
        status_code = 1 if action == 'approve' else 2

        if route_ids:
            format_strings = ','.join(['%s'] * len(route_ids))
            cur.execute(f"UPDATE route SET status = %s, admin_feedback = %s WHERE route_id IN ({format_strings})",
                        [status_code, global_feedback] + route_ids)

        if bus_ids:
            format_strings = ','.join(['%s'] * len(bus_ids))
            bus_status = 2 if action == 'approve' else 3
            cur.execute(f"UPDATE bus SET layout_status = %s, admin_feedback = %s WHERE bus_id IN ({format_strings})",
                        [bus_status, global_feedback] + bus_ids)

        if schedule_ids:
            flat_sched_ids = []
            for sid_group in schedule_ids:
                flat_sched_ids.extend([sid.strip() for sid in sid_group.split(",") if sid.strip().isdigit()])
            if flat_sched_ids:
                format_strings = ','.join(['%s'] * len(flat_sched_ids))
                cur.execute(
                    f"UPDATE schedule SET status = %s, admin_feedback = %s WHERE schedule_id IN ({format_strings})",
                    [status_code, global_feedback] + flat_sched_ids)

        mysql.connection.commit()
        cur.close()
        flash(f"Batch {action.capitalize()} completed successfully!", "success")

    except MySQLdb.Error as e:
        print(f"Batch Action Error: {e}")
        flash("Database error during batch action.", "danger")

    return redirect(url_for('admin_approval_hub') + '?tab=registry')


@app.route("/admin_route_action/<int:route_id>/<action>", methods=["POST"])
def admin_route_action(route_id, action):
    if "admin" not in session: return redirect(url_for("login_page"))
    feedback_text = request.form.get("feedback_text", "").strip()
    try:
        cur = mysql.connection.cursor()
        if action == "approve":
            cur.execute("UPDATE route SET status = 1, admin_feedback = %s WHERE route_id = %s",
                        (feedback_text, route_id))
        elif action == "reject":
            cur.execute("UPDATE route SET status = 2, admin_feedback = %s WHERE route_id = %s",
                        (feedback_text, route_id))
        mysql.connection.commit()
        cur.close()
    except MySQLdb.Error as e:
        print(f"Admin Route Action Error: {e}")
    return redirect(url_for('admin_approval_hub') + '?tab=registry')


@app.route("/admin_seat_action/<int:bus_id>/<action>", methods=["POST"])
def admin_seat_action(bus_id, action):
    if "admin" not in session: return redirect(url_for("login_page"))
    feedback_text = request.form.get("feedback_text", "").strip()
    try:
        cur = mysql.connection.cursor()
        if action == "approve":
            cur.execute("UPDATE bus SET layout_status = 2, admin_feedback = %s WHERE bus_id = %s",
                        (feedback_text, bus_id))
        elif action == "reject":
            cur.execute("UPDATE bus SET layout_status = 3, admin_feedback = %s WHERE bus_id = %s",
                        (feedback_text, bus_id))
        mysql.connection.commit()
        cur.close()
    except MySQLdb.Error as e:
        print(f"Admin Seat Action Error: {e}")
    return redirect(url_for('admin_approval_hub') + '?tab=registry')


@app.route("/admin_schedule_action/<string:schedule_ids>/<action>", methods=["POST"])
def admin_schedule_action(schedule_ids, action):
    if "admin" not in session: return redirect(url_for("login_page"))
    feedback_text = request.form.get("feedback_text", "").strip()
    id_list = [sid.strip() for sid in schedule_ids.split(",") if sid.strip().isdigit()]
    if not id_list: return redirect(url_for("admin_approval_hub") + '?tab=registry')
    format_strings = ','.join(['%s'] * len(id_list))
    try:
        cur = mysql.connection.cursor()
        if action == "approve":
            cur.execute(f"UPDATE schedule SET status = 1, admin_feedback = %s WHERE schedule_id IN ({format_strings})",
                        [feedback_text] + id_list)
        elif action == "reject":
            cur.execute(f"UPDATE schedule SET status = 2, admin_feedback = %s WHERE schedule_id IN ({format_strings})",
                        [feedback_text] + id_list)
        mysql.connection.commit()
        cur.close()
    except MySQLdb.Error as e:
        print(f"Admin Schedule Action Error: {e}")
    return redirect(url_for('admin_approval_hub') + '?tab=registry')


@app.route("/admin_view_routes")
def admin_view_routes():
    if "admin" not in session: return redirect(url_for("login_page"))

    try:
        cur = mysql.connection.cursor()
        search_query = request.args.get("search", "").strip()

        if search_query:
            cur.execute(
                "SELECT route_id, source_city, destination_city, distance_km FROM route WHERE status = 1 AND (source_city LIKE %s OR destination_city LIKE %s) ORDER BY source_city ASC",
                (f"%{search_query}%", f"%{search_query}%"))
        else:
            cur.execute(
                "SELECT route_id, source_city, destination_city, distance_km FROM route WHERE status = 1 ORDER BY source_city ASC")

        routes = [{"id": r[0], "source": r[1], "destination": r[2], "distance": r[3]} for r in cur.fetchall()]
        cur.close()
    except MySQLdb.Error as e:
        print(f"View Active Routes Error: {e}")
        routes, search_query = [], ""

    return render_template("admin/view_routes.html", routes=routes, admin_name=get_admin_name(),
                           search_query=search_query)


@app.route("/admin_export_routes_csv")
def admin_export_routes_csv():
    if "admin" not in session: return redirect(url_for("login_page"))

    try:
        cur = mysql.connection.cursor()
        search_query = request.args.get("search", "").strip()

        if search_query:
            cur.execute(
                "SELECT route_id, source_city, destination_city, distance_km FROM route WHERE status = 1 AND (source_city LIKE %s OR destination_city LIKE %s) ORDER BY source_city ASC",
                (f"%{search_query}%", f"%{search_query}%"))
        else:
            cur.execute(
                "SELECT route_id, source_city, destination_city, distance_km FROM route WHERE status = 1 ORDER BY source_city ASC")

        routes = cur.fetchall()
        cur.close()

        si = io.StringIO()
        cw = csv.writer(si)
        cw.writerow(['Route ID', 'Origin (Source)', 'Destination', 'Distance (KM)'])
        for r in routes:
            cw.writerow([f"RT-{r[0]}", r[1], r[2], r[3]])

        output = make_response(si.getvalue())
        output.headers["Content-Disposition"] = "attachment; filename=active_routes.csv"
        output.headers["Content-type"] = "text/csv"
        return output

    except MySQLdb.Error as e:
        print(f"Export Active Routes Error: {e}")
        flash("Error generating CSV.", "danger")
        return redirect(url_for("admin_view_routes"))


@app.route("/admin_view_seat_layout/<int:bus_id>")
def admin_view_seat_layout(bus_id):
    if "admin" not in session: return redirect(url_for("login_page"))

    try:
        cur = mysql.connection.cursor()
        cur.execute(
            "SELECT b.bus_number, b.bus_type, o.operator_name, b.layout_status, b.admin_feedback, b.operator_remarks FROM bus b JOIN operator o ON b.operator_id = o.operator_id WHERE b.bus_id = %s",
            (bus_id,))
        bus_data = cur.fetchone()

        if not bus_data:
            cur.close()
            return redirect(url_for("admin_approval_hub") + '?tab=engine')

        bus_info = {"id": bus_id, "number": bus_data[0], "type": bus_data[1], "operator": bus_data[2],
                    "status": bus_data[3], "feedback": bus_data[4], "operator_remarks": bus_data[5]}

        cur.execute("SELECT row_num, col_num, seat_type, seat_number, seat_price FROM seat WHERE bus_id = %s",
                    (bus_id,))
        layout_data = [{"row": s[0], "col": s[1], "type": s[2], "id": s[3], "price": float(s[4]) if s[4] else 0} for s
                       in
                       cur.fetchall()]
        cur.close()
    except MySQLdb.Error as e:
        print(f"Admin View Seat Layout Error: {e}")
        return redirect(url_for("admin_approval_hub") + '?tab=engine')

    return render_template("admin/view_seat_layout.html", bus=bus_info, layout_data=layout_data,
                           admin_name=get_admin_name())


@app.route("/admin_update_layout/<int:bus_id>", methods=["POST"])
def admin_update_layout(bus_id):
    if "admin" not in session: return {"success": False, "error": "Unauthorized"}
    data = request.json
    layout_grid = data.get("layout", [])

    try:
        cur = mysql.connection.cursor()
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
        mysql.connection.commit()
        cur.close()
        return {"success": True}
    except MySQLdb.Error as e:
        print(f"Error updating layout as admin: {e}")
        return {"success": False, "error": str(e)}


@app.route('/admin_view_users')
def admin_view_users():
    if 'admin' not in session: return redirect(url_for('login_page'))
    try:
        cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cur.execute("""
            SELECT user_id, full_name, email, mobile_no, status, 
                   gender, age, upi_provider, upi_id, name_on_card, card_number, card_expiry, card_cvv 
            FROM user ORDER BY user_id DESC
        """)

        users = []
        for row in cur.fetchall():
            users.append({
                "id": row['user_id'],
                "name": row['full_name'],
                "email": row['email'],
                "mobile": row['mobile_no'],
                "status": row['status'],
                "gender": row['gender'] or "",
                "age": row['age'] or "",
                "upi_provider": row['upi_provider'] or "",
                "upi_id": row['upi_id'] or "",
                "name_on_card": row['name_on_card'] or "",
                "card_number": row['card_number'] or "",
                "card_expiry": row['card_expiry'] or "",
                "card_cvv": "•••" if row['card_cvv'] else ""
            })
        cur.close()
    except MySQLdb.Error as e:
        print(f"Admin View Users Error: {e}")
        users = []

    return render_template('admin/view_users.html', users=users, admin_name=get_admin_name())


@app.route('/admin_export_users_csv')
def admin_export_users_csv():
    if 'admin' not in session: return redirect(url_for('login_page'))

    try:
        cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cur.execute("""
            SELECT user_id, full_name, email, mobile_no, status, 
                   gender, age, upi_provider, upi_id, name_on_card, card_number, card_expiry 
            FROM user ORDER BY user_id DESC
        """)
        users = cur.fetchall()
        cur.close()

        si = io.StringIO()
        cw = csv.writer(si)
        cw.writerow(['User ID', 'Full Name', 'Email', 'Mobile', 'Gender', 'Age', 'Status', 'UPI Provider', 'UPI ID',
                     'Name on Card', 'Card Number', 'Card Expiry'])

        for row in users:
            u_id = f"UID-{row['user_id']}"
            status = "Active" if row['status'] == 1 else "Inactive"
            gender = row['gender'] or ""
            age = row['age'] or ""
            upi_prov = row['upi_provider'] or ""
            upi_id = row['upi_id'] or ""
            name_card = row['name_on_card'] or ""
            card_num = row['card_number'] or ""
            card_exp = row['card_expiry'] or ""

            cw.writerow([u_id, row['full_name'], row['email'], row['mobile_no'], gender, age, status, upi_prov, upi_id,
                         name_card, card_num, card_exp])

        output = make_response(si.getvalue())
        output.headers["Content-Disposition"] = "attachment; filename=passenger_directory.csv"
        output.headers["Content-type"] = "text/csv"
        return output

    except MySQLdb.Error as e:
        print(f"Export Users Error: {e}")
        flash("Error generating CSV.", "danger")
        return redirect(url_for("admin_view_users"))


@app.route('/admin_view_operators')
def admin_view_operators():
    if 'admin' not in session: return redirect(url_for('login_page'))
    try:
        cur = mysql.connection.cursor()
        cur.execute(
            "SELECT operator_id, operator_name, email, mobile_no, account_status, admin_remarks, operator_message, age, gender FROM operator ORDER BY operator_id DESC")
        operators = [
            {
                "id": row[0], "name": row[1], "email": row[2], "mobile": row[3],
                "status": row[4], "remarks": row[5], "operator_message": row[6],
                "age": row[7] if len(row) > 7 and row[7] else "",
                "gender": row[8] if len(row) > 8 and row[8] else ""
            } for row in cur.fetchall()]
        cur.close()
    except MySQLdb.Error as e:
        print(f"Admin View Operators Error: {e}")
        operators = []

    return render_template('admin/view_operators.html', operators=operators, admin_name=get_admin_name())


@app.route('/admin_export_operators_csv')
def admin_export_operators_csv():
    if 'admin' not in session: return redirect(url_for('login_page'))

    try:
        cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cur.execute("""
            SELECT operator_id, operator_name, email, mobile_no, account_status, admin_remarks, age, gender 
            FROM operator ORDER BY operator_id DESC
        """)
        operators = cur.fetchall()
        cur.close()

        si = io.StringIO()
        cw = csv.writer(si)
        cw.writerow(['Node ID', 'Operator Name', 'Age', 'Gender', 'Email', 'Mobile', 'Account Status', 'Admin Remarks'])

        for row in operators:
            op_id = f"OP-{row['operator_id']}"
            if row['account_status'] == 1:
                status = "Authorized"
            elif row['account_status'] == 2:
                status = "Terminated"
            else:
                status = "Pending"

            remarks = row['admin_remarks'] or ""
            age = row.get('age') or ""
            gender = row.get('gender') or ""

            cw.writerow([op_id, row['operator_name'], age, gender, row['email'], row['mobile_no'], status, remarks])

        output = make_response(si.getvalue())
        output.headers["Content-Disposition"] = "attachment; filename=operator_directory.csv"
        output.headers["Content-type"] = "text/csv"
        return output

    except MySQLdb.Error as e:
        print(f"Export Operators Error: {e}")
        flash("Error generating CSV.", "danger")
        return redirect(url_for("admin_view_operators"))


@app.route("/admin_operator_remark/<int:op_id>", methods=["POST"])
def admin_operator_remark(op_id):
    if "admin" not in session: return redirect(url_for('login_page'))
    remark = request.form.get("admin_remarks", "").strip()
    try:
        cur = mysql.connection.cursor()
        cur.execute("UPDATE operator SET admin_remarks = %s WHERE operator_id = %s", (remark, op_id))
        mysql.connection.commit()
        cur.close()
        flash("Communication sent to the operator successfully!", "success")
    except MySQLdb.Error as e:
        print(f"Admin Operator Remark Error: {e}")
        flash("Error sending message to operator.", "danger")
    return redirect(url_for('admin_view_operators'))


@app.route("/admin_manage_commission", methods=["GET", "POST"])
def admin_manage_commission():
    if "admin" not in session: return redirect(url_for('login_page'))

    try:
        cur = mysql.connection.cursor()

        if request.method == "POST":
            commission_rate = request.form.get("commission_rate")
            operator_id = request.form.get("operator_id")

            if operator_id == "all":
                cur.execute("UPDATE operator SET commission_rate = %s", (commission_rate,))
                mysql.connection.commit()
                flash(f"Global commission rate updated to {commission_rate}% for ALL operators!", "success")
            else:
                cur.execute("UPDATE operator SET commission_rate = %s WHERE operator_id = %s",
                            (commission_rate, operator_id))
                mysql.connection.commit()
                flash("Operator commission rate updated successfully!", "success")

            return redirect(url_for('admin_manage_commission'))

        search_query = request.args.get("search", "").strip()
        if search_query:
            cur.execute(
                "SELECT operator_id, operator_name, email, commission_rate FROM operator WHERE operator_name LIKE %s OR email LIKE %s ORDER BY operator_name ASC",
                (f"%{search_query}%", f"%{search_query}%"))
        else:
            cur.execute(
                "SELECT operator_id, operator_name, email, commission_rate FROM operator ORDER BY operator_name ASC")

        operators = [
            {"id": row[0], "name": row[1], "email": row[2], "rate": float(row[3]) if row[3] is not None else 10.0}
            for row in cur.fetchall()]
        cur.close()
    except MySQLdb.Error as e:
        print(f"Admin Manage Commission Error: {e}")
        operators, search_query = [], ""

    return render_template("admin/manage_commission.html", operators=operators, admin_name=get_admin_name(),
                           search_query=search_query)


@app.route("/admin_settlement", methods=["GET"])
def admin_settlement():
    if "admin" not in session: return redirect(url_for('login_page'))

    settlements, total_gross, total_platform_fee, total_payable = [], 0, 0, 0
    past_settlements = []
    cur = None

    try:
        cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

        cur.execute("""
            SELECT o.operator_id, o.operator_name, o.commission_rate, o.upi_id, o.bank_name, o.account_number, o.ifsc_code, COUNT(bk.booking_id) as total_bookings, COALESCE(SUM(bk.total_amount), 0) as gross_revenue
            FROM operator o LEFT JOIN bus b ON o.operator_id = b.operator_id LEFT JOIN schedule s ON b.bus_id = s.bus_id LEFT JOIN booking bk ON s.schedule_id = bk.schedule_id AND bk.booking_status = 1
            WHERE o.account_status = 1 GROUP BY o.operator_id, o.operator_name, o.commission_rate, o.upi_id, o.bank_name, o.account_number, o.ifsc_code
        """)
        op_stats = cur.fetchall()

        cur.execute(
            "SELECT operator_id, COALESCE(SUM(net_payable_amount), 0) as already_paid FROM settlement WHERE settlement_status = 1 GROUP BY operator_id")
        paid_stats = {row['operator_id']: float(row['already_paid']) for row in cur.fetchall()}

        for row in op_stats:
            op_id = row['operator_id']
            rate = float(row['commission_rate']) if row['commission_rate'] is not None else 10.0
            gross = float(row['gross_revenue'])
            cut = gross * (rate / 100)
            total_earned_payable = gross - cut
            already_paid = paid_stats.get(op_id, 0.0)
            current_payable = total_earned_payable - already_paid
            if current_payable <= 0.01: current_payable = 0.0

            total_gross += gross
            total_platform_fee += cut
            total_payable += current_payable

            settlements.append(
                {"id": op_id, "name": row['operator_name'], "rate": rate, "bookings": row['total_bookings'],
                 "gross": gross, "cut": cut, "payable": current_payable,
                 "bank_name": row['bank_name'] or "",
                 "account_number": row['account_number'] or "",
                 "ifsc_code": row['ifsc_code'] or "",
                 "upi_id": row['upi_id'] or ""})

        cur.execute("""
            SELECT s.settlement_id, o.operator_name, s.total_booking_amount, s.commission_amount, s.net_payable_amount, s.settlement_period, s.settlement_date
            FROM settlement s
            JOIN operator o ON s.operator_id = o.operator_id
            ORDER BY s.settlement_date DESC
        """)
        past_settlements = cur.fetchall()

    except MySQLdb.Error as e:
        print(f"Payout Data Load Error: {e}")
    finally:
        if cur: cur.close()

    return render_template("admin/settlement.html", admin_name=get_admin_name(), settlements=settlements,
                           summary={"gross": total_gross, "platform_fee": total_platform_fee, "payable": total_payable},
                           past_settlements=past_settlements)


@app.route("/admin_process_payout", methods=["POST"])
def admin_process_payout():
    if "admin" not in session: return redirect(url_for('login_page'))

    operator_id = request.form.get("operator_id")
    amount = float(request.form.get("amount", 0))

    if amount <= 0:
        flash("Invalid payout amount or balance is already paid out.", "danger")
        return redirect(url_for("admin_settlement"))

    try:
        cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cur.execute(
            "SELECT operator_name, commission_rate, upi_id, bank_name, account_number, ifsc_code FROM operator WHERE operator_id = %s",
            (operator_id,))
        op_data = cur.fetchone()
        cur.close()

        commission_rate = float(op_data['commission_rate']) if op_data and op_data[
            'commission_rate'] is not None else 10.0
        rate_decimal = commission_rate / 100.0
        gross_amount = amount / (1 - rate_decimal)
        commission_amount = gross_amount - amount

        payout_data = {
            "operator_id": operator_id,
            "operator_name": op_data['operator_name'],
            "upi_id": op_data['upi_id'] or "",
            "bank_name": op_data['bank_name'] or "",
            "account_number": op_data['account_number'] or "",
            "ifsc_code": op_data['ifsc_code'] or "",
            "amount": amount,
            "gross_amount": gross_amount,
            "commission_amount": commission_amount,
            "commission_rate": commission_rate
        }

        return render_template("admin/process_payout.html", admin_name=get_admin_name(), payout=payout_data)
    except MySQLdb.Error as e:
        print(f"Process Payout Error: {e}")
        flash("Error loading payout details.", "danger")
        return redirect(url_for("admin_settlement"))


@app.route("/admin_confirm_payout", methods=["POST"])
def admin_confirm_payout():
    if "admin" not in session: return redirect(url_for('login_page'))

    operator_id = request.form.get("operator_id")
    amount = float(request.form.get("amount"))
    gross_amount = float(request.form.get("gross_amount"))
    commission_amount = float(request.form.get("commission_amount"))

    current_month = datetime.now().strftime("%B %Y")
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        cur = mysql.connection.cursor()
        cur.execute(
            "INSERT INTO settlement (operator_id, total_booking_amount, commission_amount, gst_on_commission, net_payable_amount, settlement_period, settlement_date, settlement_status) VALUES (%s, %s, %s, 0, %s, %s, %s, 1)",
            (operator_id, gross_amount, commission_amount, amount, current_month, today)
        )
        settlement_id = cur.lastrowid
        mysql.connection.commit()
        cur.close()

        flash("Payout successfully authorized!", "success")
        return redirect(url_for("admin_payout_receipt", settlement_id=settlement_id))
    except MySQLdb.Error as e:
        print(f"Confirm Payout Error: {e}")
        flash("Database Error saving payout.", "danger")
        return redirect(url_for("admin_settlement"))


@app.route("/admin_payout_receipt/<int:settlement_id>")
def admin_payout_receipt(settlement_id):
    if "admin" not in session: return redirect(url_for("login_page"))

    try:
        cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cur.execute("""
            SELECT s.settlement_id, s.total_booking_amount, s.commission_amount, s.net_payable_amount, s.settlement_date, s.settlement_period,
                   o.operator_name, o.upi_id, o.bank_name, o.account_number, o.ifsc_code
            FROM settlement s
            JOIN operator o ON s.operator_id = o.operator_id
            WHERE s.settlement_id = %s
        """, (settlement_id,))
        receipt = cur.fetchone()
        cur.close()

        if not receipt:
            flash("Payout receipt not found in database.", "danger")
            return redirect(url_for("admin_settlement"))

        return render_template("admin/payout_receipt.html", admin_name=get_admin_name(), receipt=receipt)
    except MySQLdb.Error as e:
        print(f"Receipt Fetch Error: {e}")
        return redirect(url_for("admin_settlement"))


@app.route("/admin_view_bookings")
def admin_view_bookings():
    if "admin" not in session: return redirect(url_for('login_page'))

    try:
        cur = mysql.connection.cursor()
        cur.execute("""
            SELECT bk.booking_id, bk.booking_date, bk.journey_date, bk.total_amount, bk.seat_numbers, bk.booking_status, u.full_name, u.mobile_no, r.source_city, r.destination_city, o.operator_name, b.bus_number
            FROM booking bk JOIN user u ON bk.user_id = u.user_id JOIN schedule s ON bk.schedule_id = s.schedule_id JOIN route r ON s.route_id = r.route_id JOIN bus b ON s.bus_id = b.bus_id JOIN operator o ON b.operator_id = o.operator_id ORDER BY bk.booking_date DESC
        """)
        bookings = [{"id": row[0], "pnr": f"RBR-{row[0]:06d}",
                     "booking_date": row[1].strftime("%d %b %Y, %I:%M %p") if row[1] else "",
                     "journey_date": row[2].strftime("%d %b %Y") if row[2] else "", "amount": float(row[3]),
                     "seats": row[4], "status": int(row[5]), "passenger": row[6], "mobile": row[7], "source": row[8],
                     "destination": row[9], "operator": row[10], "bus": row[11]} for row in cur.fetchall()]
        cur.close()
    except MySQLdb.Error as e:
        print(f"Admin View Bookings Error: {e}")
        bookings = []

    return render_template("admin/view_bookings.html", admin_name=get_admin_name(), bookings=bookings)


@app.route("/admin_export_bookings_csv")
def admin_export_bookings_csv():
    if "admin" not in session: return redirect(url_for("login_page"))

    try:
        cur = mysql.connection.cursor()
        cur.execute("""
            SELECT bk.booking_id, bk.booking_date, bk.journey_date, bk.total_amount, bk.seat_numbers, bk.booking_status, u.full_name, u.mobile_no, r.source_city, r.destination_city, o.operator_name, b.bus_number
            FROM booking bk JOIN user u ON bk.user_id = u.user_id JOIN schedule s ON bk.schedule_id = s.schedule_id JOIN route r ON s.route_id = r.route_id JOIN bus b ON s.bus_id = b.bus_id JOIN operator o ON b.operator_id = o.operator_id ORDER BY bk.booking_date DESC
        """)
        bookings = cur.fetchall()
        cur.close()

        si = io.StringIO()
        cw = csv.writer(si)
        cw.writerow(
            ['PNR', 'Booking Date', 'Journey Date', 'Passenger Name', 'Mobile', 'Route', 'Operator', 'Bus Number',
             'Seats', 'Total Amount', 'Status'])

        for row in bookings:
            pnr = f"RBR-{row[0]:06d}"
            b_date = row[1].strftime("%d %b %Y, %I:%M %p") if row[1] else ""
            j_date = row[2].strftime("%d %b %Y") if row[2] else ""
            amount = float(row[3])
            seats = row[4]
            status = "Confirmed" if row[5] == 1 else "Failed/Cancelled"
            passenger = row[6]
            mobile = row[7]
            route = f"{row[8]} to {row[9]}"
            operator = row[10]
            bus = row[11]

            cw.writerow([pnr, b_date, j_date, passenger, mobile, route, operator, bus, seats, amount, status])

        output = make_response(si.getvalue())
        output.headers["Content-Disposition"] = "attachment; filename=global_bookings.csv"
        output.headers["Content-type"] = "text/csv"
        return output

    except MySQLdb.Error as e:
        print(f"Export Global Bookings Error: {e}")
        flash("Error generating CSV.", "danger")
        return redirect(url_for("admin_view_bookings"))


@app.route("/admin_view_feedback")
def admin_view_feedback():
    if "admin" not in session: return redirect(url_for('login_page'))

    try:
        cur = mysql.connection.cursor()
        cur.execute("""
            SELECT f.feedback_id, f.rating, f.comments, f.feedback_date, u.full_name, u.mobile_no, r.source_city, r.destination_city, o.operator_name, bk.booking_id, bk.journey_date
            FROM feedback f JOIN user u ON f.user_id = u.user_id JOIN booking bk ON f.booking_id = bk.booking_id JOIN schedule s ON bk.schedule_id = s.schedule_id JOIN route r ON s.route_id = r.route_id JOIN bus b ON s.bus_id = b.bus_id JOIN operator o ON b.operator_id = o.operator_id ORDER BY f.feedback_date DESC
        """)
        feedbacks = [{"id": row[0], "rating": int(row[1]), "comments": row[2],
                      "date": row[3].strftime("%d %b %Y, %I:%M %p") if row[3] else "", "passenger": row[4],
                      "mobile": row[5], "route": f"{row[6]} ⇄ {row[7]}", "operator": row[8], "pnr": f"RBR-{row[9]:06d}",
                      "journey_date": row[10].strftime("%d %b %Y") if row[10] else ""} for row in cur.fetchall()]
        cur.close()
    except MySQLdb.Error as e:
        print(f"Admin View Feedback Error: {e}")
        feedbacks = []

    return render_template("admin/view_feedback.html", admin_name=get_admin_name(), feedbacks=feedbacks)


@app.route("/admin_view_ticket/<int:booking_id>")
def admin_view_ticket(booking_id):
    if "admin" not in session: return redirect(url_for("login_page"))

    try:
        cur = mysql.connection.cursor()
        cur.execute("""
            SELECT bk.booking_id, bk.booking_date, bk.journey_date, bk.total_amount, bk.seat_numbers, bk.booking_status, u.full_name, u.email, u.mobile_no, s.departure_time, s.arrival_time, r.source_city, r.destination_city, b.bus_number, b.bus_type, o.operator_name, off.offer_code, bk.discount_amount, p.payment_method
            FROM booking bk JOIN user u ON bk.user_id = u.user_id JOIN schedule s ON bk.schedule_id = s.schedule_id JOIN route r ON s.route_id = r.route_id JOIN bus b ON s.bus_id = b.bus_id JOIN operator o ON b.operator_id = o.operator_id LEFT JOIN offer off ON bk.offer_id = off.offer_id LEFT JOIN payment p ON bk.booking_id = p.booking_id WHERE bk.booking_id = %s
        """, (booking_id,))
        result = cur.fetchone()

        if not result:
            flash("Ticket not found in the database.", "danger")
            cur.close()
            return redirect(url_for("admin_view_bookings"))

        dep_time = (datetime.min + result[9]).strftime("%I:%M %p") if result[9] else ""
        arr_time = (datetime.min + result[10]).strftime("%I:%M %p") if result[10] else ""
        ticket_data = {"pnr": f"RBR-{result[0]:06d}", "booking_date": result[1].strftime("%d %b %Y, %I:%M %p"),
                       "journey_date": result[2].strftime("%d %b %Y"), "total_amount": result[3], "seats": result[4],
                       "status": "Confirmed" if result[5] == 1 else "Failed/Cancelled", "passenger_name": result[6],
                       "passenger_email": result[7], "passenger_mobile": result[8], "departure_time": dep_time,
                       "arrival_time": arr_time, "source": result[11], "destination": result[12],
                       "bus_number": result[13], "bus_type": result[14], "operator_name": result[15],
                       "promo_code": result[16] if result[16] else "",
                       "discount": float(result[17]) if result[17] else 0.0,
                       "payment_method": result[18] if len(result) > 18 and result[18] else ""}
        cur.close()
    except MySQLdb.Error as e:
        print(f"Admin Ticket Fetch Error: {e}")
        return redirect(url_for("admin_view_bookings"))

    return render_template("passenger/ticket.html", ticket=ticket_data)


@app.route('/admin_reports')
def admin_reports():
    if 'admin' not in session: return redirect(url_for('login_page'))

    try:
        cur = mysql.connection.cursor()
        cur.execute("SELECT COUNT(*) FROM user")
        total_users = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM operator WHERE account_status = 1")
        total_operators = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*), SUM(total_amount) FROM booking WHERE booking_status = 1")
        booking_data = cur.fetchone()
        total_bookings = booking_data[0] if booking_data and booking_data[0] else 0
        total_revenue = float(booking_data[1]) if booking_data and booking_data[1] else 0.0

        today_date = date.today()
        last_7_dates = [today_date - timedelta(days=i) for i in range(6, -1, -1)]
        rev_labels = [d.strftime("%b %d") for d in last_7_dates]
        rev_vals = [0.0] * 7

        cur.execute(
            "SELECT DATE(booking_date), SUM(total_amount) FROM booking WHERE booking_status = 1 AND booking_date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) GROUP BY DATE(booking_date)")
        rev_dict = {row[0]: float(row[1]) for row in cur.fetchall()}
        for i, d in enumerate(last_7_dates):
            if d in rev_dict: rev_vals[i] = rev_dict[d]

        cur.execute("""
            SELECT o.operator_name, COUNT(bk.booking_id), SUM(bk.total_amount) FROM operator o JOIN bus b ON o.operator_id = b.operator_id JOIN schedule s ON b.bus_id = s.bus_id JOIN booking bk ON s.schedule_id = bk.schedule_id WHERE bk.booking_status = 1 GROUP BY o.operator_id, o.operator_name ORDER BY SUM(bk.total_amount) DESC LIMIT 5
        """)
        top_operators = [{'name': op[0], 'tickets_sold': op[1], 'total_revenue': float(op[2]) if op[2] else 0.0} for op
                         in cur.fetchall()]
        stats = {'users': total_users, 'operators': total_operators, 'bookings': total_bookings,
                 'revenue': total_revenue}
        cur.close()

        return render_template('admin/reports.html', admin_name=get_admin_name(), stats=stats, rev_labels=rev_labels,
                               rev_vals=rev_vals, top_operators=top_operators)
    except MySQLdb.Error as e:
        print(f"Error rendering reports: {e}")
        flash("An error occurred fetching report data.", "danger")
        return redirect(url_for('admin_dashboard'))


@app.route("/admin_approve_offer")
def admin_approve_offer():
    if "admin" not in session: return redirect(url_for('login_page'))

    try:
        cur = mysql.connection.cursor()
        search_query = request.args.get("search", "").strip()
        status_filter = request.args.get("status_filter", "all")

        query_params = []
        base_query = """
            SELECT o.offer_id, op.operator_name, o.offer_title, o.offer_code, 
                   o.discount_percentage, o.valid_until, o.status, o.admin_feedback, o.operator_remarks 
            FROM offer o 
            JOIN operator op ON o.operator_id = op.operator_id 
            WHERE 1=1
        """

        if search_query:
            base_query += " AND (op.operator_name LIKE %s OR o.offer_title LIKE %s OR o.offer_code LIKE %s)"
            query_params.extend([f"%{search_query}%", f"%{search_query}%", f"%{search_query}%"])

        if status_filter != "all":
            base_query += " AND o.status = %s"
            query_params.append(int(status_filter))

        base_query += " ORDER BY o.status ASC, o.offer_id DESC"

        cur.execute(base_query, tuple(query_params))

        offers_list = [{"id": r[0], "operator": r[1], "title": r[2], "code": r[3], "discount": float(r[4]),
                        "valid_until": r[5].strftime('%d %b %Y'), "status": r[6], "feedback": r[7],
                        "operator_remarks": r[8]} for r in cur.fetchall()]
        cur.close()
    except MySQLdb.Error as e:
        print(f"Admin View Offers Error: {e}")
        offers_list, search_query, status_filter = [], "", "all"

    return render_template("admin/approve_offer.html", admin_name=get_admin_name(), offers=offers_list,
                           search_query=search_query, status_filter=status_filter)


@app.route("/admin_batch_offer_action", methods=["POST"])
def admin_batch_offer_action():
    if "admin" not in session: return redirect(url_for("login_page"))

    action = request.form.get("batch_action")
    global_feedback = request.form.get("global_feedback", "").strip()
    offer_ids = request.form.getlist("offer_ids")

    if not action:
        flash("No action selected.", "danger")
        return redirect(url_for('admin_approve_offer'))

    if not offer_ids:
        flash("No campaigns selected. Please check at least one box.", "warning")
        return redirect(url_for('admin_approve_offer'))

    try:
        cur = mysql.connection.cursor()
        status_code = 1 if action == 'approve' else 2

        format_strings = ','.join(['%s'] * len(offer_ids))
        cur.execute(f"UPDATE offer SET status = %s, admin_feedback = %s WHERE offer_id IN ({format_strings})",
                    [status_code, global_feedback] + offer_ids)

        mysql.connection.commit()
        cur.close()
        flash(f"Batch {action.capitalize()} completed successfully!", "success")

    except MySQLdb.Error as e:
        print(f"Batch Offer Action Error: {e}")
        flash("Database error during batch action.", "danger")

    return redirect(url_for('admin_approve_offer'))


@app.route("/admin_offer_action/<int:offer_id>/<action>", methods=["POST", "GET"])
def admin_offer_action(offer_id, action):
    if "admin" not in session: return redirect(url_for("login_page"))
    feedback_text = request.form.get("feedback_text", "").strip()

    try:
        cur = mysql.connection.cursor()
        if action == "approve":
            cur.execute("UPDATE offer SET status = 1, admin_feedback = %s WHERE offer_id = %s",
                        (feedback_text, offer_id))
            flash("Campaign approved successfully!", "success")
        elif action == "reject":
            cur.execute("UPDATE offer SET status = 2, admin_feedback = %s WHERE offer_id = %s",
                        (feedback_text, offer_id))
            flash("Campaign rejected.", "danger")

        mysql.connection.commit()
        cur.close()
    except MySQLdb.Error as e:
        print(f"Admin Offer Action Error: {e}")
        flash("Database Error saving offer action.", "danger")

    return redirect(url_for("admin_approve_offer"))


@app.route('/admin_manage_refunds')
def admin_manage_refunds():
    if 'admin' not in session:
        return redirect(url_for('login_page'))

    try:
        cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cur.execute("""
            SELECT c.cancellation_id, c.cancel_date, c.refund_amount, c.refund_status, c.cancel_reason, 
                   b.booking_id, u.full_name, u.mobile_no, r.source_city, r.destination_city, 
                   op.operator_name, p.payment_method
            FROM cancellation c 
            JOIN booking b ON c.booking_id = b.booking_id 
            JOIN user u ON b.user_id = u.user_id 
            JOIN schedule s ON b.schedule_id = s.schedule_id 
            JOIN route r ON s.route_id = r.route_id 
            JOIN bus bs ON s.bus_id = bs.bus_id 
            JOIN operator op ON bs.operator_id = op.operator_id 
            LEFT JOIN payment p ON b.booking_id = p.booking_id 
            ORDER BY c.refund_status ASC, c.cancel_date DESC
        """)
        refunds = cur.fetchall()
        cur.close()
    except MySQLdb.Error as e:
        print(f"Admin Manage Refunds Error: {e}")
        refunds = []

    return render_template('admin/manage_refunds.html', admin_name=get_admin_name(), refunds=refunds)


@app.route('/admin_process_refund/<int:cancellation_id>', methods=['POST'])
def admin_process_refund(cancellation_id):
    if 'admin' not in session:
        return redirect(url_for('login_page'))

    try:
        cur = mysql.connection.cursor()
        cur.execute("UPDATE cancellation SET refund_status = 1 WHERE cancellation_id = %s", (cancellation_id,))
        cur.execute(
            "SELECT p.payment_id, c.refund_amount, p.payment_method FROM cancellation c JOIN booking b ON c.booking_id = b.booking_id LEFT JOIN payment p ON b.booking_id = p.booking_id WHERE c.cancellation_id = %s",
            (cancellation_id,))
        data = cur.fetchone()

        if data:
            payment_id, refund_amount, payment_method = data[0], data[1], data[2]
            if payment_method == 'Cash' or refund_amount == 0:
                flash("Cancellation marked as processed. No gateway refund issued for Cash bookings.", "success")
            elif payment_id:
                cur.execute(
                    "INSERT INTO refund (cancellation_id, payment_id, refund_amount, refund_method, refund_status, processed_at) VALUES (%s, %s, %s, 'Original Source', 1, NOW())",
                    (cancellation_id, payment_id, refund_amount))
                flash("Refund authorized and processed successfully.", "success")
        mysql.connection.commit()
        cur.close()
    except MySQLdb.Error as e:
        print(f"Process Refund Error: {e}")
        flash("Database Error processing refund.", "danger")

    return redirect(url_for('admin_manage_refunds'))


@app.route("/admin_monitor_payments")
def admin_monitor_payments():
    if "admin" not in session: return redirect(url_for('login_page'))

    try:
        cur = mysql.connection.cursor()

        cur.execute("""
            SELECT bk.booking_id, bk.booking_date, bk.total_amount, bk.booking_status, u.full_name, o.operator_name, o.commission_rate, p.payment_method
            FROM booking bk JOIN user u ON bk.user_id = u.user_id JOIN schedule s ON bk.schedule_id = s.schedule_id JOIN bus b ON s.bus_id = b.bus_id JOIN operator o ON b.operator_id = o.operator_id LEFT JOIN payment p ON bk.booking_id = p.booking_id ORDER BY bk.booking_date DESC
        """)

        transactions, total_volume, total_platform_earnings, total_tax = [], 0, 0, 0
        for row in cur.fetchall():
            amount = float(row[2])
            base_amount = amount / 1.05
            tax = amount - base_amount
            platform_cut = base_amount * (float(row[6]) / 100 if row[6] else 0.10)

            if row[3] == 1:
                total_volume += amount
                total_tax += tax
                total_platform_earnings += platform_cut

            transactions.append(
                {"pnr": f"RBR-{row[0]:06d}", "date": row[1].strftime("%d %b %Y, %I:%M %p") if row[1] else "",
                 "passenger": row[4], "operator": row[5], "total": amount, "tax": tax, "platform_cut": platform_cut,
                 "status": row[3], "method": row[7] if row[7] else ""})
        cur.close()
    except MySQLdb.Error as e:
        print(f"Admin Monitor Payments Error: {e}")
        transactions, total_volume, total_platform_earnings, total_tax = [], 0, 0, 0

    return render_template("admin/monitor_payments.html", admin_name=get_admin_name(), transactions=transactions,
                           summary={"volume": total_volume, "tax": total_tax, "earnings": total_platform_earnings})


@app.route("/admin_support")
def admin_support():
    if "admin" not in session: return redirect(url_for("login_page"))

    try:
        cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        search_query = request.args.get("search", "").strip()

        if search_query:
            cur.execute(
                "SELECT ticket_id, name, email, pnr, message, admin_reply, status, created_at FROM support_ticket WHERE name LIKE %s OR email LIKE %s OR pnr LIKE %s ORDER BY status ASC, created_at DESC",
                (f"%{search_query}%", f"%{search_query}%", f"%{search_query}%"))
        else:
            cur.execute(
                "SELECT ticket_id, name, email, pnr, message, admin_reply, status, created_at FROM support_ticket ORDER BY status ASC, created_at DESC")
        tickets = cur.fetchall()
        cur.close()
    except MySQLdb.Error as e:
        print(f"Admin Support Error: {e}")
        tickets, search_query = [], ""

    return render_template("admin/support.html", admin_name=get_admin_name(), tickets=tickets,
                           search_query=search_query)


@app.route("/admin_support_action/<int:ticket_id>", methods=["POST"])
def admin_support_action(ticket_id):
    if "admin" not in session: return redirect(url_for("login_page"))
    admin_id = session["admin"]
    reply_text = request.form.get("reply_text", "").strip()

    try:
        cur = mysql.connection.cursor()
        cur.execute("UPDATE support_ticket SET admin_reply = %s, status = 1, admin_id = %s WHERE ticket_id = %s",
                    (reply_text, admin_id, ticket_id))
        mysql.connection.commit()
        cur.close()
        flash(f"Reply sent securely. Ticket TKT-{ticket_id} is now Resolved!", "success")
    except MySQLdb.Error as e:
        print(f"Admin Support Reply Error: {e}")
        flash("An error occurred while saving your reply.", "danger")

    return redirect(request.referrer or url_for("admin_support"))


@app.route("/admin_settings", methods=["GET", "POST"])
def admin_settings():
    if "admin" not in session: return redirect(url_for("login_page"))

    try:
        cur = mysql.connection.cursor()

        if request.method == "POST":
            settings_to_update = {
                'tax_rate': request.form.get('tax_rate'),
                'default_commission': request.form.get('default_commission'),
                'support_email': request.form.get('support_email'),
                'support_phone': request.form.get('support_phone'),
                'whatsapp_number': request.form.get('whatsapp_number'),
                'auto_approve_routes': '1' if request.form.get('auto_approve_routes') else '0',
                'maintenance_mode': '1' if request.form.get('maintenance_mode') else '0'
            }
            for key, value in settings_to_update.items():
                cur.execute(
                    "INSERT INTO system_settings (setting_key, setting_value) VALUES (%s, %s) ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value)",
                    (key, value))
            mysql.connection.commit()
            flash("System settings updated successfully.", "success")
            cur.close()
            return redirect(url_for("admin_settings"))

        cur.execute("SELECT setting_key, setting_value FROM system_settings")
        settings = dict(cur.fetchall())
        cur.close()
    except MySQLdb.Error as e:
        print(f"Admin Settings Error: {e}")
        settings = {}

    return render_template("admin/admin_settings.html", admin_name=get_admin_name(), settings=settings)