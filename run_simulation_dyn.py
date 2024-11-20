import os
import sys
import traci
import sumolib
import random
import argparse
from threading import Thread, Event
from queue import Queue

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

def initialize_simulation(step_length):
    """Initializes the SUMO simulation."""
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

def spawn_taxis_at_runtime(net, num_taxis, taxi_ids):
    """Spawns additional taxis during simulation runtime."""
    valid_edges = [edge.getID() for edge in net.getEdges() if edge.getLaneNumber() > 0]
    new_taxis = []

    for i in range(num_taxis):
        taxi_id = f"runtime_taxi_{len(taxi_ids) + i}"
        start_edge = random.choice(valid_edges)
        traci.route.add(f"route_{taxi_id}", [start_edge])
        traci.vehicle.add(taxi_id, routeID=f"route_{taxi_id}", typeID="taxi")
        print(f"Spawned additional taxi {taxi_id} at edge {start_edge}")
        new_taxis.append(taxi_id)

    return new_taxis

def mock_communication_listener(add_taxi_queue, stop_event, trigger_timestep, current_timestep_func, num_extra_taxis):
    """Mocks communication with a frontend app, signaling additional taxis to be added."""
    while not stop_event.is_set():
        if current_timestep_func() == trigger_timestep:
            add_taxi_queue.put(num_extra_taxis)
            print(f"Trigger sent to add {num_extra_taxis} taxis at timestep {trigger_timestep}.")
            stop_event.set()  # Stop the listener after sending the trigger


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

def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="SUMO simulation with configurable parameters.")
    parser.add_argument("--num-people", type=int, default=3, help="Number of people in the simulation.")
    parser.add_argument("--num-taxis", type=int, default=3, help="Number of taxis in the simulation.")
    parser.add_argument("--step-length", type=float, default=1.0, help="Step length for the simulation in seconds.")
    parser.add_argument("--sim-length", type=int, default=1000, help="Total simulation length in seconds.")
    args = parser.parse_args()

    num_people = args.num_people
    num_taxis = args.num_taxis
    step_length = args.step_length
    sim_length = args.sim_length

    print(f"Simulation Parameters: num_people={num_people}, num_taxis={num_taxis}, step_length={step_length}, sim_length={sim_length}")

    net = sumolib.net.readNet(network_file)
    num_spawned_people = generate_persons_xml(net, num_people=num_people)
    initialize_simulation(step_length)
    taxi_ids = spawn_taxis(net, num_taxis=num_taxis)

    print("\nStarting simulation...")

    # Mock frontend communication setup
    trigger_timestep = 150  # Hidden hardcoded trigger timestep
    num_extra_taxis = 5     # Number of taxis to add dynamically
    add_taxi_queue = Queue()
    stop_event = Event()
    
    # Function to get the current simulation timestep
    current_timestep_func = lambda: traci.simulation.getTime()

    communication_thread = Thread(
        target=mock_communication_listener,
        args=(add_taxi_queue, stop_event, trigger_timestep, current_timestep_func, num_extra_taxis),
    )
    communication_thread.start()

    assignments = {}
    invalid_taxis = set()
    previous_consumption = {}
    timestep = 0

    try:
        while timestep * step_length < sim_length:
            traci.simulationStep()
            timestep += 1

            # Handle runtime addition of taxis
            while not add_taxi_queue.empty():
                num_extra_taxis = add_taxi_queue.get()
                print(f"Adding {num_extra_taxis} taxis at timestep {timestep}.")
                new_taxis = spawn_taxis_at_runtime(net, num_extra_taxis, taxi_ids)
                taxi_ids.extend(new_taxis)

                # Advance the simulation to ensure the taxis are registered
                traci.simulationStep()
                
                # Count the number of confirmed taxis in the simulation
                confirmed_taxis = len(traci.vehicle.getIDList())
                print(
                    f"{len(new_taxis)} taxis successfully added. "
                    f"Pausing simulation. {confirmed_taxis} taxis are now confirmed in the simulation."
                )
                input("Press Enter to resume the simulation...")


            assignments = assign_taxis_to_reservations(taxi_ids, assignments, invalid_taxis)
            monitor_pickups_and_dropoffs(assignments)
            if timestep % 50 == 0:
                write_energy_output(timestep, taxi_ids, previous_consumption)

    except traci.exceptions.TraCIException as e:
        print("TraCI encountered an error:", e)
    finally:
        traci.close()
        stop_event.set()
        communication_thread.join()
        print("Simulation ended.")


if __name__ == "__main__":
    main()
