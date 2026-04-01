from __future__ import annotations

import os
from datetime import datetime
from flask import Flask, flash, redirect, render_template, request, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-me-in-production")
database_url = os.getenv("DATABASE_URL", "sqlite:///ashmouni_prod.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def set_password(self, raw_password: str) -> None:
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)


class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False, index=True)
    color = db.Column(db.String(80))
    meters_per_top = db.Column(db.Float, nullable=False, default=20.0)
    full_tops = db.Column(db.Integer, nullable=False, default=0)
    open_meters = db.Column(db.Float, nullable=False, default=0.0)
    low_meter_threshold = db.Column(db.Float, nullable=False, default=20.0)
    notes = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def total_meters(self) -> float:
        return (self.full_tops * self.meters_per_top) + self.open_meters

    def display_stock(self) -> str:
        return f"{self.full_tops} توب + {self.open_meters:.2f} متر"

    def status_text(self) -> str:
        total = self.total_meters()
        if total <= 0:
            return "خلص"
        if total <= self.low_meter_threshold:
            return "ناقص"
        return "موجود"


class Movement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    movement_type = db.Column(db.String(20), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    item_name = db.Column(db.String(120), nullable=False)
    meter_amount = db.Column(db.Float, nullable=False, default=0.0)
    display_amount = db.Column(db.String(120), nullable=False)
    note = db.Column(db.String(255))
    order_number = db.Column(db.String(80))
    created_by = db.Column(db.String(80), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    item = db.relationship("Item")


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(80), unique=True, nullable=False, index=True)
    customer = db.Column(db.String(120), nullable=False)
    order_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(40), nullable=False, default="جديد")
    notes = db.Column(db.Text)
    deducted = db.Column(db.Boolean, nullable=False, default=False)
    created_by = db.Column(db.String(80), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    lines = db.relationship("OrderLine", backref="order", cascade="all, delete-orphan", lazy=True)


class OrderLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    item_name = db.Column(db.String(120), nullable=False)
    mode = db.Column(db.String(20), nullable=False)
    qty = db.Column(db.Float, nullable=False)
    note = db.Column(db.String(255))

    item = db.relationship("Item")


@login_manager.user_loader
def load_user(user_id: str):
    return User.query.get(int(user_id))


def add_by_meter(item: Item, meters: float) -> None:
    remaining = meters
    space = item.meters_per_top - item.open_meters
    if space > 0:
        put = min(space, remaining)
        item.open_meters += put
        remaining -= put
    if remaining > 0:
        full = int(remaining // item.meters_per_top)
        item.full_tops += full
        remaining -= full * item.meters_per_top
        item.open_meters += remaining
        if item.open_meters >= item.meters_per_top:
            item.full_tops += int(item.open_meters // item.meters_per_top)
            item.open_meters = item.open_meters % item.meters_per_top


def deduct_by_meter(item: Item, meters: float) -> bool:
    remaining = meters
    used = min(item.open_meters, remaining)
    item.open_meters -= used
    remaining -= used
    while remaining > 0 and item.full_tops > 0:
        item.full_tops -= 1
        taken = min(item.meters_per_top, remaining)
        leftover = item.meters_per_top - taken
        remaining -= taken
        item.open_meters = leftover
        if remaining > 0 and item.open_meters > 0:
            take_again = min(item.open_meters, remaining)
            item.open_meters -= take_again
            remaining -= take_again
    return remaining <= 0


@app.route("/init-db")
def init_db():
    db.create_all()
    if not User.query.filter_by(username="admin").first():
        admin = User(full_name="Administrator", username="admin", is_admin=True)
        admin.set_password("123456")
        db.session.add(admin)
        db.session.commit()
    return "Database initialized. Login with admin / 123456"


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        if not full_name or not username or not password:
            flash("اكمل كل البيانات.", "error")
            return redirect(url_for("register"))
        if User.query.filter_by(username=username).first():
            flash("اسم المستخدم مستخدم بالفعل.", "error")
            return redirect(url_for("register"))
        user = User(full_name=full_name, username=username, is_admin=False)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash("تم إنشاء الحساب. سجل دخولك الآن.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for("dashboard"))
        flash("اسم المستخدم أو كلمة المرور غير صحيحة.", "error")
        return redirect(url_for("login"))
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    items = Item.query.order_by(Item.name.asc()).all()
    orders = Order.query.order_by(Order.created_at.desc()).all()
    movements = Movement.query.order_by(Movement.created_at.desc()).limit(12).all()

    alerts = []
    for item in items:
        total = item.total_meters()
        if total <= 0:
            alerts.append(f"{item.name} خلص تمامًا")
        elif total <= item.low_meter_threshold:
            alerts.append(f"{item.name} قرب يخلص — المتبقي {total:.2f} متر")
        if item.open_meters > 0 and item.open_meters <= min(3, item.meters_per_top):
            alerts.append(f"التوب المفتوح في {item.name} قرب يخلص — {item.open_meters:.2f} متر")
    for order in orders:
        if order.status not in ["تم", "ملغي"] and not order.deducted:
            alerts.append(f"الأوردر {order.number} محفوظ ولسه متخصمش من المخزون")

    return render_template(
        "dashboard.html",
        items=items,
        orders=orders[:10],
        movements=movements,
        alerts=alerts[:10],
        total_items=len(items),
        total_tops=sum(item.full_tops for item in items),
        total_meters=sum(item.total_meters() for item in items),
        open_orders=sum(1 for o in orders if o.status not in ["تم", "ملغي"]),
    )


@app.route("/items")
@login_required
def items_page():
    return render_template("items.html", items=Item.query.order_by(Item.name.asc()).all())


@app.route("/items/add", methods=["POST"])
@login_required
def add_item():
    item = Item(
        code=request.form.get("code", "").strip(),
        name=request.form.get("name", "").strip(),
        color=request.form.get("color", "").strip(),
        meters_per_top=float(request.form.get("meters_per_top", 0) or 0),
        full_tops=int(request.form.get("full_tops", 0) or 0),
        open_meters=float(request.form.get("open_meters", 0) or 0),
        low_meter_threshold=float(request.form.get("low_meter_threshold", 20) or 20),
        notes=request.form.get("notes", "").strip(),
        updated_at=datetime.utcnow(),
    )
    if not item.code or not item.name or item.meters_per_top <= 0:
        flash("بيانات الصنف غير مكتملة.", "error")
        return redirect(url_for("items_page"))

    db.session.add(item)
    db.session.flush()
    db.session.add(Movement(
        movement_type="add",
        item_id=item.id,
        item_name=item.name,
        meter_amount=item.total_meters(),
        display_amount=f"{item.full_tops} توب + {item.open_meters:.2f} متر",
        note="رصيد افتتاحي",
        order_number="",
        created_by=current_user.username,
    ))
    db.session.commit()
    flash("تم إضافة الصنف.", "success")
    return redirect(url_for("items_page"))


@app.route("/items/<int:item_id>/quick-add", methods=["POST"])
@login_required
def quick_add(item_id: int):
    item = Item.query.get_or_404(item_id)
    mode = request.form.get("mode", "top")
    qty = float(request.form.get("qty", 0) or 0)
    if qty <= 0:
        flash("الكمية غير صحيحة.", "error")
        return redirect(url_for("items_page"))
    if mode == "meter":
        add_by_meter(item, qty)
        display = f"{qty:.2f} متر"
        meter_amount = qty
    else:
        qty_int = int(qty)
        item.full_tops += qty_int
        display = f"{qty_int} توب"
        meter_amount = qty_int * item.meters_per_top
    item.updated_at = datetime.utcnow()
    db.session.add(Movement(
        movement_type="add",
        item_id=item.id,
        item_name=item.name,
        meter_amount=meter_amount,
        display_amount=display,
        note="إضافة سريعة",
        order_number="",
        created_by=current_user.username,
    ))
    db.session.commit()
    flash("تمت الإضافة.", "success")
    return redirect(url_for("items_page"))


@app.route("/items/<int:item_id>/quick-deduct", methods=["POST"])
@login_required
def quick_deduct(item_id: int):
    item = Item.query.get_or_404(item_id)
    mode = request.form.get("mode", "meter")
    qty = float(request.form.get("qty", 0) or 0)
    if qty <= 0:
        flash("الكمية غير صحيحة.", "error")
        return redirect(url_for("items_page"))
    if mode == "meter":
        if item.total_meters() < qty:
            flash("الكمية غير كافية.", "error")
            return redirect(url_for("items_page"))
        deduct_by_meter(item, qty)
        display = f"{qty:.2f} متر"
        meter_amount = qty
    else:
        qty_int = int(qty)
        if item.full_tops < qty_int:
            flash("عدد التوبات الكاملة غير كاف.", "error")
            return redirect(url_for("items_page"))
        item.full_tops -= qty_int
        display = f"{qty_int} توب"
        meter_amount = qty_int * item.meters_per_top
    item.updated_at = datetime.utcnow()
    db.session.add(Movement(
        movement_type="sub",
        item_id=item.id,
        item_name=item.name,
        meter_amount=meter_amount,
        display_amount=display,
        note="خصم سريع",
        order_number="",
        created_by=current_user.username,
    ))
    db.session.commit()
    flash("تم الخصم.", "success")
    return redirect(url_for("items_page"))


@app.route("/orders")
@login_required
def orders_page():
    return render_template(
        "orders.html",
        items=Item.query.order_by(Item.name.asc()).all(),
        orders=Order.query.order_by(Order.created_at.desc()).all(),
    )


@app.route("/orders/add", methods=["POST"])
@login_required
def add_order():
    number = request.form.get("number", "").strip()
    customer = request.form.get("customer", "").strip()
    status = request.form.get("status", "جديد").strip()
    notes = request.form.get("notes", "").strip()
    order_date_raw = request.form.get("order_date", "").strip()
    item_ids = request.form.getlist("item_id")
    modes = request.form.getlist("mode")
    qtys = request.form.getlist("qty")
    line_notes = request.form.getlist("line_note")
    deduct_now = request.form.get("deduct_now") == "1"

    if not number or not customer or not order_date_raw:
        flash("بيانات الأوردر غير مكتملة.", "error")
        return redirect(url_for("orders_page"))

    order = Order(
        number=number,
        customer=customer,
        order_date=datetime.strptime(order_date_raw, "%Y-%m-%d").date(),
        status=status,
        notes=notes,
        deducted=False,
        created_by=current_user.username,
    )
    db.session.add(order)
    db.session.flush()

    for idx, item_id in enumerate(item_ids):
        if not item_id:
            continue
        item = Item.query.get(int(item_id))
        qty = float(qtys[idx] or 0)
        if not item or qty <= 0:
            continue
        db.session.add(OrderLine(
            order_id=order.id,
            item_id=item.id,
            item_name=item.name,
            mode=modes[idx],
            qty=qty,
            note=line_notes[idx],
        ))

    db.session.commit()
    if deduct_now:
        return redirect(url_for("deduct_order", order_id=order.id))
    flash("تم حفظ الأوردر.", "success")
    return redirect(url_for("orders_page"))


@app.route("/orders/<int:order_id>/deduct")
@login_required
def deduct_order(order_id: int):
    order = Order.query.get_or_404(order_id)
    if order.deducted:
        flash("الأوردر متخصم بالفعل.", "error")
        return redirect(url_for("orders_page"))

    for line in order.lines:
        item = Item.query.get(line.item_id)
        if not item:
            flash(f"الصنف {line.item_name} غير موجود.", "error")
            return redirect(url_for("orders_page"))
        if line.mode == "meter" and item.total_meters() < line.qty:
            flash(f"الكمية غير كافية في {item.name}.", "error")
            return redirect(url_for("orders_page"))
        if line.mode == "top" and item.full_tops < int(line.qty):
            flash(f"عدد التوبات الكاملة غير كاف في {item.name}.", "error")
            return redirect(url_for("orders_page"))

    for line in order.lines:
        item = Item.query.get(line.item_id)
        if line.mode == "meter":
            deduct_by_meter(item, line.qty)
            meter_amount = line.qty
            display = f"{line.qty:.2f} متر"
        else:
            tops_qty = int(line.qty)
            item.full_tops -= tops_qty
            meter_amount = tops_qty * item.meters_per_top
            display = f"{tops_qty} توب"
        item.updated_at = datetime.utcnow()
        db.session.add(Movement(
            movement_type="sub",
            item_id=item.id,
            item_name=item.name,
            meter_amount=meter_amount,
            display_amount=display,
            note="صرف على أوردر",
            order_number=order.number,
            created_by=current_user.username,
        ))

    order.deducted = True
    if order.status == "جديد":
        order.status = "جاري التنفيذ"
    db.session.commit()
    flash("تم الخصم من المخزون.", "success")
    return redirect(url_for("orders_page"))


@app.route("/orders/<int:order_id>/restore")
@login_required
def restore_order(order_id: int):
    order = Order.query.get_or_404(order_id)
    if not order.deducted:
        flash("الأوردر غير مخصوم.", "error")
        return redirect(url_for("orders_page"))

    for line in order.lines:
        item = Item.query.get(line.item_id)
        if not item:
            continue
        if line.mode == "meter":
            add_by_meter(item, line.qty)
            meter_amount = line.qty
            display = f"{line.qty:.2f} متر"
        else:
            tops_qty = int(line.qty)
            item.full_tops += tops_qty
            meter_amount = tops_qty * item.meters_per_top
            display = f"{tops_qty} توب"
        item.updated_at = datetime.utcnow()
        db.session.add(Movement(
            movement_type="return",
            item_id=item.id,
            item_name=item.name,
            meter_amount=meter_amount,
            display_amount=display,
            note="مرتجع من أوردر",
            order_number=order.number,
            created_by=current_user.username,
        ))

    order.deducted = False
    if order.status == "تم":
        order.status = "جاري التنفيذ"
    db.session.commit()
    flash("تم إرجاع الكميات للمخزون.", "success")
    return redirect(url_for("orders_page"))


@app.route("/orders/<int:order_id>/done")
@login_required
def mark_done(order_id: int):
    order = Order.query.get_or_404(order_id)
    order.status = "تم"
    db.session.commit()
    flash("تم إنهاء الأوردر.", "success")
    return redirect(url_for("orders_page"))


@app.route("/movements")
@login_required
def movements_page():
    return render_template(
        "movements.html",
        movements=Movement.query.order_by(Movement.created_at.desc()).all(),
    )


if __name__ == "__main__":
    app.run(debug=True)
