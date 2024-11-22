from flask import Flask, request, jsonify
from flask_cors import CORS
import threading
import traci
import os
import sys
import time
import random
import sumolib

app = Flask(__name__)
CORS(app)  # Enable Cross-Origin Resource Sharing

# =========================
# Configuration Parameters
# =========================

# Path to SUMO installation directory
SUMO_HOME = "/path/to/sumo"  # **Update this path**

# Path to SUMO binary ('sumo' or 'sumo-gui' for GUI)
SUMO_BINARY = os.path.join(SUMO_HOME, "bin", "sumo")  # or 'sumo-gui'

SUMO_CONFIG = "simulation2.sumocfg"

# Network file derived from SUMO_CONFIG (assumes same base name)
NETWORK_FILE = "downtown_houston.net.xml"

# =========================
# Ensure SUMO_HOME is Set
# =========================

if 'SUMO_HOME' in os.environ:
    tools = os.path.join(os.environ['SUMO_HOME'], 'tools')
    sys.path.append(tools)
else:
    sys.exit("Please set the 'SUMO_HOME' environment variable.")

# =========================
# Global Variables
# =========================

# Simulation control
simulation_thread = None
simulation_running = False
lock = threading.Lock()

# Simulation parameters with default values
simulation_parameters = {
    'num_cars': 3,         # Number of taxis
    'num_chargers': 3,     # Number of chargers
    'num_people': 3,       # Number of people
    'time_step': 1.0,      # Step length in seconds
    'sim_length': 1000     # Simulation length in seconds
}

# Vehicle ID tracking
vehicle_counter = 0
vehicle_counter_lock = threading.Lock()

# Cumulative energy consumption per vehicle (in kJ)
cumulative_consumption = {}
cumulative_consumption_lock = threading.Lock()

# Previous energy consumption per vehicle (in kJ)
previous_consumption = {}
previous_consumption_lock = threading.Lock()

# =========================
# Utility Functions
# =========================

def generate_vehicle_id():
    """Generates a unique vehicle ID."""
    global vehicle_counter
    with vehicle_counter_lock:
        vehicle_id = f"veh{vehicle_counter}"
        vehicle_counter += 1
    return vehicle_id

def generate_persons_xml(net, num_people=3):
    """Generates an XML file with person definitions including ride stages."""
    valid_edges = [edge.getID() for edge in net.getEdges() if edge.getLaneNumber() > 0]
    persons = []

    for i in range(num_people):
        person_id = f"person_{i}"
        pickup_edge = random.choice(valid_edges)
        dropoff_edge = random.choice(valid_edges)
        while pickup_edge == dropoff_edge:
            dropoff_edge = random.choice(valid_edges)
        person_xml = f'''
    <person id="{person_id}" depart="0.00">
        <ride from="{pickup_edge}" to="{dropoff_edge}" lines="taxi"/>
    </person>
        '''
        persons.append(person_xml)
        print(f"Person {person_id} added with ride from {pickup_edge} to {dropoff_edge}")

    with open('persons.add.xml', 'w') as f:
        f.write('<additional>\n')
        for person in persons:
            f.write(person)
        f.write('</additional>\n')
    print("Persons written to 'persons.add.xml'")
    return len(persons)

def parse_charger_coords_file(file_path):
    """Parses the charger coordinates file and returns a list of (lane_id, position)."""
    chargers = []
    with open(file_path, 'r') as f:
        for line in f:
            lane_id, position = line.strip().split(',')
            chargers.append((lane_id.strip(), float(position.strip())))
    return chargers

def generate_valid_charger_locations(net, num_chargers):
    """Generates valid charger locations, ensuring they are placed on valid lanes."""
    valid_lanes = [lane for edge in net.getEdges() for lane in edge.getLanes()]
    chargers = []
    retries = 0

    while len(chargers) < num_chargers:
        if retries > 1000:  # Fail-safe to avoid infinite loops
            raise ValueError("Unable to find enough valid lanes for chargers.")
        
        lane = random.choice(valid_lanes)
        lane_id = lane.getID()
        position = random.uniform(0, lane.getLength())
        
        if all(charger[0] != lane_id for charger in chargers):
            chargers.append((lane_id, position))
        retries += 1

    return chargers

def validate_chargers_against_network(net, chargers):
    """Ensures all chargers are in valid locations."""
    valid_lanes = {lane.getID(): lane.getLength() for edge in net.getEdges() for lane in edge.getLanes()}
    validated_chargers = []
    for lane_id, position in chargers:
        if lane_id in valid_lanes and 0 <= position <= valid_lanes[lane_id]:
            validated_chargers.append((lane_id, position))
        else:
            print(f"Invalid charger location: lane_id={lane_id}, position={position}")
    if len(validated_chargers) != len(chargers):
        raise ValueError("Some charger locations were invalid.")
    return validated_chargers

def write_detectors_file(chargers):
    """Writes detectors.add.xml, ensuring proper formatting and valid locations."""
    with open("detectors.add.xml", "w") as f:
        f.write('<additional>\n')
        for i, (lane_id, position) in enumerate(chargers):
            f.write(f'    <inductionLoop id="charger_{i}" lane="{lane_id}" pos="{position:.2f}" freq="10" file="detector_output.xml"/>\n')
        f.write('</additional>\n')
    print("Detectors file updated with chargers.")

def initialize_simulation(step_length):
    """Initializes the SUMO simulation with specified step length."""
    sumo_binary = sumolib.checkBinary('sumo-gui')  # Use 'sumo' for non-GUI
    sumo_cmd = [
        sumo_binary,
        "-c", SUMO_CONFIG,
        "--start",
        "--quit-on-end",
        "--step-length", str(step_length)
    ]
    traci.start(sumo_cmd)

def spawn_taxis(net, num_taxis=3):
    """Spawns taxis at valid edges."""
    valid_edges = [edge.getID() for edge in net.getEdges() if edge.getLaneNumber() > 0]
    taxi_ids = []
    for i in range(num_taxis):
        taxi_id = f"taxi_{i}"
        start_edge = random.choice(valid_edges)
        traci.route.add(f"route_{taxi_id}", [start_edge])
        traci.vehicle.add(taxi_id, routeID=f"route_{taxi_id}", typeID="taxi")
        print(f"Spawned taxi {taxi_id} at edge {start_edge}")
        taxi_ids.append(taxi_id)
    return taxi_ids

def monitor_pickups_and_dropoffs(assignments):
    """Monitors and logs pickups and drop-offs."""
    for taxi_id, reservation in list(assignments.items()):
        person_id = reservation.persons[0]
        if person_id in traci.person.getIDList():
            current_vehicle = traci.person.getVehicle(person_id)
            if current_vehicle == taxi_id:
                print(f"Person {person_id} is inside taxi {taxi_id}.")
            else:
                print(f"Person {person_id} is waiting for taxi {taxi_id}.")
        else:
            print(f"Person {person_id} has been dropped off by taxi {taxi_id}.")
            assignments.pop(taxi_id)

def assign_taxis_to_reservations(taxi_ids, assignments, invalid_taxis):
    """Assigns taxis to available taxi reservations."""
    reservations = traci.person.getTaxiReservations(0)
    for reservation in reservations:
        if reservation.id not in [res.id for res in assignments.values()]:
            for taxi_id in taxi_ids:
                if taxi_id not in assignments and taxi_id not in invalid_taxis:
                    try:
                        traci.vehicle.dispatchTaxi(taxi_id, reservation.id)
                        assignments[taxi_id] = reservation
                        print(f"Dispatched {taxi_id} to reservation {reservation.id}")
                    except traci.exceptions.TraCIException as e:
                        print(f"Skipping {taxi_id} due to route error: {e}")
                        invalid_taxis.add(taxi_id)
                    break
    return assignments

# =========================
# Simulation Runner
# =========================

def run_simulation():
    """Runs the SUMO simulation using TraCI."""
    global simulation_running, simulation_parameters, cumulative_consumption, previous_consumption
    try:
        # Initialize SUMO simulation
        sumo_cfg = SUMO_CONFIG
        step_length = simulation_parameters['time_step']
        sim_length = simulation_parameters['sim_length']
        num_people = simulation_parameters['num_people']
        num_taxis = simulation_parameters['num_cars']
        num_chargers = simulation_parameters['num_chargers']

        print(f"Starting simulation with parameters: {simulation_parameters}")

        # Read network
        net = sumolib.net.readNet(NETWORK_FILE)

        # Set up chargers
        if num_chargers > 0:
            chargers = generate_valid_charger_locations(net, num_chargers)
            chargers = validate_chargers_against_network(net, chargers)
            write_detectors_file(chargers)
        else:
            print("No chargers to set up.")

        # Generate persons
        num_spawned_people = generate_persons_xml(net, num_people=num_people)

        # Initialize simulation
        initialize_simulation(step_length)
        simulation_running = True

        # Spawn taxis
        taxi_ids = spawn_taxis(net, num_taxis=num_taxis)

        print("\nStarting simulation...")

        assignments = {}
        invalid_taxis = set()
        timestep = 0

        while timestep * step_length < sim_length and simulation_running:
            with lock:
                traci.simulationStep()
                timestep += 1

                # Dynamically adjust the number of taxis based on updated parameters
                current_num_cars = simulation_parameters['num_cars']
                if len(taxi_ids) < current_num_cars:
                    # Spawn additional taxis
                    additional_taxis = spawn_taxis(net, num_taxis=(current_num_cars - len(taxi_ids)))
                    taxi_ids.extend(additional_taxis)
                    print(f"Added {len(additional_taxis)} taxis to match the updated num_cars.")
                elif len(taxi_ids) > current_num_cars:
                    # Remove excess taxis
                    excess = len(taxi_ids) - current_num_cars
                    for _ in range(excess):
                        taxi_id = taxi_ids.pop()
                        traci.vehicle.remove(taxi_id)
                        print(f"Removed taxi {taxi_id} to match the updated num_cars.")

                # Assign taxis to reservations
                assignments = assign_taxis_to_reservations(taxi_ids, assignments, invalid_taxis)

                # Monitor pickups and drop-offs
                monitor_pickups_and_dropoffs(assignments)

                # Update cumulative energy consumption
                with cumulative_consumption_lock, previous_consumption_lock:
                    for vid in traci.vehicle.getIDList():
                        try:
                            current_consumption = traci.vehicle.getElectricityConsumption(vid)
                            previous = previous_consumption.get(vid, 0.0)
                            delta = current_consumption - previous
                            if delta < 0:
                                # Handle potential reset or error in energy consumption
                                delta = 0.0
                            cumulative_consumption[vid] = cumulative_consumption.get(vid, 0.0) + delta
                            previous_consumption[vid] = current_consumption
                        except traci.exceptions.TraCIException:
                            # Handle non-electric vehicles or errors
                            pass

                # Optional: Write energy output periodically
                # if timestep % 50 == 0:
                #     write_energy_output(timestep, taxi_ids, cumulative_consumption)
            time.sleep(0.01)  # Adjust sleep to control simulation speed

    except Exception as e:
        print(f"Simulation encountered an error: {e}")
    finally:
        if traci.isLoaded():
            traci.close()
        simulation_running = False
        print("Simulation ended.")

# =========================
# API Endpoints
# =========================

@app.route('/api/start_simulation', methods=['POST'])
def start_simulation():
    """Starts the SUMO simulation with provided parameters."""
    global simulation_thread, simulation_running

    if simulation_running:
        return jsonify({"status": "Simulation already running"}), 400

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No input data provided'}), 400

    # Extract and validate parameters
    num_cars = data.get('num_cars')
    num_chargers = data.get('num_chargers')
    num_people = data.get('num_people')
    time_step = data.get('time_step')
    sim_length = data.get('sim_length')  

    if not isinstance(num_cars, int) or num_cars < 1:
        return jsonify({'error': 'Invalid value for num_cars'}), 400
    if not isinstance(num_chargers, int) or num_chargers < 0:
        return jsonify({'error': 'Invalid value for num_chargers'}), 400
    if not isinstance(num_people, int) or num_people < 1:
        return jsonify({'error': 'Invalid value for num_people'}), 400
    if not isinstance(time_step, (int, float)) or time_step <= 0:
        return jsonify({'error': 'Invalid value for time_step'}), 400
    if not isinstance(sim_length, (int, float)) or sim_length <= 0:
        return jsonify({'error': 'Invalid value for sim_length'}), 400

    # Update simulation parameters
    simulation_parameters['num_cars'] = num_cars
    simulation_parameters['num_chargers'] = num_chargers
    simulation_parameters['num_people'] = num_people
    simulation_parameters['time_step'] = time_step
    simulation_parameters['sim_length'] = sim_length

    # Initialize cumulative_consumption and previous_consumption
    global cumulative_consumption, previous_consumption
    with cumulative_consumption_lock, previous_consumption_lock:
        cumulative_consumption = {}
        previous_consumption = {}

    # Start simulation in a new thread
    simulation_thread = threading.Thread(target=run_simulation)
    simulation_thread.start()
    return jsonify({"status": "Simulation started"}), 200

@app.route('/api/stop_simulation', methods=['POST'])
def stop_simulation():
    """Stops the ongoing SUMO simulation."""
    global simulation_running
    if not simulation_running:
        return jsonify({"status": "No simulation running"}), 400
    simulation_running = False
    if simulation_thread.is_alive():
        simulation_thread.join()
    return jsonify({"status": "Simulation stopped"}), 200

@app.route('/api/status', methods=['GET'])
def get_status():
    """Retrieves the current status of the simulation."""
    if not simulation_running:
        return jsonify({"status": "Simulation not running"}), 200
    try:
        step = traci.simulation.getCurrentTime() / 1000  # Convert ms to seconds
        return jsonify({"status": "Simulation running", "current_time": step}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/vehicles', methods=['GET'])
def get_vehicles():
    """Fetches energy consumption information about all active vehicles in the simulation."""
    if not simulation_running:
        return jsonify({"error": "Simulation not running"}), 400
    try:
        vehicle_ids = traci.vehicle.getIDList()
        vehicles = {}
        with cumulative_consumption_lock:
            for vid in vehicle_ids:
                energy = cumulative_consumption.get(vid, 0.0)
                vehicles[vid] = {"energy_consumption_kJ": round(energy, 2)}
        return jsonify({"vehicles": vehicles}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/add_vehicle', methods=['POST'])
def add_vehicle():
    """Adds a new vehicle to a randomly selected existing route."""
    if not simulation_running:
        return jsonify({"error": "Simulation not running"}), 400
    try:
        # Generate a unique vehicle ID
        vehicle_id = generate_vehicle_id()
        
        # Retrieve the list of existing routes
        routes = traci.route.getIDList()
        if not routes:
            return jsonify({"error": "No routes available in the simulation"}), 400
        
        # Select a random route
        selected_route = random.choice(routes)
        
        # Add the vehicle to the selected route
        traci.vehicle.add(vehID=vehicle_id, routeID=selected_route, typeID="taxi")
        
        return jsonify({
            "status": f"Vehicle {vehicle_id} added to route {selected_route}",
            "vehicle_id": vehicle_id,
            "route_id": selected_route
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/change_parameters', methods=['POST'])
def change_parameters():
    """Updates simulation parameters. Changes take effect during the simulation."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No input data provided'}), 400

    num_cars = data.get('num_cars')
    num_chargers = data.get('num_chargers')
    num_people = data.get('num_people')
    time_step = data.get('time_step')
    sim_length = data.get('sim_length')  

    # Validate parameters
    if not isinstance(num_cars, int) or num_cars < 1:
        return jsonify({'error': 'Invalid value for num_cars'}), 400
    if not isinstance(num_chargers, int) or num_chargers < 0:
        return jsonify({'error': 'Invalid value for num_chargers'}), 400
    if not isinstance(num_people, int) or num_people < 1:
        return jsonify({'error': 'Invalid value for num_people'}), 400
    if not isinstance(time_step, (int, float)) or time_step <= 0:
        return jsonify({'error': 'Invalid value for time_step'}), 400
    if not isinstance(sim_length, (int, float)) or sim_length <= 0:
        return jsonify({'error': 'Invalid value for sim_length'}), 400

    # Update simulation parameters
    simulation_parameters['num_cars'] = num_cars
    simulation_parameters['num_chargers'] = num_chargers
    simulation_parameters['num_people'] = num_people
    simulation_parameters['time_step'] = time_step
    simulation_parameters['sim_length'] = sim_length

    return jsonify({
        'status': 'Parameters changed successfully',
        'num_cars': num_cars,
        'num_chargers': num_chargers,
        'num_people': num_people,
        'time_step': time_step,
        'sim_length': sim_length
    }), 200

# =========================
# Main Entry Point
# =========================

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
