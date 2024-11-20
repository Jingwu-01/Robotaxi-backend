import os
import sys
import traci
import sumolib
import random
import argparse

# Ensure SUMO_HOME is set
if 'SUMO_HOME' in os.environ:
    tools = os.path.join(os.environ['SUMO_HOME'], 'tools')
    sys.path.append(tools)
else:
    sys.exit("Please set the 'SUMO_HOME' environment variable.")

network_file = "downtown_houston.net.xml"
sumo_cfg = "simulation2.sumocfg"

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
    sumo_binary = sumolib.checkBinary('sumo-gui')
    sumo_cmd = [
        sumo_binary,
        "-c", sumo_cfg,
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

def write_energy_output(timestep, taxi_ids, previous_consumption):
    """Outputs XML data for each taxi's incremental electricity consumption."""
    output_file = f"energy_output_{timestep:04d}.xml"
    with open(output_file, 'w') as f:
        f.write('<energyData>\n')
        for taxi_id in taxi_ids:
            try:
                current_consumption = traci.vehicle.getElectricityConsumption(taxi_id)
                incremental_usage = max(current_consumption - previous_consumption.get(taxi_id, 0), 0)
                previous_consumption[taxi_id] = current_consumption
                f.write(f'  <taxi id="{taxi_id}" incrementalUsage="{incremental_usage:.3f}" />\n')
            except traci.exceptions.TraCIException:
                f.write(f'  <taxi id="{taxi_id}" incrementalUsage="0.000" />\n')
        f.write('</energyData>\n')
    print(f"Energy data written to {output_file}")

def assign_taxis_to_reservations(taxi_ids, assignments, invalid_taxis):
    """Assigns taxis to available taxi reservations."""
    reservations = traci.person.getTaxiReservations(0)
    for reservation in reservations:
        if reservation.id not in [res.id for res in assignments.values()]:
            for taxi_id in taxi_ids:
                if taxi_id not in assignments:
                    try:
                        traci.vehicle.dispatchTaxi(taxi_id, reservation.id)
                        assignments[taxi_id] = reservation
                        print(f"Dispatched {taxi_id} to reservation {reservation.id}")
                    except traci.exceptions.TraCIException as e:
                        print(f"Skipping {taxi_id} due to route error: {e}")
                        invalid_taxis.add(taxi_id)
                    break
    return assignments

def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="SUMO simulation with configurable parameters.")
    parser.add_argument("--num-people", type=int, default=3, help="Number of people in the simulation.")
    parser.add_argument("--num-taxis", type=int, default=3, help="Number of taxis in the simulation.")
    parser.add_argument("--num-chargers", type=int, help="Number of chargers in the simulation.")
    parser.add_argument("--charger-coords-file", type=str, help="File specifying charger locations (lane_id, position).")
    parser.add_argument("--step-length", type=float, default=1.0, help="Step length for the simulation in seconds.")
    parser.add_argument("--sim-length", type=int, default=1000, help="Total simulation length in seconds.")
    args = parser.parse_args()

    # Check if both options are provided explicitly in the command line
    if args.num_chargers is not None and args.charger_coords_file is not None:
        print("Error: You cannot specify both --num-chargers and --charger-coords-file.")
        sys.exit(1)

    num_people = args.num_people
    num_taxis = args.num_taxis
    step_length = args.step_length
    sim_length = args.sim_length

    print(f"Simulation Parameters: num_people={num_people}, num_taxis={num_taxis}, step_length={step_length}, sim_length={sim_length}")

    net = sumolib.net.readNet(network_file)

    # Only set chargers if one of the options is explicitly provided
    if args.charger_coords_file:
        chargers = parse_charger_coords_file(args.charger_coords_file)
    elif args.num_chargers is not None:
        chargers = generate_valid_charger_locations(net, args.num_chargers)
    else:
        chargers = []  # Default to no chargers if none are specified

    chargers = validate_chargers_against_network(net, chargers)
    write_detectors_file(chargers)

    num_spawned_people = generate_persons_xml(net, num_people=num_people)
    initialize_simulation(step_length)
    taxi_ids = spawn_taxis(net, num_taxis=num_taxis)

    print("\nStarting simulation...")

    assignments = {}
    invalid_taxis = set()
    previous_consumption = {}
    timestep = 0

    try:
        while timestep * step_length < sim_length:
            traci.simulationStep()
            timestep += 1
            assignments = assign_taxis_to_reservations(taxi_ids, assignments, invalid_taxis)
            monitor_pickups_and_dropoffs(assignments)
            if timestep % 50 == 0:
                write_energy_output(timestep, taxi_ids, previous_consumption)
    except traci.exceptions.TraCIException as e:
        print("TraCI encountered an error:", e)
    finally:
        traci.close()
        print("Simulation ended.")

if __name__ == "__main__":
    main()