from flask import Flask, request, jsonify
from flask_cors import CORS
import subprocess
import os

app = Flask(__name__)
CORS(app)  

SIMULATION_SCRIPT = os.path.join(os.path.dirname(__file__), 'run_simulation_6.py')

@app.route('/api/start_simulation', methods=['POST'])
def start_simulation():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No input data provided'}), 400

    num_cars = data.get('num_cars')
    num_chargers = data.get('num_chargers')
    num_people = data.get('num_people')
    time_step = data.get('time_step')
    sim_length = data.get('sim_length')  

    if not isinstance(num_cars, int) or num_cars < 1:
        return jsonify({'error': 'Invalid value for num_cars'}), 400
    if not isinstance(num_chargers, int) or num_chargers < 1:
        return jsonify({'error': 'Invalid value for num_chargers'}), 400
    if not isinstance(num_people, int) or num_people < 1:
        return jsonify({'error': 'Invalid value for num_people'}), 400
    if not isinstance(time_step, (int, float)) or time_step <= 0:
        return jsonify({'error': 'Invalid value for time_step'}), 400
    if not isinstance(sim_length, (int, float)) or sim_length <= 0:
        return jsonify({'error': 'Invalid value for time_step'}), 400

    command = [
        'python3',
        SIMULATION_SCRIPT,
        '--num-chargers', str(num_chargers),
        '--num-taxis', str(num_cars),
        '--num-people', str(num_people),
        '--step-length', str(time_step),
        '--sim-length', str(sim_length), 
    ]

    try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=os.environ,
                cwd=os.path.dirname(SIMULATION_SCRIPT),
                check=True
            )
            return jsonify({
                'status': 'Simulation completed successfully',
                'output': result.stdout,
                'errors': result.stderr
            }), 200
    except subprocess.CalledProcessError as e:
        return jsonify({
            'error': 'Simulation failed',
            'output': e.stdout,
            'errors': e.stderr
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)