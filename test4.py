# from flask import Flask, request, jsonify
# from simulation_runner import SimulationRunner
# import os
# import sys

# # Ensure SUMO_HOME is set
# if 'SUMO_HOME' not in os.environ:
#     os.environ['SUMO_HOME'] = '/path/to/your/sumo'  # Replace with your SUMO_HOME path
# sys.path.append(os.path.join(os.environ['SUMO_HOME'], 'tools'))

# app = Flask(__name__)

# simulation_runner = None  # Initialize simulation runner as None

# @app.route('/start_simulation', methods=['POST'])
# def start_simulation():
#     global simulation_runner
#     if simulation_runner and simulation_runner.is_running:
#         return jsonify({'status': 'error', 'message': 'Simulation is already running.'}), 400

#     data = request.get_json()
#     step_length = float(data.get('step_length', 1.0))
#     sim_length = float(data.get('sim_length', 1000))
#     num_people = int(data.get('num_people', 3))
#     num_taxis = int(data.get('num_taxis', 3))

#     # Start the simulation runner with initial parameters
#     simulation_runner = SimulationRunner(
#         step_length=step_length,
#         sim_length=sim_length,
#         num_people=num_people,
#         num_taxis=num_taxis
#     )
#     simulation_runner.start()

#     return jsonify({'status': 'success', 'message': 'Simulation started.'})

# # ... (rest of the endpoints remain the same)


# @app.route('/add_taxi', methods=['POST'])
# def add_taxi():
#     if not simulation_runner or not simulation_runner.is_running:
#         return jsonify({'status': 'error', 'message': 'Simulation is not running.'}), 400
#     data = request.get_json()
#     num_taxis = int(data.get('num_taxis', 1))
#     simulation_runner.spawn_taxis_at_runtime(num_taxis)
#     return jsonify({'status': 'success', 'message': f'Added {num_taxis} taxis.'})

# @app.route('/add_person', methods=['POST'])
# def add_person():
#     if not simulation_runner or not simulation_runner.is_running:
#         return jsonify({'status': 'error', 'message': 'Simulation is not running.'}), 400
#     data = request.get_json()
#     num_people = int(data.get('num_people', 1))
#     simulation_runner.add_people_at_runtime(num_people)
#     return jsonify({'status': 'success', 'message': f'Added {num_people} people.'})

# @app.route('/add_charger', methods=['POST'])
# def add_charger():
#     if not simulation_runner or not simulation_runner.is_running:
#         return jsonify({'status': 'error', 'message': 'Simulation is not running.'}), 400
#     data = request.get_json()
#     num_chargers = int(data.get('num_chargers', 1))
#     simulation_runner.add_chargers_at_runtime(num_chargers)
#     return jsonify({'status': 'success', 'message': f'Added {num_chargers} chargers.'})

# @app.route('/shutdown', methods=['POST'])
# def shutdown():
#     global simulation_runner
#     if not simulation_runner or not simulation_runner.is_running:
#         return jsonify({'status': 'error', 'message': 'Simulation is not running.'}), 400
#     simulation_runner.stop()
#     simulation_runner.join()
#     simulation_runner = None
#     return jsonify({'status': 'success', 'message': 'Simulation stopped.'})

# if __name__ == '__main__':
#     app.run(debug=True)

from flask import Flask, request, jsonify
from simulation_runner import SimulationRunner
import os
import sys

# Ensure SUMO_HOME is set
if 'SUMO_HOME' not in os.environ:
    os.environ['SUMO_HOME'] = '/path/to/your/sumo'  # Replace with your SUMO_HOME path
sys.path.append(os.path.join(os.environ['SUMO_HOME'], 'tools'))

app = Flask(__name__)

simulation_runner = None  # Initialize simulation runner as None

@app.route('/start_simulation', methods=['POST'])
def start_simulation():
    global simulation_runner
    if simulation_runner and simulation_runner.is_running:
        return jsonify({'status': 'error', 'message': 'Simulation is already running.'}), 400

    data = request.get_json()
    step_length = float(data.get('step_length', 1.0))
    sim_length = float(data.get('sim_length', 1000))
    num_people = int(data.get('num_people', 3))
    num_taxis = int(data.get('num_taxis', 3))

    # Start the simulation runner with initial parameters
    simulation_runner = SimulationRunner(
        step_length=step_length,
        sim_length=sim_length,
        num_people=num_people,
        num_taxis=num_taxis
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

@app.route('/add_taxi', methods=['POST'])
def add_taxi():
    if not simulation_runner or not simulation_runner.is_running:
        return jsonify({'status': 'error', 'message': 'Simulation is not running.'}), 400
    data = request.get_json()
    num_taxis = int(data.get('num_taxis', 1))
    simulation_runner.command_queue.put({'action': 'add_taxi', 'num_taxis': num_taxis})
    return jsonify({'status': 'success', 'message': f'Adding {num_taxis} taxis to the simulation.'})

@app.route('/add_charger', methods=['POST'])
def add_charger():
    if not simulation_runner or not simulation_runner.is_running:
        return jsonify({'status': 'error', 'message': 'Simulation is not running.'}), 400
    data = request.get_json()
    num_chargers = int(data.get('num_chargers', 1))
    simulation_runner.command_queue.put({'action': 'add_charger', 'num_chargers': num_chargers})
    return jsonify({'status': 'success', 'message': f'Adding {num_chargers} chargers to the simulation.'})

@app.route('/shutdown', methods=['POST'])
def shutdown():
    global simulation_runner
    if not simulation_runner or not simulation_runner.is_running:
        return jsonify({'status': 'error', 'message': 'Simulation is not running.'}), 400
    simulation_runner.stop()
    simulation_runner.join()
    simulation_runner = None
    return jsonify({'status': 'success', 'message': 'Simulation stopped.'})

if __name__ == '__main__':
    app.run(debug=True)

