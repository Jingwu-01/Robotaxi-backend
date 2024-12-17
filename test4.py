from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from simulation_runner import SimulationRunner
import os
import sys

# Ensure SUMO_HOME is set
if 'SUMO_HOME' not in os.environ:
    os.environ['SUMO_HOME'] = '/path/to/your/sumo'  # Replace with your SUMO_HOME path
sys.path.append(os.path.join(os.environ['SUMO_HOME'], 'tools'))

app = Flask(__name__)
CORS(app)

simulation_runner = None  

@app.route('/start_simulation', methods=['POST'])
def start_simulation():
    global simulation_runner
    if simulation_runner and simulation_runner.is_running:
        return jsonify({'status': 'error', 'message': 'Simulation is already running.'}), 400

    data = request.get_json()
    step_length = float(data.get('step_length', 0.5))
    sim_start_time = float(data.get('sim_start_time', 0))
    sim_end_time = float(data.get('sim_end_time', 7200))
    num_people = int(data.get('num_people', 1000))
    num_taxis = int(data.get('num_taxis', 50))
    num_chargers = int(data.get('num_chargers', 100))  # Get num_chargers from POST data
    optimized = bool(data.get('optimized', False))
    output_freq = float(data.get('output_freq', 50))

    # Start the simulation runner with initial parameters
    simulation_runner = SimulationRunner(
        step_length=step_length,
        sim_start_time = sim_start_time,
        sim_end_time=sim_end_time,
        num_people=num_people,
        num_taxis=num_taxis,
        num_chargers=num_chargers,  # Pass num_chargers to SimulationRunner
        optimized=optimized,
        output_freq=output_freq
    )
    simulation_runner.start()

    return jsonify({'status': 'success', 'message': 'Simulation started.'})

@app.route('/add_person', methods=['POST'])
def add_person():
    if not simulation_runner or not simulation_runner.is_running:
        return jsonify({'status': 'error', 'message': 'Simulation is not running.'}), 400
    data = request.get_json()
    num_people = int(data.get('num_people', 1))
    simulation_runner.command_queue.put({'action': 'add_person', 'num_people': num_people})
    return jsonify({'status': 'success', 'message': f'Adding {num_people} people to the simulation.'})

@app.route('/remove_person', methods=['POST'])
def remove_person():
    if not simulation_runner or not simulation_runner.is_running:
        return jsonify({'status': 'error', 'message': 'Simulation is not running.'}), 400
    data = request.get_json()
    num_people = int(data.get('num_people', 1))
    simulation_runner.command_queue.put({'action': 'remove_person', 'num_people': num_people})
    return jsonify({'status': 'success', 'message': f'Removing {num_people} people from the simulation.'})

@app.route('/add_taxi', methods=['POST'])
def add_taxi():
    if not simulation_runner or not simulation_runner.is_running:
        return jsonify({'status': 'error', 'message': 'Simulation is not running.'}), 400
    data = request.get_json()
    num_taxis = int(data.get('num_taxis', 1))
    simulation_runner.command_queue.put({'action': 'add_taxi', 'num_taxis': num_taxis})
    return jsonify({'status': 'success', 'message': f'Adding {num_taxis} taxis to the simulation.'})

@app.route('/remove_taxi', methods=['POST'])
def remove_taxi():
    if not simulation_runner or not simulation_runner.is_running:
        return jsonify({'status': 'error', 'message': 'Simulation is not running.'}), 400
    data = request.get_json()
    num_taxis = int(data.get('num_taxis', 1))
    simulation_runner.command_queue.put({'action': 'remove_taxi', 'num_taxis': num_taxis})
    return jsonify({'status': 'success', 'message': f'Removing {num_taxis} taxis from the simulation.'})

@app.route('/add_charger', methods=['POST'])
def add_charger():
    if not simulation_runner or not simulation_runner.is_running:
        return jsonify({'status': 'error', 'message': 'Simulation is not running.'}), 400
    data = request.get_json()
    num_chargers = int(data.get('num_chargers', 1))
    simulation_runner.command_queue.put({'action': 'add_charger', 'num_chargers': num_chargers})
    return jsonify({'status': 'success', 'message': f'Adding {num_chargers} chargers to the simulation.'})

@app.route('/remove_charger', methods=['POST'])
def remove_charger():
    if not simulation_runner or not simulation_runner.is_running:
        return jsonify({'status': 'error', 'message': 'Simulation is not running.'}), 400
    data = request.get_json()
    num_chargers = int(data.get('num_chargers', 1))
    simulation_runner.command_queue.put({'action': 'remove_charger', 'num_chargers': num_chargers})
    return jsonify({'status': 'success', 'message': f'Removing {num_chargers} chargers from the simulation.'})

@app.route('/status', methods=['GET'])
def status():
    if not simulation_runner or not simulation_runner.is_running:
        return jsonify({'status': 'error', 'message': 'Simulation is not running.'}), 400
    status = simulation_runner.get_status()
    return jsonify({'status': 'success', 'data': status})

@app.route('/shutdown', methods=['POST'])
def shutdown():
    global simulation_runner
    if not simulation_runner or not simulation_runner.is_running:
        return jsonify({'status': 'error', 'message': 'Simulation is not running.'}), 400
    simulation_runner.stop()
    simulation_runner.join()
    simulation_runner = None
    return jsonify({'status': 'success', 'message': 'Simulation stopped.'})

@app.route('/network', methods=['GET'])
def get_network():
    try:
        return send_file('network.geojson', mimetype='application/json')
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route('/electricityConsumption', methods=['GET'])
def get_electricity_consumption():
    if not simulation_runner or not simulation_runner.is_running:
        return jsonify({'status': 'error', 'message': 'Simulation is not running.'}), 400
    consumption_data = simulation_runner.get_electricity_consumption()
    return jsonify({'status': 'success', 'data': consumption_data})

@app.route('/vehicle_positions', methods=['GET'])
def get_vehicle_positions():
    if not simulation_runner or not simulation_runner.is_running:
        return jsonify({'status': 'error', 'message': 'Simulation is not running.'}), 400

    positions = simulation_runner.get_vehicle_positions()
    return jsonify({'status': 'success', 'data': positions})

@app.route('/passenger_positions', methods=['GET'])
def get_passenger_positions():
    if not simulation_runner or not simulation_runner.is_running:
        return jsonify({'status': 'error', 'message': 'Simulation is not running.'}), 400

    passenger_positions = simulation_runner.get_passenger_positions()
    return jsonify({'status': 'success', 'data': passenger_positions})

@app.route('/charger_positions', methods=['GET'])
def get_charger_positions():
    if not simulation_runner or not simulation_runner.is_running:
        return jsonify({'status': 'error', 'message': 'Simulation is not running.'}), 400

    charger_positions = simulation_runner.get_charger_positions()
    return jsonify({'status': 'success', 'data': charger_positions})

@app.route('/batteryLevels', methods=['GET'])
def battery_levels():
    if not simulation_runner or not simulation_runner.is_running:
        return jsonify({'status': 'error', 'message': 'Simulation is not running.'}), 400
    battery_levels = simulation_runner.get_battery_levels()
    return jsonify({'status': 'success', 'data': battery_levels})

@app.route('/averagePassengerWaitTime', methods=['GET'])
def get_average_passenger_wait_time():
    if not simulation_runner or not simulation_runner.is_running:
        return jsonify({'status': 'error', 'message': 'Simulation is not running.'}), 400
    average_wait_time = simulation_runner.get_average_passenger_wait_time()
    return jsonify({'status': 'success', 'data': {'average_wait_time': average_wait_time}})

@app.route('/activePassengers', methods=['GET'])
def get_active_passengers():
    if not simulation_runner or not simulation_runner.is_running:
        return jsonify({'status': 'error', 'message': 'Simulation is not running.'}), 400

    active_passengers_count = simulation_runner.get_active_passengers_count()
    return jsonify({'status': 'success', 'data': {'active_passengers': active_passengers_count}})



if __name__ == '__main__':
    app.run(debug=True)