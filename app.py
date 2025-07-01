from flask import Flask, render_template, request, redirect, url_for, session
from pymongo import MongoClient
from dotenv import load_dotenv
from datetime import datetime
import random
import string
import os
import stripe
import uuid
import json
from sklearn.metrics.pairwise import cosine_similarity



# Load environment variables from .env file
load_dotenv()
print("Loaded MONGO_URI:", os.getenv("MONGO_URI"))


# Stripe setup
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
YOUR_DOMAIN = os.getenv("YOUR_DOMAIN")

app = Flask(__name__)
app.secret_key = 'secret123'

# Admin login credentials
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "adminpass")

# MongoDB setup
mongo_uri = os.getenv("MONGO_URI")
if not mongo_uri:
    raise ValueError("MONGO_URI is not set. Please check your .env file.")

client = MongoClient(mongo_uri)
db = client["marketplace_bot"]
users = db["users"]
orders = db["orders"]


# Generate credentials if user doesn't exist
def generate_credentials():
    username = "user_" + ''.join(random.choices(string.digits, k=3))
    password = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    return username, password

# Home/Login route
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        telegram_id_input = request.form['telegram_id']

        # Ensure input is valid integer
        if not telegram_id_input.isdigit():
            return render_template('login.html', error="⚠️ Please enter a valid numeric Telegram ID.")

        telegram_id = int(telegram_id_input)
        user = users.find_one({"telegram_id": telegram_id})

        if not user:
            # Auto-create user
            username, password = generate_credentials()
            user = {
                "telegram_id": telegram_id,
                "username": username,
                "password": password,
                "wallet": 0.0,
                "joined": datetime.utcnow()
            }
            users.insert_one(user)

        session['telegram_id'] = telegram_id
        return redirect(url_for('dashboard'))

    return render_template('login.html')

@app.route('/')
def welcome():
    return render_template('welcome.html')


# Dashboard route
@app.route('/dashboard')
def dashboard():
    if 'telegram_id' not in session:
        return redirect(url_for('login'))

    telegram_id = session['telegram_id']
    user = db.users.find_one({"telegram_id": telegram_id})
    user_orders = list(db.orders.find({"telegram_id": telegram_id}))
    msg = request.args.get("msg")
    notifications = user.get("notifications", [])

    return render_template(
        'dashboard.html',
        user=user,
        orders=user_orders,
        msg=msg,
        notifications=notifications
    )

# Logout
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

#Add Buy Route 
@app.route('/buy/<int:amount>')
def buy(amount):
    if 'telegram_id' not in session:
        return redirect(url_for('login'))

    telegram_id = session['telegram_id']
    user = db.users.find_one({"telegram_id": telegram_id})

    if not user:
        return "❌ User not found", 404

    # Check if user has enough balance
    if user['wallet'] < amount:
        return redirect(url_for('recharge', msg="❌ Not enough balance. Please recharge your wallet."))

    # Deduct wallet balance
    db.users.update_one(
        {"telegram_id": telegram_id},
        {"$inc": {"wallet": -amount}}
    )

    # Create new order
    order_id = str(uuid.uuid4())
    db.orders.insert_one({
        "order_id": order_id,
        "telegram_id": telegram_id,
        "amount": amount,
        "status": "pending",  # could also use "url_submitted"
        "post_url": None,
        "created_at": datetime.utcnow()
    })

    # Show success on dashboard
    return redirect(url_for('dashboard', msg=f"✅ ${amount} deducted from your wallet. Order ID: {order_id}"))



@app.route('/submit_url/<order_id>', methods=['GET', 'POST'])
def submit_url(order_id):
    if 'telegram_id' not in session:
        return redirect(url_for('login'))

    # Show a placeholder message instead of collecting URL
    return "🚧 Submitting post URLs is currently disabled. Please wait for this feature to be activated."


@app.route('/recharge', methods=['GET', 'POST'])
def recharge():
    if 'telegram_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        try:
            telegram_id = session.get("telegram_id")
            amount_dollars = float(request.form['amount'])  # ✅ Read user input from form

            if amount_dollars < 1:
                return "❌ Minimum amount is $1", 400

            checkout_session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {
                            'name': f'Wallet Recharge: ${amount_dollars}',
                        },
                        'unit_amount': int(amount_dollars * 100),  # Stripe uses cents
                    },
                    'quantity': 1,
                }],
                mode='payment',
                success_url=f"{YOUR_DOMAIN}/wallet_success?session_id={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{YOUR_DOMAIN}/dashboard",
                metadata={
                    "telegram_id": telegram_id,
                    "amount": str(int(amount_dollars))  # Pass amount back via metadata
                }
            )

            return redirect(checkout_session.url)

        except Exception as e:
            print("❌ Error in /recharge POST:", e)
            return "An error occurred while processing your payment.", 500

    # Show form when method is GET
    return render_template("recharge.html")


@app.route('/success')
def success():

    session_id = request.args.get('session_id')
    if not session_id:
        return "❌ Missing session ID", 400

    try:
        # Fetch Stripe session details
        checkout_session = stripe.checkout.Session.retrieve(session_id)
        if checkout_session.payment_status != 'paid':
            return "❌ Payment not successful", 400

        telegram_id = int(checkout_session.metadata['telegram_id'])
        amount = int(checkout_session.metadata['amount'])

        # Generate a unique order ID
        order_id = str(uuid.uuid4())

        # Insert new order into the database
        db.orders.insert_one({
            "order_id": order_id,
            "telegram_id": telegram_id,
            "amount": amount,
            "status": "pending",  # or "url_submitted" if you want to skip the URL input step
            "post_url": None,
            "created_at": datetime.utcnow()
        })

        return redirect(url_for('dashboard', msg='✅ Order placed successfully!'))


    except Exception as e:
        return f"❌ Error: {str(e)}", 500


@app.route('/admin', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            return render_template('admin_login.html', error="Invalid admin credentials.")
    return render_template('admin_login.html')


@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    pending_orders = list(db.orders.find({"status": "pending"}))
    admin_doc = db.admin_notifications.find_one({"role": "admin"})
    notifications = admin_doc.get("notifications", []) if admin_doc else []

    return render_template('admin_dashboard.html', orders=pending_orders, notifications=notifications)


@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))


@app.route('/admin/decision/<order_id>/<action>')
def admin_decision(order_id, action):
    order = db.orders.find_one({"order_id": order_id})
    if not order:
        return "❌ Order not found."

    user = db.users.find_one({"telegram_id": order['telegram_id']})
    if not user:
        return "❌ User not found."

    admin_notification = None

    if action == 'approve':
        db.orders.update_one(
            {"order_id": order_id},
            {"$set": {"status": "approved"}}
        )

        # Notify user
        db.users.update_one(
            {"telegram_id": user['telegram_id']},
            {"$push": {
                "notifications": {
                    "message": f"✅ {user['username']}, your order {order_id} was approved.",
                    "timestamp": datetime.utcnow()
                }
            }}
        )

        # Notify admin
        admin_notification = {
            "message": f"✅ Approved order {order_id} for user {user['username']}.",
            "timestamp": datetime.utcnow()
        }

    elif action == 'reject':
        db.orders.update_one(
            {"order_id": order_id},
            {"$set": {"status": "rejected"}}
        )

        db.users.update_one(
            {"telegram_id": user['telegram_id']},
            {"$inc": {"wallet": order['amount']}}
        )

        # Notify user
        db.users.update_one(
            {"telegram_id": user['telegram_id']},
            {"$push": {
                "notifications": {
                    "message": f"❌ {user['username']}, your order {order_id} was rejected. ${order['amount']} refunded.",
                    "timestamp": datetime.utcnow()
                }
            }}
        )

        # Notify admin
        admin_notification = {
            "message": f"❌ Rejected order {order_id} for user {user['username']}. Refunded ${order['amount']}.",
            "timestamp": datetime.utcnow()
        }

    if admin_notification:
        db.admin_notifications.update_one(
            {"role": "admin"},
            {"$push": {"notifications": admin_notification}},
            upsert=True
        )

    return redirect(url_for('admin_dashboard'))

    
@app.route('/orders/approved')
def approved_orders():
    if 'telegram_id' not in session:
        return redirect(url_for('login'))

    orders = list(db.orders.find({"telegram_id": session['telegram_id'], "status": "approved"}))
    return render_template('approved_orders.html', orders=orders)


@app.route('/orders/rejected')
def rejected_orders():
    if 'telegram_id' not in session:
        return redirect(url_for('login'))

    orders = list(db.orders.find({"telegram_id": session['telegram_id'], "status": "rejected"}))
    return render_template('rejected_orders.html', orders=orders)



@app.route('/wallet_success')
def wallet_success():
    session_id = request.args.get('session_id')
    if not session_id:
        return "❌ Missing session ID", 400

    try:
        # Get session from Stripe
        session_data = stripe.checkout.Session.retrieve(session_id)
        if session_data.payment_status != 'paid':
            return "❌ Payment not completed", 400

        # Extract info
        telegram_id = int(session_data.metadata['telegram_id'])
        amount = int(session_data.metadata['amount'])

        # Update wallet
        user = db.users.find_one({"telegram_id": telegram_id})
        if not user:
            return "❌ User not found", 404

        new_balance = user.get('wallet', 0) + amount
        db.users.update_one(
            {"telegram_id": telegram_id},
            {"$set": {"wallet": new_balance}}
        )

        # Optional: add a notification in DB
        db.users.update_one(
            {"telegram_id": telegram_id},
            {"$push": {
                "notifications": {
                    "message": f"✅ Your wallet was recharged with ${amount}.",
                    "timestamp": datetime.utcnow()
                }
            }}
        )

        # Show confirmation on the website
        return render_template('payment_success.html', username=user['username'], amount=amount, balance=new_balance)

    except Exception as e:
        return f"❌ Error: {str(e)}", 500


@app.route('/clear_notifications', methods=['POST'])
def clear_notifications():
    if 'telegram_id' not in session:
        return redirect(url_for('login'))

    db.users.update_one(
        {"telegram_id": session['telegram_id']},
        {"$set": {"notifications": []}}
    )
    return redirect(url_for('dashboard', msg="✅ All notifications cleared."))

@app.route('/admin/clear_notifications', methods=['POST'])
def clear_admin_notifications():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    db.admin_notifications.update_one(
        {"role": "admin"},
        {"$set": {"notifications": []}}
    )
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/pending')
def view_pending_orders():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    pending_orders = list(db.orders.find({"status": "pending"}))
    return render_template('pending_orders.html', orders=pending_orders)

@app.route('/admin/assign', methods=['GET', 'POST'])
def assign_order_manual():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    users_list = list(db.users.find({}))

    if request.method == 'POST':
        telegram_id = int(request.form['telegram_id'])
        post_url = request.form['post_url']
        amount = float(request.form['amount'])

        # Create new order
        order = {
            "order_id": str(uuid.uuid4())[:8],
            "telegram_id": telegram_id,
            "url": post_url,
            "amount": amount,
            "status": "approved",
            "timestamp": datetime.utcnow()
        }
        db.orders.insert_one(order)

        # Notify user
        db.users.update_one(
            {"telegram_id": telegram_id},
            {"$push": {
                "notifications": {
                    "message": f"✅ Your manual order has been approved by admin.",
                    "timestamp": datetime.utcnow()
                }}
            }
        )

        return redirect(url_for('admin_dashboard'))

    return render_template('assign_order.html', users=users_list)

@app.route('/support/faq')
def support_faq():
    import json
    with open('faq_data.json') as f:
        data = json.load(f)
    return render_template("faq_only.html", deposit=data['deposit'], orders=data['orders'], policy=data['policy'])


if __name__ == '__main__':
    print("✅ Flask app is starting...")
    app.run(debug=True)