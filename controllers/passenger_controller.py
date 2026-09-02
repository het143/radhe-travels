# noinspection PyUnresolvedReferences,DuplicatedCode
from flask import render_template, request, redirect, url_for, flash, session
from app import app
from database.db_connection import mysql
from datetime import datetime
import MySQLdb
import os
from werkzeug.utils import secure_filename


def get_passenger_name():
    if "user" in session:
        if "user_name" not in session or "user_profile_image" not in session:
            cur = mysql.connection.cursor()
            cur.execute("SELECT full_name, profile_image FROM user WHERE user_id = %s", (session["user"],))
            user_data = cur.fetchone()
            cur.close()

            if user_data:
                session["user_name"] = user_data[0]
                session["user_profile_image"] = user_data[1]

        return session.get("user_name")
    return None


@app.route("/home")
def passenger_home():
    cur = mysql.connection.cursor()
    try:
        cur.execute("""
            SELECT route_id, source_city, destination_city, distance_km 
            FROM route 
            WHERE status = 1 
            ORDER BY source_city ASC 
            LIMIT 4
        """)
        routes_data = cur.fetchall()
        routes = [{"id": r[0], "source": r[1], "destination": r[2], "distance": r[3]} for r in routes_data]

        cur.execute("""
            SELECT DISTINCT source_city FROM route WHERE status = 1
            UNION
            SELECT DISTINCT destination_city FROM route WHERE status = 1
            ORDER BY source_city ASC
        """)
        cities = [row[0] for row in cur.fetchall()]

        active_bookings = []
        if "user" in session:
            cur.execute("""
                SELECT b.booking_id, r.source_city, r.destination_city 
                FROM booking b 
                JOIN schedule s ON b.schedule_id = s.schedule_id 
                JOIN route r ON s.route_id = r.route_id 
                WHERE b.user_id = %s AND b.booking_status = 1 AND b.journey_date >= CURDATE()
            """, (session["user"],))
            active_bookings = [{"pnr": f"RBR-{row[0]:06d}", "route": f"{row[1]} to {row[2]}"} for row in cur.fetchall()]

    except Exception as e:
        print(f"Home Routes Error: {e}")
        routes = []
        cities = []
        active_bookings = []
    finally:
        cur.close()

    today = datetime.now().strftime('%Y-%m-%d')
    return render_template("passenger/index.html", user_name=get_passenger_name(), routes=routes, cities=cities,
                           today=today, active_bookings=active_bookings)


@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "user" not in session: return redirect(url_for("login_page"))
    user_id = session["user"]

    if request.method == "POST":
        full_name = request.form.get("full_name")
        mobile_no = request.form.get("mobile_no")
        gender = request.form.get("gender")
        age = request.form.get("age")

        upi_provider = request.form.get("upi_provider")
        upi_id = request.form.get("upi_id")
        name_on_card = request.form.get("name_on_card")
        card_number = request.form.get("card_number")
        card_expiry = request.form.get("card_expiry")
        card_cvv = request.form.get("card_cvv")

        if not age: age = None

        profile_img = request.files.get("profile_image")

        try:
            cur = mysql.connection.cursor()

            update_fields = [
                "full_name = %s", "mobile_no = %s", "gender = %s", "age = %s",
                "upi_provider = %s", "upi_id = %s", "name_on_card = %s",
                "card_number = %s", "card_expiry = %s", "card_cvv = %s"
            ]
            update_values = [full_name, mobile_no, gender, age, upi_provider, upi_id, name_on_card, card_number,
                             card_expiry, card_cvv]

            if profile_img and profile_img.filename != '':
                filename = secure_filename(profile_img.filename)
                unique_filename = f"user_{user_id}_{filename}"
                upload_folder = os.path.join(app.root_path, 'static', 'uploads', 'profiles')
                os.makedirs(upload_folder, exist_ok=True)
                profile_img.save(os.path.join(upload_folder, unique_filename))
                update_fields.append("profile_image = %s")
                update_values.append(unique_filename)
                session["user_profile_image"] = unique_filename

            update_values.append(user_id)

            cur.execute(f"UPDATE user SET {', '.join(update_fields)} WHERE user_id = %s", tuple(update_values))
            mysql.connection.commit()
            cur.close()

            session["user_name"] = full_name
            flash("Profile and Payment details updated successfully!", "success")
        except MySQLdb.Error as e:
            print(f"Profile Update Error: {e}")
            flash("Database error updating profile.", "danger")

        return redirect(url_for("profile"))

    try:
        cur = mysql.connection.cursor()

        cur.execute("""
            SELECT full_name, email, mobile_no, profile_image, gender, age, 
                   upi_provider, upi_id, name_on_card, card_number, card_expiry, card_cvv 
            FROM user WHERE user_id = %s
        """, (user_id,))
        user_data = cur.fetchone()
        cur.close()

        if user_data:
            return render_template("passenger/profile.html",
                                   user_name=user_data[0],
                                   user_email=user_data[1],
                                   user_mobile=user_data[2],
                                   user_profile_image=user_data[3],
                                   user_gender=user_data[4],
                                   user_age=user_data[5],
                                   upi_provider=user_data[6],
                                   upi_id=user_data[7],
                                   name_on_card=user_data[8],
                                   card_number=user_data[9],
                                   card_expiry=user_data[10],
                                   card_cvv=user_data[11],
                                   user_id=f"RBR-{user_id:06d}")
    except MySQLdb.Error as e:
        print(f"View Profile Error: {e}")

    return redirect(url_for("login_page"))


@app.route("/search_bus", methods=["GET", "POST"])
def search_bus():
    if "user" not in session:
        flash("Please log in to search for buses.", "danger")
        return redirect(url_for("login_page"))

    cur = mysql.connection.cursor()

    cur.execute("""
                SELECT DISTINCT source_city FROM route WHERE status = 1
                UNION
                SELECT DISTINCT destination_city FROM route WHERE status = 1
                ORDER BY source_city ASC
                """)
    cities = [row[0] for row in cur.fetchall()]

    cur.execute("SELECT DISTINCT operator_name FROM operator WHERE account_status = 1 ORDER BY operator_name ASC")
    operators = [row[0] for row in cur.fetchall()]

    bus_types = ["AC", "Non-AC", "Sleeper"]

    schedules = None
    search_params = {}

    source = request.form.get("source") or request.args.get("source") or request.args.get("from")
    destination = request.form.get("destination") or request.args.get("destination") or request.args.get("to")
    date = request.form.get("date") or request.args.get("date")
    operator_filter = request.form.get("operator") or request.args.get("operator")
    bus_type_filter = request.form.get("bus_type") or request.args.get("bus_type")

    if source and destination and date:
        source = source.strip().title()
        destination = destination.strip().title()

        search_params = {
            'source': source,
            'destination': destination,
            'date': date,
            'operator': operator_filter,
            'bus_type': bus_type_filter
        }

        if source == destination:
            flash("Origin and Destination cities cannot be the same. Please choose a valid route.", "warning")
        else:
            query = """
                    SELECT s.schedule_id, o.operator_name, b.bus_type, b.amenities,
                           s.departure_time, s.arrival_time, s.ticket_price,
                           b.total_seats, b.bus_id, r.source_city, r.destination_city, s.schedule_pattern
                    FROM schedule s
                             JOIN route r ON s.route_id = r.route_id
                             JOIN bus b ON s.bus_id = b.bus_id
                             JOIN operator o ON b.operator_id = o.operator_id
                    WHERE ((r.source_city = %s AND r.destination_city = %s AND (s.schedule_pattern LIKE 'Single%%' OR s.schedule_pattern LIKE 'Round%%Outbound%%'))
                       OR (r.source_city = %s AND r.destination_city = %s AND s.schedule_pattern LIKE 'Round%%Return%%'))
                      AND s.travel_date = %s AND s.status = 1
                      AND (s.travel_date > CURDATE() OR (s.travel_date = CURDATE() AND s.departure_time > CURTIME()))
                    """
            params = [source, destination, destination, source, date]

            if operator_filter:
                query += " AND o.operator_name = %s"
                params.append(operator_filter)

            if bus_type_filter:
                query += " AND b.bus_type = %s"
                params.append(bus_type_filter)

            cur.execute(query, tuple(params))
            results = cur.fetchall()

            schedules = []
            for row in results:
                dep_time = (datetime.min + row[4]).strftime("%I:%M %p") if row[4] else ""
                arr_time = (datetime.min + row[5]).strftime("%I:%M %p") if row[5] else ""

                try:
                    cur.execute("SELECT COUNT(*) FROM booking WHERE schedule_id = %s AND booking_status = 1", (row[0],))
                    booked_seats = cur.fetchone()[0]
                except Exception as e:
                    print(f"Error counting seats: {e}")
                    booked_seats = 0

                available_seats = row[7] - booked_seats
                schedules.append({
                    "schedule_id": row[0], "operator": row[1], "bus_type": row[2],
                    "amenities": row[3], "departure": dep_time, "arrival": arr_time,
                    "price": row[6], "available_seats": available_seats
                })

    today_str = datetime.now().strftime('%Y-%m-%d')
    cur.close()

    return render_template("passenger/search_bus.html", user_name=get_passenger_name(),
                           cities=cities, operators=operators, bus_types=bus_types,
                           schedules=schedules, search_params=search_params,
                           today=today_str)


@app.route("/booking", methods=["GET", "POST"])
def booking():
    if "user" not in session: return redirect(url_for("login_page"))
    if request.method == "GET": return redirect(url_for("search_bus"))

    schedule_id = request.form.get("schedule_id")
    seat_numbers = request.form.get("seat_numbers")
    total_amount = request.form.get("total_amount")

    cur = mysql.connection.cursor()
    try:
        cur.execute("SELECT full_name, email, mobile_no, age, gender FROM user WHERE user_id = %s", (session["user"],))
        u_data = cur.fetchone()

        contact = {"name": u_data[0], "email": u_data[1], "mobile": u_data[2], "age": u_data[3],
                   "gender": u_data[4]} if u_data else {}

        cur.execute("""
                    SELECT b.bus_number,
                           b.bus_type,
                           r.source_city,
                           r.destination_city,
                           s.travel_date,
                           s.departure_time,
                           s.arrival_time,
                           o.operator_name,
                           s.schedule_pattern
                    FROM schedule s
                             JOIN bus b ON s.bus_id = b.bus_id
                             JOIN route r ON s.route_id = r.route_id
                             JOIN operator o ON b.operator_id = o.operator_id
                    WHERE s.schedule_id = %s
                    """, (schedule_id,))
        data = cur.fetchone()

        pattern = data[8]
        if pattern and "Return" in pattern:
            src = data[3]
            dest = data[2]
        else:
            src = data[2]
            dest = data[3]

        journey = {
            "bus": data[0], "type": data[1], "source": src, "destination": dest,
            "date": data[4].strftime("%d %b %Y"),
            "dep": (datetime.min + data[5]).strftime("%I:%M %p") if data[5] else "",
            "arr": (datetime.min + data[6]).strftime("%I:%M %p") if data[6] else "",
            "operator": data[7]
        }
        seat_list = [s.strip() for s in seat_numbers.split(",") if s.strip()]
    except Exception as e:
        print(f"Booking Error: {e}")
        flash("Error loading passenger details.", "danger")
        return redirect(url_for("search_bus"))
    finally:
        cur.close()

    return render_template("passenger/booking.html", user_name=get_passenger_name(), schedule_id=schedule_id,
                           seat_numbers=seat_numbers, total_amount=total_amount, seat_list=seat_list, journey=journey,
                           contact=contact)


@app.route("/seat_selection", methods=["GET"])
def seat_selection():
    if "user" not in session: return redirect(url_for("login_page"))
    schedule_id = request.args.get("schedule_id")
    if not schedule_id: return redirect(url_for("search_bus"))

    cur = mysql.connection.cursor()
    try:
        cur.execute("""
                    SELECT s.schedule_id,
                           b.bus_id,
                           b.bus_number,
                           b.bus_type,
                           r.source_city,
                           r.destination_city,
                           s.travel_date,
                           s.departure_time,
                           s.ticket_price,
                           s.schedule_pattern
                    FROM schedule s
                             JOIN bus b ON s.bus_id = b.bus_id
                             JOIN route r ON s.route_id = r.route_id
                    WHERE s.schedule_id = %s
                    """, (schedule_id,))
        sched_data = cur.fetchone()

        pattern = sched_data[9]
        if pattern and "Return" in pattern:
            src = sched_data[5]
            dest = sched_data[4]
        else:
            src = sched_data[4]
            dest = sched_data[5]

        schedule = {
            "id": sched_data[0], "bus_id": sched_data[1], "bus_number": sched_data[2],
            "bus_type": sched_data[3], "source": src, "destination": dest,
            "date": sched_data[6], "time": (datetime.min + sched_data[7]).strftime("%I:%M %p") if sched_data[7] else "",
            "base_price": sched_data[8]
        }

        cur.execute("SELECT row_num, col_num, seat_type, seat_number, seat_price FROM seat WHERE bus_id = %s",
                    (schedule["bus_id"],))
        seats = cur.fetchall()

        layout_data = []
        seat_prices = {}

        for s in seats:
            price = float(s[4]) if s[4] else float(schedule["base_price"])
            seat_type = s[2]

            layout_data.append({
                "row": s[0], "col": s[1], "type": seat_type, "id": s[3],
                "price": price
            })

            if seat_type in ['seater', 'sleeper', 'ladies']:
                if price > 0:
                    seat_prices[seat_type] = price

        booked_seats = []
        cur.execute("SELECT seat_numbers FROM booking WHERE schedule_id = %s AND booking_status = 1", (schedule_id,))
        for b in cur.fetchall():
            if b[0]: booked_seats.extend([seat.strip() for seat in b[0].split(',')])

    except Exception as e:
        print(f"Seat Selection Error: {e}")
        layout_data, booked_seats, schedule, seat_prices = [], [], {}, {}
    finally:
        cur.close()

    return render_template("passenger/seat_selection.html", user_name=get_passenger_name(), schedule=schedule,
                           layout_data=layout_data,
                           booked_seats=booked_seats,
                           seat_prices=seat_prices)


@app.route("/history")
def history():
    if "user" not in session: return redirect(url_for("login_page"))
    user_id = session["user"]
    cur = mysql.connection.cursor()
    try:
        query = """
                SELECT bk.booking_id,
                       bk.booking_date,
                       bk.journey_date,
                       bk.total_amount,
                       bk.seat_numbers,
                       bk.booking_status,
                       r.source_city,
                       r.destination_city,
                       s.departure_time,
                       o.operator_name,
                       b.bus_type,
                       s.schedule_pattern
                FROM booking bk
                         JOIN schedule s ON bk.schedule_id = s.schedule_id
                         JOIN route r ON s.route_id = r.route_id
                         JOIN bus b ON s.bus_id = b.bus_id
                         JOIN operator o ON b.operator_id = o.operator_id
                WHERE bk.user_id = %s
                ORDER BY bk.journey_date DESC, bk.booking_date DESC
                """
        cur.execute(query, (user_id,))
        results = cur.fetchall()

        bookings = []
        now = datetime.now().date()
        for row in results:
            journey_date = row[2]
            dep_time = (datetime.min + row[8]).strftime("%I:%M %p") if row[8] else "TBD"

            if row[5] == 0:
                trip_status = "Failed"
            elif journey_date >= now:
                trip_status = "Upcoming"
            else:
                trip_status = "Completed"

            pattern = row[11]
            if pattern and "Return" in pattern:
                src = row[7]
                dest = row[6]
            else:
                src = row[6]
                dest = row[7]

            bookings.append({
                "id": row[0], "pnr": f"RBR-{row[0]:06d}",
                "booked_on": row[1].strftime("%d %b %Y") if row[1] else "Unknown",
                "journey_date": journey_date.strftime("%d %b %Y") if journey_date else "Unknown",
                "amount": float(row[3]), "seats": row[4], "db_status": row[5],
                "trip_status": trip_status, "source": src, "destination": dest,
                "time": dep_time, "operator": row[9], "bus_type": row[10]
            })
    except Exception as e:
        print(f"History Fetch Error: {e}")
        bookings = []
    finally:
        cur.close()

    return render_template("passenger/history.html", user_name=get_passenger_name(), bookings=bookings)


@app.route("/payment", methods=["GET", "POST"])
def payment():
    if "user" not in session: return redirect(url_for("login_page"))
    if request.method == "GET": return redirect(url_for("search_bus"))

    user_id = session["user"]

    schedule_id = request.form.get("schedule_id")
    seat_numbers = request.form.get("seat_numbers")
    base_amount = float(request.form.get("total_amount", 0))
    offer_code = request.form.get("offer_code", "").strip().upper()

    discount_amount = 0.0
    offer_id = None
    saved_payment = None

    cur = mysql.connection.cursor()
    try:
        if offer_code:
            cur.execute("""
                SELECT b.operator_id 
                FROM schedule s 
                JOIN bus b ON s.bus_id = b.bus_id 
                WHERE s.schedule_id = %s
            """, (schedule_id,))
            op_data = cur.fetchone()

            if op_data:
                operator_id = op_data[0]
                cur.execute("""
                    SELECT offer_id, discount_percentage 
                    FROM offer 
                    WHERE offer_code = %s AND operator_id = %s AND status = 1 AND valid_until >= CURDATE()
                """, (offer_code, operator_id))
                offer_data = cur.fetchone()

                if offer_data:
                    offer_id = offer_data[0]
                    discount_percentage = float(offer_data[1])
                    discount_amount = base_amount * (discount_percentage / 100.0)
                    flash(f"Promo Code '{offer_code}' applied! You saved ₹{discount_amount:,.2f}.", "success")
                else:
                    flash("Invalid or expired Promo Code for this specific bus operator.", "danger")

        discounted_base = base_amount - discount_amount
        tax = discounted_base * 0.05
        total_amount = discounted_base + tax

        cur.execute("""
                    SELECT b.bus_number, r.source_city, r.destination_city, s.travel_date, s.schedule_pattern
                    FROM schedule s
                             JOIN bus b ON s.bus_id = b.bus_id
                             JOIN route r ON s.route_id = r.route_id
                    WHERE s.schedule_id = %s
                    """, (schedule_id,))
        sched_data = cur.fetchone()

        pattern = sched_data[4]
        if pattern and "Return" in pattern:
            src = sched_data[2]
            dest = sched_data[1]
        else:
            src = sched_data[1]
            dest = sched_data[2]

        journey = {"bus": sched_data[0], "source": src, "destination": dest, "date": sched_data[3]}

        cur.execute(
            "SELECT upi_provider, upi_id, name_on_card, card_number, card_expiry, card_cvv FROM user WHERE user_id = %s",
            (user_id,))
        sp_data = cur.fetchone()

        if sp_data:
            saved_payment = {
                "upi_provider": sp_data[0],
                "upi_id": sp_data[1],
                "name_on_card": sp_data[2],
                "card_number": sp_data[3],
                "card_expiry": sp_data[4],
                "card_cvv": sp_data[5]
            }

    except Exception as e:
        print(f"Payment Route Error: {e}")
        return redirect(url_for("search_bus"))
    finally:
        cur.close()

    return render_template("passenger/payment.html", user_name=get_passenger_name(), schedule_id=schedule_id,
                           seat_numbers=seat_numbers, base_amount=base_amount, tax=tax, total_amount=total_amount,
                           journey=journey, offer_id=offer_id, discount_amount=discount_amount,
                           saved_payment=saved_payment)


@app.route("/process_payment", methods=["POST"])
def process_payment():
    if "user" not in session: return redirect(url_for("login_page"))
    user_id = session["user"]
    schedule_id = request.form.get("schedule_id")
    seat_numbers = request.form.get("seat_numbers")
    total_amount = request.form.get("total_amount")

    payment_method = request.form.get("payment_method", "UPI")

    offer_id = request.form.get("offer_id")
    if offer_id == "None" or not offer_id:
        offer_id = None
    discount_amount = float(request.form.get("discount_amount", 0))

    cur = mysql.connection.cursor()
    try:
        cur.execute("SELECT travel_date, bus_id FROM schedule WHERE schedule_id = %s", (schedule_id,))
        sched_data = cur.fetchone()
        journey_date = sched_data[0]
        bus_id = sched_data[1]

        cur.execute("""
                    INSERT INTO booking (user_id, schedule_id, journey_date, total_amount, booking_status, seat_numbers, offer_id, discount_amount)
                    VALUES (%s, %s, %s, %s, 1, %s, %s, %s)
                    """, (user_id, schedule_id, journey_date, total_amount, seat_numbers, offer_id, discount_amount))

        booking_id = cur.lastrowid

        cur.execute("""
                    INSERT INTO payment (booking_id, payment_method, amount, payment_status)
                    VALUES (%s, %s, %s, 1)
                    """, (booking_id, payment_method, total_amount))

        ticket_num = f"TKT-{booking_id:06d}"
        qr_hash = f"QR-{booking_id}-RADHE"
        cur.execute("""
                    INSERT INTO ticket (booking_id, ticket_number, qr_code, ticket_status)
                    VALUES (%s, %s, %s, 1)
                    """, (booking_id, ticket_num, qr_hash))

        seat_list = [s.strip() for s in seat_numbers.split(",") if s.strip()]
        for sn in seat_list:
            cur.execute("SELECT seat_id FROM seat WHERE bus_id = %s AND seat_number = %s", (bus_id, sn))
            seat_data = cur.fetchone()
            if seat_data:
                cur.execute("INSERT INTO booked_seat (booking_id, seat_id) VALUES (%s, %s)", (booking_id, seat_data[0]))

        mysql.connection.commit()
        flash(f"Payment Successful via {payment_method}! Your tickets are confirmed.", "success")
    except Exception as e:
        print(f"Payment DB Insert Error: {e}")
        flash("Transaction failed. Your account was not charged.", "danger")
        mysql.connection.rollback()
    finally:
        cur.close()

    return redirect(url_for("history"))


@app.route("/ticket/<int:booking_id>")
def ticket(booking_id):
    if "user" not in session: return redirect(url_for("login_page"))
    user_id = session["user"]
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
                WHERE bk.booking_id = %s
                  AND bk.user_id = %s
                """
        cur.execute(query, (booking_id, user_id))
        result = cur.fetchone()

        dep_time = (datetime.min + result[9]).strftime("%I:%M %p") if result[9] else "TBD"
        arr_time = (datetime.min + result[10]).strftime("%I:%M %p") if result[10] else "TBD"

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
            "seats": result[4], "status": "Confirmed" if result[5] == 1 else "Failed",
            "passenger_name": result[6], "passenger_email": result[7], "passenger_mobile": result[8],
            "departure_time": dep_time, "arrival_time": arr_time, "source": src,
            "destination": dest, "bus_number": result[13], "bus_type": result[14], "operator_name": result[15],
            "promo_code": result[16] if result[16] else None,
            "discount": float(result[17]) if result[17] else 0.0,
            "payment_method": result[18] if len(result) > 18 and result[18] else "N/A"
        }
    except Exception as e:
        print(f"Ticket Fetch Error: {e}")
        return redirect(url_for("passenger_home"))
    finally:
        cur.close()

    return render_template("passenger/ticket.html", user_name=get_passenger_name(), ticket=ticket_data)


@app.route("/support", methods=["GET", "POST"])
def support():
    user_name = None
    user_email = None
    user_id = session.get("user")
    user_tickets = []

    cur = mysql.connection.cursor()

    cur.execute(
        "SELECT setting_key, setting_value FROM system_settings WHERE setting_key IN ('support_email', 'support_phone', 'whatsapp_number')")
    settings_data = dict(cur.fetchall())

    sys_email = settings_data.get('support_email', 'support@radhetravels.com')
    sys_phone = settings_data.get('support_phone', '+91 1800-RADHE-00')
    sys_whatsapp = settings_data.get('whatsapp_number', '+91 98765 43210')

    if user_id:
        user_name = get_passenger_name()

        cur.execute("SELECT email FROM user WHERE user_id = %s", (user_id,))
        user_data = cur.fetchone()
        if user_data:
            user_email = user_data[0]

        cur.execute("""
            SELECT ticket_id, pnr, message, admin_reply, status, created_at 
            FROM support_ticket 
            WHERE user_id = %s 
            ORDER BY created_at DESC
        """, (user_id,))
        tickets_data = cur.fetchall()

        for t in tickets_data:
            user_tickets.append({
                "ticket_id": t[0],
                "pnr": t[1],
                "message": t[2],
                "admin_reply": t[3],
                "status": t[4],
                "created_at": t[5].strftime("%d %b %Y, %I:%M %p") if t[5] else "N/A"
            })

    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        pnr = request.form.get("pnr", "N/A")
        message = request.form.get("message")

        try:
            cur.execute("""
                INSERT INTO support_ticket (user_id, name, email, pnr, message, status)
                VALUES (%s, %s, %s, %s, %s, 0)
            """, (user_id, name, email, pnr, message))
            mysql.connection.commit()
            flash(f"Thank you, {name}. Your support request has been sent securely to our Admin team.", "success")
        except Exception as e:
            print(f"Support Ticket Error: {e}")
            flash("An error occurred while submitting your ticket.", "danger")

        cur.close()
        return redirect(url_for("support"))

    cur.close()

    return render_template("passenger/support.html",
                           user_name=user_name,
                           user_email=user_email,
                           user_tickets=user_tickets,
                           sys_email=sys_email,
                           sys_phone=sys_phone,
                           sys_whatsapp=sys_whatsapp)


@app.route("/passenger_emergency", methods=["POST"])
def passenger_emergency():
    if "user" not in session:
        flash("Please log in to use the SOS feature.", "danger")
        return redirect(url_for("login_page"))

    user_id = session["user"]
    pnr = request.form.get("pnr", "N/A")
    issue_type = request.form.get("issue_type", "Emergency")
    message = request.form.get("message", "")

    full_message = f"[EMERGENCY SOS] {issue_type} - {message}"

    cur = mysql.connection.cursor()
    try:
        cur.execute("SELECT full_name, email FROM user WHERE user_id = %s", (user_id,))
        user_data = cur.fetchone()
        name = user_data[0] if user_data else "Unknown Passenger"
        email = user_data[1] if user_data else "N/A"

        cur.execute("""
            INSERT INTO support_ticket (user_id, name, email, pnr, message, status)
            VALUES (%s, %s, %s, %s, %s, 0)
        """, (user_id, name, email, pnr, full_message))
        mysql.connection.commit()

        # --- NEW WEBSOCKET BROADCAST ---
        from app import socketio
        socketio.emit('emergency_trigger', {
            'title': '🚨 Passenger SOS',
            'message': f'{name} (Ticket: {pnr}) just triggered a {issue_type} alert!'
        })

        flash("SOS Alert sent! Our emergency response team has been notified and will act immediately.", "success")
    except Exception as e:
        print(f"SOS Error: {e}")
        flash("An error occurred while sending your SOS alert.", "danger")
    finally:
        cur.close()

    return redirect(url_for("passenger_home"))

@app.route("/feedback", methods=["GET", "POST"])
def feedback():
    if "user" not in session: return redirect(url_for("login_page"))
    user_id = session["user"]
    cur = mysql.connection.cursor()

    if request.method == "POST":
        booking_id = request.form.get("booking_id")
        rating = request.form.get("rating")
        comments = request.form.get("comments")
        try:
            cur.execute("INSERT INTO feedback (user_id, booking_id, rating, comments) VALUES (%s, %s, %s, %s)",
                        (user_id, booking_id, rating, comments))
            mysql.connection.commit()
            flash("Thank you! Your feedback helps us maintain the Royal Standard.", "success")
            return redirect(url_for("passenger_home"))
        except Exception as e:
            print(f"Feedback Submission Error: {e}")
            flash("An error occurred.", "danger")
        finally:
            cur.close()
            return redirect(url_for("feedback"))

    try:
        cur.execute("""
                    SELECT b.booking_id, r.source_city, r.destination_city, s.travel_date, s.schedule_pattern
                    FROM booking b
                             JOIN schedule s ON b.schedule_id = s.schedule_id
                             JOIN route r ON s.route_id = r.route_id
                    WHERE b.user_id = %s
                      AND b.booking_status = 1
                    ORDER BY s.travel_date DESC
                    """, (user_id,))
        past_bookings = cur.fetchall()

        bookings_list = []
        for row in past_bookings:
            pattern = row[4]
            if pattern and "Return" in pattern:
                src = row[2]
                dest = row[1]
            else:
                src = row[1]
                dest = row[2]

            bookings_list.append({
                "id": row[0], "pnr": f"RBR-{row[0]:06d}", "route": f"{src} ⇄ {dest}",
                "date": row[3].strftime("%d %b %Y") if row[3] else "Unknown"
            })
    except Exception as e:
        print(f"Feedback List Error: {e}")
        bookings_list = []
    finally:
        cur.close()

    return render_template("passenger/feedback.html", user_name=get_passenger_name(), bookings=bookings_list)


@app.route("/search_routes")
def search_routes():
    cur = mysql.connection.cursor()
    try:
        cur.execute(
            "SELECT route_id, source_city, destination_city, distance_km FROM route WHERE status = 1 ORDER BY source_city ASC, destination_city ASC")
        routes_data = cur.fetchall()
        routes = [{"id": r[0], "source": r[1], "destination": r[2], "distance": r[3]} for r in routes_data]
    except MySQLdb.Error as e:
        print(f"Search Routes Error: {e}")
        routes = []
    finally:
        cur.close()
    today = datetime.now().strftime('%Y-%m-%d')
    return render_template("passenger/search_routes.html", user_name=get_passenger_name(), routes=routes, today=today)


@app.route("/offers")
def offers():
    cur = mysql.connection.cursor()
    try:
        query = """
                SELECT o.offer_title, o.offer_code, o.discount_percentage, o.valid_until, op.operator_name
                FROM offer o
                         JOIN operator op ON o.operator_id = op.operator_id
                WHERE o.status = 1
                  AND o.valid_until >= CURDATE()
                ORDER BY o.valid_until ASC
                """
        cur.execute(query)
        results = cur.fetchall()
        offers_list = [
            {"title": row[0], "code": row[1], "discount": float(row[2]), "valid_until": row[3].strftime('%d %b %Y'),
             "operator": row[4]} for row in results]
    except Exception as e:
        print(f"Offers Load Error: {e}")
        offers_list = []
    finally:
        cur.close()
    return render_template("passenger/offers.html", user_name=get_passenger_name(), offers=offers_list)


@app.route('/user_refunds')
def user_refunds():
    if 'user' not in session:
        return redirect(url_for('login_page'))

    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    try:
        cur.execute("""
            SELECT c.cancellation_id, c.cancel_date, c.refund_amount, c.refund_status, c.cancel_reason,
                   b.booking_id, r.source_city, r.destination_city, s.travel_date, op.operator_name, s.schedule_pattern
            FROM cancellation c
            JOIN booking b ON c.booking_id = b.booking_id
            JOIN schedule s ON b.schedule_id = s.schedule_id
            JOIN route r ON s.route_id = r.route_id
            JOIN bus bs ON s.bus_id = bs.bus_id
            JOIN operator op ON bs.operator_id = op.operator_id
            WHERE b.user_id = %s
            ORDER BY c.cancel_date DESC
        """, (session['user'],))

        refunds = cur.fetchall()

        for r in refunds:
            pattern = r['schedule_pattern']
            if pattern and "Return" in pattern:
                r['source_city'], r['destination_city'] = r['destination_city'], r['source_city']

            r['cancel_date_str'] = r['cancel_date'].strftime('%d %b %Y') if r['cancel_date'] else "N/A"
            r['travel_date_str'] = r['travel_date'].strftime('%d %b %Y') if r['travel_date'] else "N/A"

    except Exception as e:
        print(f"Error fetching refunds: {e}")
        refunds = []
    finally:
        cur.close()

    return render_template('passenger/refunds.html', user_name=get_passenger_name(), refunds=refunds)


@app.route("/cancel_ticket", methods=["POST"])
def cancel_ticket():
    if "user" not in session: return redirect(url_for("login_page"))

    user_id = session["user"]
    booking_id = request.form.get("booking_id")
    cancel_reason = request.form.get("cancel_reason", "Change of plans")

    cur = mysql.connection.cursor()
    try:
        cur.execute("""
            SELECT b.total_amount, p.payment_method 
            FROM booking b
            JOIN payment p ON b.booking_id = p.booking_id
            WHERE b.booking_id = %s AND b.user_id = %s AND b.booking_status = 1
        """, (booking_id, user_id))
        booking_data = cur.fetchone()

        if booking_data:
            total_paid = float(booking_data[0])
            payment_method = booking_data[1]

            if payment_method == 'Cash':
                refund_amount = 0.00
                cur.execute("""
                    INSERT INTO cancellation (booking_id, refund_amount, refund_status, cancel_reason)
                    VALUES (%s, %s, 1, %s)
                """, (booking_id, refund_amount, cancel_reason))

                flash("Ticket cancelled successfully. As per policy, Cash bookings are strictly non-refundable.",
                      "warning")
            else:
                refund_amount = total_paid * 0.90
                cur.execute("""
                    INSERT INTO cancellation (booking_id, refund_amount, refund_status, cancel_reason)
                    VALUES (%s, %s, 0, %s)
                """, (booking_id, refund_amount, cancel_reason))

                flash(f"Ticket cancelled successfully. ₹{refund_amount:,.2f} is being processed for refund.", "success")

            cur.execute("UPDATE booking SET booking_status = 0 WHERE booking_id = %s", (booking_id,))
            mysql.connection.commit()
        else:
            flash("Invalid booking or ticket is already cancelled.", "danger")

    except Exception as e:
        print(f"Cancellation Error: {e}")
        flash("An error occurred while cancelling your ticket.", "danger")
    finally:
        cur.close()

    return redirect(url_for("user_refunds"))


@app.route("/about")
def about(): return render_template("passenger/about.html", user_name=get_passenger_name())


@app.route("/terms")
def terms(): return render_template("passenger/terms.html", user_name=get_passenger_name())


@app.route("/privacy")
def privacy():
    return render_template("passenger/privacy.html", user_name=get_passenger_name())


@app.route("/index")
def index(): return redirect(url_for("passenger_home"))


@app.route("/gujarat_routes")
def gujarat_routes(): return render_template("passenger/gujarat_routes.html", user_name=get_passenger_name())