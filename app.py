
import os
import calendar as cal
from datetime import datetime, date, time, timedelta
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash

# firebase_admin is imported lazily in _init_firebase() to avoid adding
# 2-5 seconds to every cold start on Vercel serverless. The SDK pulls in
# grpc, google-auth, and cryptography — all expensive at import time.
_firebase_admin   = None
_firebase_creds   = None
_firebase_messaging = None

# Only import APScheduler when not running on Vercel (serverless)
_IS_VERCEL = os.environ.get("VERCEL") == "1"
if not _IS_VERCEL:
    from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)

# ── Database URI ────────────────────────────────────────────────────────────
# Independent database for this app. On Render, BOOKING_DATABASE_URL should
# be injected when a PostgreSQL instance is attached. Render still uses the
# legacy "postgres://" prefix; SQLAlchemy requires "postgresql://".
_db_url = os.environ.get("BOOKING_DATABASE_URL", os.environ.get("DATABASE_URL", "sqlite:///booking.db"))
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = _db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me-booking")

db = SQLAlchemy(app)


def ensure_schema():
    """Create all tables and seed the default admin. Safe to call repeatedly."""
    db.create_all()
    if not User.query.filter_by(role=ROLE_ADMIN).first():
        admin = User(username="admin", role=ROLE_ADMIN)
        admin.set_password("admin123")
        db.session.add(admin)
        db.session.commit()
        print("Default admin created — username: admin  password: admin123")


_schema_ready = False

@app.before_request
def init_db():
    """Run once on the first request — works on both gunicorn and Vercel serverless."""
    global _schema_ready
    if not _schema_ready:
        ensure_schema()
        _schema_ready = True

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to continue."

# ---------- Firebase Admin setup (used for booking reminder pushes) ----------
FIREBASE_CRED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "firebase-service-account.json")
FIREBASE_ENABLED = os.path.exists(FIREBASE_CRED_PATH)

if not FIREBASE_ENABLED:
    print(
        "WARNING: firebase-service-account.json not found. "
        "Push notifications are disabled."
    )

def _init_firebase():
    """Lazily import and initialise firebase-admin on first use.
    Keeps it out of the module-level import path so Vercel cold starts
    don't pay the 2-5 s SDK initialisation cost on every request."""
    global _firebase_admin, _firebase_messaging
    if _firebase_admin is not None:
        return True  # already initialised
    if not FIREBASE_ENABLED:
        return False
    try:
        import firebase_admin as _fb
        from firebase_admin import credentials as _creds, messaging as _msg
        if not _fb._apps:
            cred = _creds.Certificate(FIREBASE_CRED_PATH)
            _fb.initialize_app(cred)
        _firebase_admin = _fb
        _firebase_messaging = _msg
        return True
    except Exception as exc:
        print("Firebase init error:", exc)
        return False

# ---------- roles ----------
ROLE_ADMIN  = "admin"
ROLE_STAFF  = "staff"
ROLE_CLIENT = "client"
ALL_ROLES   = [ROLE_ADMIN, ROLE_STAFF, ROLE_CLIENT]


# ---------- models ----------

class User(UserMixin, db.Model):
    __tablename__ = "users"

    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role          = db.Column(db.String(20), nullable=False, default=ROLE_CLIENT)
    created_at    = db.Column(db.DateTime, default=db.func.now())

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

    @property
    def is_admin(self):
        return self.role == ROLE_ADMIN

    @property
    def is_staff(self):
        return self.role == ROLE_STAFF

    @property
    def is_client(self):
        return self.role == ROLE_CLIENT

    @property
    def can_manage_futsals(self):
        """Admin and staff may add/edit/delete futsal courts."""
        return self.role in (ROLE_ADMIN, ROLE_STAFF)

    @property
    def can_manage_bookings(self):
        """All roles may add/edit/delete bookings."""
        return True


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


class Futsal(db.Model):
    __tablename__ = "futsals"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, default="")
    location    = db.Column(db.String(200), default="")
    created_at  = db.Column(db.DateTime, default=db.func.now())

    events = db.relationship("Event", backref="futsal", lazy=True)


PRICE_PER_PLAYER_PER_HOUR = 100  # Rs. per player per hour

PAYMENT_PENDING   = "pending"
PAYMENT_PARTIAL   = "partial"
PAYMENT_CONFIRMED = "confirmed"


class Event(db.Model):
    __tablename__ = "events"

    id             = db.Column(db.Integer, primary_key=True)
    futsal_id      = db.Column(db.Integer, db.ForeignKey("futsals.id"), nullable=False)
    user_id        = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    name           = db.Column(db.String(100), nullable=False)
    phone_number   = db.Column(db.String(20), nullable=True)
    description    = db.Column(db.Text)
    event_date     = db.Column(db.Date, nullable=False)
    start_time     = db.Column(db.Time, nullable=False)
    end_time       = db.Column(db.Time, nullable=False)
    created_at     = db.Column(db.DateTime, default=db.func.now())
    reminder_sent  = db.Column(db.Boolean, default=False, nullable=False)
    # --- payment fields ---
    num_players    = db.Column(db.Integer, default=1, nullable=False)
    amount_due     = db.Column(db.Float,   default=0.0, nullable=False)
    amount_paid    = db.Column(db.Float,   default=0.0, nullable=False)
    payment_status = db.Column(db.String(20), default=PAYMENT_PENDING, nullable=False)

    owner = db.relationship("User", foreign_keys=[user_id])

    @property
    def owner_name(self):
        return self.owner.username if self.owner else "Unclaimed"

    @property
    def minimum_due(self):
        """Minimum amount required to confirm the booking (half of total)."""
        return self.amount_due / 2

    @property
    def balance_due(self):
        """Remaining unpaid balance."""
        return max(0.0, self.amount_due - self.amount_paid)

    @property
    def is_confirmed(self):
        return self.payment_status == PAYMENT_CONFIRMED

    @property
    def is_pending(self):
        return self.payment_status == PAYMENT_PENDING


class DeviceToken(db.Model):
    __tablename__ = "device_tokens"

    id         = db.Column(db.Integer, primary_key=True)
    token      = db.Column(db.String(255), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=db.func.now())


# ---------- role decorators ----------

def admin_required(f):
    """Restrict a route to admin users only."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            flash("Admin access required.", "error")
            return abort(403)
        return f(*args, **kwargs)
    return decorated


def staff_or_admin_required(f):
    """Restrict a route to staff or admin users."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.can_manage_futsals:
            flash("You don't have permission to do that.", "error")
            return abort(403)
        return f(*args, **kwargs)
    return decorated


def staff_required(f):
    """Restrict a route to staff users specifically (their own dashboard)."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_staff:
            return abort(403)
        return f(*args, **kwargs)
    return decorated


def client_required(f):
    """Restrict a route to client users specifically (their own dashboard)."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_client:
            return abort(403)
        return f(*args, **kwargs)
    return decorated


def can_edit_event(event):
    """Staff/admin can edit any booking; clients can only edit their own."""
    return current_user.can_manage_futsals or event.user_id == current_user.id


def is_upcoming(event):
    """True if the booking's start time is still in the future.
    Editing is only allowed for upcoming bookings — once a booking has
    started, its details are locked (deleting is still allowed, since
    cancelling/cleaning up a record is a different action than changing it)."""
    return datetime.combine(event.event_date, event.start_time) > datetime.now()


# ---------- helpers ----------

def parse_date(s, default=None):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return default or date.today()


def parse_time(s):
    return datetime.strptime(s, "%H:%M").time()


# ---------- feature switch: single-court vs multi-court mode ----------
# True  -> full experience: staff/client see a court list/dashboard, can manage
#          multiple courts, etc.
# False -> single-court mode: staff and clients skip straight to the one
#          court's calendar (the first Futsal row, by id) and the dashboard
#          list is disabled everywhere, no matter how many Futsal rows exist
#          in the database. Admin is unaffected either way — they always land
#          on /admin, since role assignment isn't tied to court count.
MULTI_FUTSAL_MODE = False 


# ---------- booking hours & slot options ----------

OPEN_TIME  = time(6, 0)
CLOSE_TIME = time(22, 0)
SLOT_STEP_MINUTES    = 30
MIN_DURATION_MINUTES = 60
MAX_DURATION_MINUTES = 240


def generate_start_slots():
    slots = []
    t = datetime.combine(date.today(), OPEN_TIME)
    last_start = datetime.combine(date.today(), CLOSE_TIME) - timedelta(minutes=MIN_DURATION_MINUTES)
    while t <= last_start:
        label = t.strftime("%H:%M")
        slots.append((label, label))
        t += timedelta(minutes=SLOT_STEP_MINUTES)
    return slots


def generate_duration_options():
    options = []
    m = MIN_DURATION_MINUTES
    while m <= MAX_DURATION_MINUTES:
        hours, mins = divmod(m, 60)
        label = f"{hours}h" if mins == 0 else f"{hours}h {mins}m"
        options.append((m, label))
        m += SLOT_STEP_MINUTES
    return options


START_SLOTS      = generate_start_slots()
DURATION_OPTIONS = generate_duration_options()


def month_grid(year, month):
    """Weeks of date objects (Sunday-first). Days outside the month are None."""
    sunday_first = cal.Calendar(firstweekday=6)  # 6 = Sunday
    weeks = sunday_first.monthdatescalendar(year, month)
    grid  = []
    for week in weeks:
        # monthdatescalendar returns actual date objects; mark other-month days as None
        grid.append([d if d.month == month else None for d in week])
    return grid


def has_conflict(event_date, start_time, end_time, futsal_id, exclude_id=None):
    """Return True if the slot overlaps an existing booking on this court.

    NOTE: this app has no visibility into the separate Training App's
    database, so it cannot detect a conflict with a training session running
    on the same court at the same time. If that cross-checking is ever
    needed again, the two apps would need to share a database or expose an
    API to each other.
    """
    query = Event.query.filter(
        Event.futsal_id  == futsal_id,
        Event.event_date == event_date,
        Event.start_time <  end_time,
        Event.end_time   >  start_time,
    )
    if exclude_id is not None:
        query = query.filter(Event.id != exclude_id)
    return query.first() is not None


# ---------- push notifications ----------

def send_push_to_all(title, body):
    if not _init_firebase():
        print(f"[push skipped] {title}: {body}")
        return

    tokens = [t.token for t in DeviceToken.query.all()]
    if not tokens:
        return

    msg_mod = _firebase_messaging
    messages = [
        msg_mod.Message(
            notification=msg_mod.Notification(title=title, body=body),
            token=tok,
        )
        for tok in tokens
    ]

    try:
        response = msg_mod.send_each(messages)
    except Exception as exc:
        print("Push send error:", exc)
        return

    for tok, result in zip(tokens, response.responses):
        if not result.success:
            DeviceToken.query.filter_by(token=tok).delete()
    db.session.commit()


def check_reminders():
    with app.app_context():
        now          = datetime.now()
        window_start = now + timedelta(minutes=28)
        window_end   = now + timedelta(minutes=32)

        candidates = Event.query.filter(
            Event.reminder_sent.is_(False),
            Event.event_date >= now.date(),
            Event.event_date <= window_end.date(),
        ).all()

        for event in candidates:
            event_start = datetime.combine(event.event_date, event.start_time)
            if window_start <= event_start <= window_end:
                send_push_to_all(
                    "Upcoming booking",
                    f"{event.name} starts at {event.start_time.strftime('%H:%M')}",
                )
                event.reminder_sent = True

        db.session.commit()


@app.context_processor
def inject_dashboard_endpoint():
    def dashboard_endpoint():
        if not current_user.is_authenticated:
            return "login"
        if current_user.is_admin:
            return "admin_panel"
        # In single-court mode staff/client have no dashboard — send them home
        # (home immediately redirects to the single court's calendar).
        # In multi-court mode send them to their role-specific panel.
        if not MULTI_FUTSAL_MODE:
            return "home"
        if current_user.is_staff:
            return "staff_panel"
        return "client_panel"
    return {"dashboard_endpoint": dashboard_endpoint, "multi_futsal_mode": MULTI_FUTSAL_MODE}


# ---------- error handlers ----------

@app.errorhandler(403)
def forbidden(e):
    return render_template("403.html"), 403


# ---------- auth ----------

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("home"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm_password", "")

        if not username or not password:
            flash("Username and password are required.", "error")
            return render_template("signup.html")

        if password != confirm:
            flash("Passwords don't match.", "error")
            return render_template("signup.html")

        if User.query.filter_by(username=username).first():
            flash("That username is already taken.", "error")
            return render_template("signup.html")

        # Self-registration always creates a client account
        user = User(username=username, role=ROLE_CLIENT)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        login_user(user)
        flash("Account created — welcome!")
        return redirect(url_for("home"))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("home"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            next_url = request.args.get("next")
            return redirect(next_url or url_for("home"))

        flash("Invalid username or password.", "error")

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("Logged out.")
    return redirect(url_for("login"))


# ---------- admin panel ----------

@app.route("/admin")
@admin_required
def admin_panel():
    users = User.query.order_by(User.username).all()
    futsals = Futsal.query.order_by(Futsal.name).all()
    today = date.today()
    return render_template(
        "admin.html", users=users, all_roles=ALL_ROLES, futsals=futsals,
        now_year=today.year, now_month=today.month,
    )


@app.route("/staff")
@staff_required
def staff_panel():
    if not MULTI_FUTSAL_MODE:
        return redirect(url_for("home"))
    futsals = Futsal.query.order_by(Futsal.name).all()
    today = date.today()
    return render_template("staff.html", futsals=futsals, now_year=today.year, now_month=today.month)


@app.route("/client")
@client_required
def client_panel():
    if not MULTI_FUTSAL_MODE:
        return redirect(url_for("home"))
    futsals = Futsal.query.order_by(Futsal.name).all()
    today = date.today()
    return render_template("client.html", futsals=futsals, now_year=today.year, now_month=today.month)


@app.route("/admin/user/add", methods=["POST"])
@admin_required
def admin_add_user():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    role     = request.form.get("role", ROLE_CLIENT)

    if role not in ALL_ROLES:
        flash("Invalid role.")
        return redirect(url_for("admin_panel"))

    if not username or not password:
        flash("Username and password are required.")
        return redirect(url_for("admin_panel"))

    if User.query.filter_by(username=username).first():
        flash(f'Username "{username}" is already taken.')
        return redirect(url_for("admin_panel"))

    user = User(username=username, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    flash(f'User "{username}" ({role}) created.')
    return redirect(url_for("admin_panel"))


@app.route("/admin/user/<int:user_id>/role", methods=["POST"])
@admin_required
def admin_change_role(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You cannot change your own role.")
        return redirect(url_for("admin_panel"))

    new_role = request.form.get("role", "")
    if new_role not in ALL_ROLES:
        flash("Invalid role.")
        return redirect(url_for("admin_panel"))

    user.role = new_role
    db.session.commit()
    flash(f'"{user.username}" is now {new_role}.')
    return redirect(url_for("admin_panel"))


@app.route("/admin/user/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You cannot delete yourself.")
        return redirect(url_for("admin_panel"))
    db.session.delete(user)
    db.session.commit()
    flash(f'User "{user.username}" deleted.')
    return redirect(url_for("admin_panel"))


# ---------- misc routes ----------

@app.route("/register-token", methods=["POST"])
@login_required
def register_token():
    data  = request.get_json(silent=True) or {}
    token = data.get("token")
    if not token:
        return {"status": "error", "message": "missing token"}, 400
    if not DeviceToken.query.filter_by(token=token).first():
        db.session.add(DeviceToken(token=token))
        db.session.commit()
    return {"status": "ok"}


@app.route("/firebase-messaging-sw.js")
def firebase_sw():
    return send_from_directory(app.static_folder, "firebase-messaging-sw.js")


# ---------- home / role dispatch ----------

@app.route("/")
@login_required
def home():
    # Admin always needs their own landing page (role assignment isn't tied to
    # how many courts exist), so the switch only applies below.
    if current_user.is_admin:
        return redirect(url_for("admin_panel"))

    if not MULTI_FUTSAL_MODE:
        only = Futsal.query.order_by(Futsal.id).first()
        if not only:
            # No court has been created yet. Render a plain message instead of
            # redirecting anywhere — redirecting to /login here would bounce
            # right back to / for an already-authenticated user (infinite loop).
            return render_template("no_court.html")
        today = date.today()
        return redirect(url_for("calendar_view", futsal_id=only.id, year=today.year, month=today.month))

    if current_user.is_staff:
        return redirect(url_for("staff_panel"))
    return redirect(url_for("client_panel"))


# ---------- futsal management (admin + staff only) ----------

@app.route("/futsal/add", methods=["POST"])
@staff_or_admin_required
def add_futsal():
    if not MULTI_FUTSAL_MODE and Futsal.query.count() >= 1:
        flash("Single-court mode is on — only one court is allowed.")
        return redirect(url_for("home"))

    name     = request.form.get("name", "").strip()
    desc     = request.form.get("description", "").strip()
    location = request.form.get("location", "").strip()
    if not name:
        flash("Futsal name is required.")
        return redirect(url_for("home"))
    db.session.add(Futsal(name=name, description=desc, location=location))
    db.session.commit()
    flash(f'"{name}" added.')
    return redirect(url_for("home"))


def _safe_next(next_value):
    """Only allow redirecting to a relative in-app path, never an external URL."""
    if next_value and next_value.startswith("/") and not next_value.startswith("//"):
        return next_value
    return None


@app.route("/futsal/<int:futsal_id>/edit", methods=["POST"])
@staff_or_admin_required
def edit_futsal(futsal_id):
    futsal          = Futsal.query.get_or_404(futsal_id)
    futsal.name     = request.form.get("name", futsal.name).strip()
    futsal.description = request.form.get("description", futsal.description).strip()
    futsal.location = request.form.get("location", futsal.location).strip()
    db.session.commit()
    flash(f'"{futsal.name}" updated.')
    return redirect(_safe_next(request.form.get("next")) or url_for("home"))


@app.route("/futsal/<int:futsal_id>/delete", methods=["POST"])
@admin_required
def delete_futsal(futsal_id):
    futsal = Futsal.query.get_or_404(futsal_id)
    # Delete all events first to satisfy FK constraint
    Event.query.filter_by(futsal_id=futsal_id).delete()
    db.session.delete(futsal)
    db.session.commit()
    flash(f'"{futsal.name}" and all its bookings deleted.')
    return redirect(url_for("home"))


# ---------- calendar view (all roles) ----------

@app.route("/futsal/<int:futsal_id>/calendar/<int:year>/<int:month>")
@login_required
def calendar_view(futsal_id, year, month):
    futsal = Futsal.query.get_or_404(futsal_id)

    if month < 1:
        year, month = year - 1, 12
    elif month > 12:
        year, month = year + 1, 1

    first_day = date(year, month, 1)
    last_day  = date(year, month, cal.monthrange(year, month)[1])

    events = (
        Event.query
        .filter(
            Event.futsal_id  == futsal_id,
            Event.event_date >= first_day,
            Event.event_date <= last_day,
        )
        .order_by(Event.event_date, Event.start_time)
        .all()
    )
    events_by_day = {}
    for e in events:
        events_by_day.setdefault(e.event_date.isoformat(), []).append(e)

    selected_str = request.args.get("day")
    selected     = parse_date(selected_str, default=None) if selected_str else None
    day_events   = []
    if selected:
        day_events = sorted(
            events_by_day.get(selected.isoformat(), []), key=lambda e: e.start_time
        )
        for e in day_events:
            start_dt = datetime.combine(e.event_date, e.start_time)
            end_dt   = datetime.combine(e.event_date, e.end_time)
            e.duration_minutes = int((end_dt - start_dt).total_seconds() // 60)
            e.is_upcoming = is_upcoming(e)

    prev_month = month - 1 or 12
    prev_year  = year - 1 if month == 1 else year
    next_month = month + 1 if month < 12 else 1
    next_year  = year + 1 if month == 12 else year

    return render_template(
        "calendar.html",
        futsal=futsal,
        year=year,
        month=month,
        month_name=cal.month_name[month],
        grid=month_grid(year, month),
        today=date.today(),
        now=datetime.now(),
        events_by_day=events_by_day,
        selected=selected,
        day_events=day_events,
        prev_year=prev_year,
        prev_month=prev_month,
        next_year=next_year,
        next_month=next_month,
        start_slots=START_SLOTS,
        duration_options=DURATION_OPTIONS,
        price_per_player_per_hour=PRICE_PER_PLAYER_PER_HOUR,
    )


def compute_booking_times(event_date, form):
    start_time = parse_time(form["start_time"])
    duration   = int(form["duration"])
    start_dt   = datetime.combine(event_date, start_time)
    end_dt     = start_dt + timedelta(minutes=duration)

    if start_dt < datetime.now():
        raise ValueError("That time has already passed — pick a time in the future.")

    if start_time < OPEN_TIME or end_dt.time() > CLOSE_TIME or end_dt.date() != event_date:
        raise ValueError(
            f"Bookings must fall between {OPEN_TIME.strftime('%H:%M')} and "
            f"{CLOSE_TIME.strftime('%H:%M')}."
        )
    return start_time, end_dt.time()


# ---------- booking management (all roles) ----------

@app.route("/futsal/<int:futsal_id>/event/add", methods=["POST"])
@login_required
def add_event(futsal_id):
    Futsal.query.get_or_404(futsal_id)
    event_date = parse_date(request.form["event_date"])

    try:
        start_time, end_time = compute_booking_times(event_date, request.form)
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("calendar_view", futsal_id=futsal_id,
                                year=event_date.year, month=event_date.month,
                                day=event_date.isoformat()))
    except KeyError:
        flash("Please choose a valid start time and duration.")
        return redirect(url_for("calendar_view", futsal_id=futsal_id,
                                year=event_date.year, month=event_date.month,
                                day=event_date.isoformat()))

    if has_conflict(event_date, start_time, end_time, futsal_id):
        flash("This time slot conflicts with an existing booking.", "error")
        return redirect(url_for("calendar_view", futsal_id=futsal_id,
                                year=event_date.year, month=event_date.month,
                                day=event_date.isoformat()))

    # Prevent booking times where the start time has already passed
    now = datetime.now()
    start_datetime = datetime.combine(event_date, start_time)
    if start_datetime < now:
        flash("Cannot book a time slot that has already started.", "error")
        return redirect(url_for("calendar_view", futsal_id=futsal_id,
                                year=event_date.year, month=event_date.month,
                                day=event_date.isoformat()))

    try:
        num_players = max(1, int(request.form.get("num_players", 1)))
    except (ValueError, TypeError):
        num_players = 1

    # Duration in hours (start_time and end_time already validated above)
    start_dt   = datetime.combine(event_date, start_time)
    end_dt     = datetime.combine(event_date, end_time)
    hours      = (end_dt - start_dt).total_seconds() / 3600

    amount_due = num_players * PRICE_PER_PLAYER_PER_HOUR * hours
    phone_number = request.form.get("phone_number", "").strip()

    # Validate phone number: exactly 10 digits if provided
    if phone_number and (not phone_number.isdigit() or len(phone_number) != 10):
        flash("Phone number must be exactly 10 digits.")
        return redirect(url_for("calendar_view", futsal_id=futsal_id,
                                year=event_date.year, month=event_date.month,
                                day=event_date.isoformat()))

    event = Event(
        futsal_id=futsal_id,
        user_id=current_user.id,
        name=request.form["name"].strip(),
        phone_number=phone_number,
        description=request.form.get("description", "").strip(),
        event_date=event_date,
        start_time=start_time,
        end_time=end_time,
        num_players=num_players,
        amount_due=amount_due,
        amount_paid=0.0,
        payment_status=PAYMENT_PENDING,
    )
    db.session.add(event)
    db.session.commit()

    # Redirect to payment page — booking is not confirmed until payment is made
    return redirect(url_for("payment_page", futsal_id=futsal_id, event_id=event.id))


# ---------- payment routes ----------

def _can_access_payment(event):
    """Owner, staff, and admin can access the payment page."""
    return (current_user.id == event.user_id
            or current_user.can_manage_futsals)


@app.route("/futsal/<int:futsal_id>/event/<int:event_id>/pay", methods=["GET", "POST"])
@login_required
def payment_page(futsal_id, event_id):
    futsal = Futsal.query.get_or_404(futsal_id)
    event  = Event.query.get_or_404(event_id)

    if not _can_access_payment(event):
        abort(403)

    if request.method == "POST":
        if event.is_confirmed:
            flash("This booking is already fully paid.")
            return redirect(url_for("calendar_view", futsal_id=futsal_id,
                                    year=event.event_date.year,
                                    month=event.event_date.month,
                                    day=event.event_date.isoformat()))

        pay_option = request.form.get("pay_option", "")  # "half" or "full"

        if pay_option == "full":
            pay_amount = event.balance_due
        elif pay_option == "half":
            pay_amount = min(event.minimum_due, event.balance_due)
        else:
            flash("Invalid payment option.")
            return redirect(url_for("payment_page", futsal_id=futsal_id, event_id=event_id))

        event.amount_paid += pay_amount

        if event.amount_paid >= event.amount_due:
            event.payment_status = PAYMENT_CONFIRMED
            db.session.commit()
            flash(f"Payment of Rs. {pay_amount:.0f} received. Booking confirmed!")
        elif event.amount_paid >= event.minimum_due:
            event.payment_status = PAYMENT_PARTIAL
            db.session.commit()
            flash(
                f"Partial payment of Rs. {pay_amount:.0f} received. "
                f"Booking confirmed. Rs. {event.balance_due:.0f} remaining."
            )
        else:
            # Paid less than minimum — keep pending, do not confirm
            db.session.commit()
            flash(
                f"Payment of Rs. {pay_amount:.0f} is below the minimum "
                f"Rs. {event.minimum_due:.0f} required. Please pay at least half to confirm."
            )
            return redirect(url_for("payment_page", futsal_id=futsal_id, event_id=event_id))

        return redirect(url_for("calendar_view", futsal_id=futsal_id,
                                year=event.event_date.year,
                                month=event.event_date.month,
                                day=event.event_date.isoformat()))

    return render_template(
        "payment.html",
        futsal=futsal,
        event=event,
        price_per_player=PRICE_PER_PLAYER_PER_HOUR,
    )


@app.route("/futsal/<int:futsal_id>/event/<int:event_id>/pay/confirm", methods=["POST"])
@staff_or_admin_required
def force_confirm_payment(futsal_id, event_id):
    """Staff / admin can manually mark a booking as confirmed regardless of payment."""
    Futsal.query.get_or_404(futsal_id)
    event = Event.query.get_or_404(event_id)
    event.payment_status = PAYMENT_CONFIRMED
    db.session.commit()
    flash(f'Booking "{event.name}" force-confirmed.')
    return redirect(url_for("calendar_view", futsal_id=futsal_id,
                            year=event.event_date.year,
                            month=event.event_date.month,
                            day=event.event_date.isoformat()))


@app.route("/futsal/<int:futsal_id>/event/<int:event_id>/update", methods=["POST"])
@login_required
def update_event(futsal_id, event_id):
    Futsal.query.get_or_404(futsal_id)
    event      = Event.query.get_or_404(event_id)

    if not can_edit_event(event):
        flash("You can only edit your own bookings.")
        return abort(403)

    if not is_upcoming(event):
        flash("This booking has already started and can no longer be edited.")
        return redirect(url_for("calendar_view", futsal_id=futsal_id,
                                year=event.event_date.year, month=event.event_date.month,
                                day=event.event_date.isoformat()))

    if event.amount_paid > 0:
        flash("This booking already has a payment on it and can't be edited — "
              "cancel and rebook instead if the time or duration needs to change.")
        return redirect(url_for("calendar_view", futsal_id=futsal_id,
                                year=event.event_date.year, month=event.event_date.month,
                                day=event.event_date.isoformat()))

    event_date = parse_date(request.form["event_date"])

    try:
        start_time, end_time = compute_booking_times(event_date, request.form)
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("calendar_view", futsal_id=futsal_id,
                                year=event.event_date.year, month=event.event_date.month,
                                day=event.event_date.isoformat()))
    except KeyError:
        flash("Please choose a valid start time and duration.")
        return redirect(url_for("calendar_view", futsal_id=futsal_id,
                                year=event.event_date.year, month=event.event_date.month,
                                day=event.event_date.isoformat()))

    if has_conflict(event_date, start_time, end_time, futsal_id, exclude_id=event.id):
        flash("This time slot conflicts with an existing booking.", "error")
        return redirect(url_for("calendar_view", futsal_id=futsal_id,
                                year=event.event_date.year, month=event.event_date.month,
                                day=event.event_date.isoformat()))

    # Prevent updating to a time where the start time has already passed
    now = datetime.now()
    start_datetime = datetime.combine(event_date, start_time)
    if start_datetime < now:
        flash("Cannot update to a time slot that has already started.", "error")
        return redirect(url_for("calendar_view", futsal_id=futsal_id,
                                year=event.event_date.year, month=event.event_date.month,
                                day=event.event_date.isoformat()))

    event.name        = request.form["name"].strip()
    event.description = request.form.get("description", "").strip()
    event.event_date  = event_date
    event.start_time  = start_time
    event.end_time    = end_time
    db.session.commit()
    flash("Booking updated.")
    return redirect(url_for("calendar_view", futsal_id=futsal_id,
                            year=event.event_date.year, month=event.event_date.month,
                            day=event.event_date.isoformat()))


@app.route("/futsal/<int:futsal_id>/event/<int:event_id>/delete", methods=["POST"])
@login_required
def delete_event(futsal_id, event_id):
    Futsal.query.get_or_404(futsal_id)
    event = Event.query.get_or_404(event_id)

    if not can_edit_event(event):
        flash("You can only delete your own bookings.")
        return abort(403)

    d     = event.event_date
    db.session.delete(event)
    db.session.commit()
    flash("Booking deleted.")
    return redirect(url_for("calendar_view", futsal_id=futsal_id,
                            year=d.year, month=d.month, day=d.isoformat()))


# ── Background scheduler (not used on Vercel serverless) ───────────────────
if not _IS_VERCEL:
    def _should_start_scheduler():
        """Avoids starting the scheduler twice.
        - Under gunicorn, this module is imported (not run as __main__), and
          there's no Werkzeug reloader involved — safe to start once here.
        - Under `python app.py` locally, Flask's debug reloader re-executes
          this whole module in both a parent "monitor" process and a spawned
          child that actually serves requests. Only the child sets
          WERKZEUG_RUN_MAIN, so only it should start the scheduler."""
        if __name__ != "__main__":
            return True
        return os.environ.get("WERKZEUG_RUN_MAIN") == "true"

    if _should_start_scheduler():
        try:
            _scheduler = BackgroundScheduler()
            _scheduler.add_job(check_reminders, "interval", minutes=1)
            _scheduler.start()
        except Exception as _exc:
            print("Scheduler start error:", _exc)


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))