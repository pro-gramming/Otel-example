from flask import Flask, render_template, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
from random import randint, uniform
import threading
import logging
from sqlalchemy.exc import IntegrityError
from logging.handlers import RotatingFileHandler

#### otel dependencies ####

# Import the function to set the global logger provider from the OpenTelemetry logs module.
from opentelemetry._logs import set_logger_provider

# Import the OTLPLogExporter class from the OpenTelemetry gRPC log exporter module.
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
    OTLPLogExporter,
)

# Import the LoggerProvider and LoggingHandler classes from the OpenTelemetry SDK logs module.
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler

# Import the BatchLogRecordProcessor class from the OpenTelemetry SDK logs export module.
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

# Import the Resource class from the OpenTelemetry SDK resources module.
from opentelemetry.sdk.resources import Resource

############################

## Otel logger initialization ##

# Create an instance of LoggerProvider with a Resource object that includes
# service name and instance ID, identifying the source of the logs.
logger_provider = LoggerProvider(
    resource=Resource.create(
        {
            "service.name": "greenhouse-app",
            "service.instance.id": "instance-1",
        }
    ),
)

# Set the created LoggerProvider as the global logger provider.
set_logger_provider(logger_provider)

# Create an instance of OTLPLogExporter with insecure connection.
exporter = OTLPLogExporter(insecure=True)

# Add a BatchLogRecordProcessor to the logger provider with the exporter.
logger_provider.add_log_record_processor(BatchLogRecordProcessor(exporter))

# Create a LoggingHandler with the specified logger provider and log level set to NOTSET.
handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)

# Attach OTLP handler to the root logger.
logging.getLogger().addHandler(handler)

#################################

app = Flask(__name__)

app.config['SECRET_KEY'] = 'plantsarecool1234'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///carnivorous_green_house.db'
db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", engineio_logger=True)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    plants = db.relationship('Plant', backref='owner', lazy=True)

class Plant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    plant_type = db.Column(db.String(50), nullable=False)
    health_data = db.Column(db.String(300), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

@app.route('/')
def index():
    error_mode = session.get('error_mode', False)  # Get the current error mode state
    return render_template('index.html', error_mode=error_mode)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    error_mode = session.get('error_mode', False)  # Get the current error mode state
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        hashed_password = generate_password_hash(password)
        new_user = User(username=username, password_hash=hashed_password)
        try:
            db.session.add(new_user)
            db.session.commit()
            logging.info(f"New user created: {username}")
            return redirect(url_for('login'))
        except IntegrityError:
            db.session.rollback()  # Important to rollback the session to clean state
            logging.error(f"Signup failed: Username '{username}' already exists.")
            return render_template('signup.html', error="That username is already taken, please choose another.", error_mode=error_mode)
        except Exception as e:
            db.session.rollback()
            logging.error(f"An unexpected error occurred during signup:{str(e)}")
            return render_template('signup.html', error="An unexpected error occurred. Please try again.", error_mode=error_mode)
    return render_template('signup.html', error_mode=error_mode)

@app.route('/login', methods=['GET', 'POST'])
def login():
    error_mode = session.get('error_mode', False)  # Get the current error mode state
    if request.method == 'POST':
        if session.get('error_mode', False) and randint(0, 1):
            logging.error("Login process failed unexpectedly.")
            return 'Login Error', 500

        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            return redirect(url_for('dashboard'))
        return 'Login Failed'
    return render_template('login.html', error_mode=error_mode)

@app.route('/logout')
def logout():
    error_mode = session.get('error_mode', False)  # Get the current error mode state
    if session.get('error_mode', False) and randint(0, 1):
        logging.error("Logout failed due to session error.")
        return "Logout Error", 500
    
    session.pop('user_id', None)
    return redirect(url_for('index'))

@app.route('/dashboard', methods=['GET'])
def dashboard():
    error_mode = session.get('error_mode', False)  # Get the current error mode state
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    user = User.query.get(user_id)
    plants = Plant.query.filter_by(user_id=user_id).all()
    
    return render_template('dashboard.html', user=user, plants=plants, error_mode=error_mode)

@app.route('/toggle_error_mode', methods=['POST'])
def toggle_error_mode():
    current_mode = session.get('error_mode', False)
    session['error_mode'] = not current_mode  # Toggle the state
    session.modified = True  # Make sure the change is saved
    logging.info(f"Error mode toggled to {'on' if session['error_mode'] else 'off'}.")
    return redirect(request.referrer or url_for('index'))

@socketio.on('add_plant')
def handle_add_plant(json):
    user_id = session.get('user_id')
    if not user_id or (session.get('error_mode', False) and randint(0, 1)):
        logging.error("Unauthorized or failed attempt to add plant.")
        emit('error', {'error': 'Failed to add plant due to server error'}, room=request.sid)
        return
    plant_name = json.get('plant_name')
    plant_type = json.get('plant_type')
    new_plant = Plant(name=plant_name, plant_type=plant_type, health_data="Healthy", user_id=user_id)
    db.session.add(new_plant)
    db.session.commit()
    emit('new_plant', {'plant_id': new_plant.id, 'plant_name': new_plant.name, 'plant_type': new_plant.plant_type}, room=str(user_id))
    logging.info(f"New plant {plant_name} added successfully.")


active_users = {}

@socketio.on('connect')
def handle_connect():
    user_id = session.get('user_id')
    if user_id:
        # Initialize or update the user's status including error mode
        active_users[user_id] = {
            'error_mode': session.get('error_mode', False)
        }
        join_room(str(user_id))
        logging.info(f"User {user_id} connected and joined their room with error mode {active_users[user_id]['error_mode']}.")

@socketio.on('disconnect')
def on_disconnect():
    user_id = session.get('user_id')
    if user_id in active_users:
        del active_users[user_id]
        logging.info(f"User {user_id} disconnected and was removed from active list.")

def simulate_plant_data():
    while True:
        with app.app_context():
            socketio.sleep(2)  # Sleep for 10 seconds
            for user_id, user_info in list(active_users.items()):
                try:
                    if user_info['error_mode'] and randint(0, 1):
                        # Log an error message and continue without sending data
                        logging.warn(f"Failed to send data to: {user_id}: Will retry later")
                        continue

                    plants = Plant.query.filter_by(user_id=user_id).all()
                    for plant in plants:
                        fake_data = {
                            'temperature': round(uniform(20.0, 30.0), 2),
                            'humidity': round(uniform(40.0, 60.0), 2),
                            'water_level': randint(1, 10),
                            'number_of_insects': randint(0, 10)
                        }
                        socketio.emit('update_plant', {'plant_id': plant.id, 'data': fake_data}, room=str(user_id))
                        logging.debug(f"Simulated data for plant {plant.id} sent to user {user_id}")
                except Exception as e:
                    logging.error(f"Error in simulation thread for user {user_id}: {str(e)}")


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    socketio.start_background_task(simulate_plant_data)
    socketio.run(app=app, host="0.0.0.0", port=5005, allow_unsafe_werkzeug=True)
